import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.utils import timezone


class UserQuerySet(models.QuerySet):
    def pending_deletion(self):
        """Users that have been scheduled for deletion"""
        return self.filter(scheduled_deletion_at__isnull=False, is_active=False)

    def due_for_deletion(self, now=None):
        """Users whose scheduled deletion time has passed"""
        now = now or timezone.now()
        return self.pending_deletion().filter(scheduled_deletion_at__lte=now)

    def delete(self):
        """Schedule users for deletion rather than deleting them

        Covers bulk deletes such as the admin "delete selected" action. Users
        already pending keep their existing date. Nothing is removed from the
        database, so the deleted count is always 0"""
        self.filter(scheduled_deletion_at__isnull=True).update(
            is_active=False,
            scheduled_deletion_at=(
                timezone.now() + settings.ACCOUNT_DELETION_GRACE_PERIOD
            ),
        )
        # Users deactivated with a stale date set are pending again
        self.filter(is_active=True).update(is_active=False)
        return 0, {}

    delete.alters_data = True
    delete.queryset_only = True

    def _hard_delete(self):
        """Permanently delete users that are pending deletion

        Users not pending deletion are left untouched"""
        return super(UserQuerySet, self.pending_deletion()).delete()

    _hard_delete.alters_data = True
    _hard_delete.queryset_only = True


class CustomUserManager(UserManager.from_queryset(UserQuerySet)):
    pass


class Theme(models.TextChoices):
    # Whatever the device is set to
    AUTO = "auto", "Same as my device"
    LIGHT = "light", "Light"
    DARK = "dark", "Dark"


class UIMode(models.TextChoices):
    """How much of the app is shown. Simple is the default, and switching
    back to it is always one click away"""

    SIMPLE = "simple", "Simple"
    ADVANCED = "advanced", "Advanced"


class User(AbstractUser):
    """Customer user model

    Username is the login identifier as email may change in the future"""

    display_name = models.CharField(
        # Names can be long, but let's be reasonable here, even
        # Hubert Blaine Wolfeschlegelsteinhausenbergerdorff Sr. made his shorter
        # 150 should be ample
        max_length=150,
        blank=True,
        help_text="Display name on site",
    )

    theme = models.CharField(
        max_length=8, choices=Theme.choices, default=Theme.AUTO
    )
    ui_mode = models.CharField(
        "view",
        max_length=8,
        choices=UIMode.choices,
        default=UIMode.SIMPLE,
        help_text="Advanced shows every field and more ways to search",
    )

    # Used for lazy deletion
    scheduled_deletion_at = models.DateTimeField(null=True, blank=True)

    objects = CustomUserManager()

    @property
    def is_pending_deletion(self) -> bool:
        """Is the user pending deletion?"""
        return self.scheduled_deletion_at is not None and not self.is_active

    def schedule_deletion(self, grace_period: timedelta | None = None) -> None:
        """Deactivate the user and schedule them for deletion

        The account is removed by the purge task once the grace period passes"""
        if grace_period is None:
            grace_period = settings.ACCOUNT_DELETION_GRACE_PERIOD
        self.is_active = False
        self.scheduled_deletion_at = timezone.now() + grace_period
        self.save(update_fields=["is_active", "scheduled_deletion_at"])

    def delete(self, using=None, keep_parents=False):
        """Schedule the user for deletion rather than deleting them

        Stops an accidental delete() from removing an account outright. A user
        already pending deletion keeps their existing date. Nothing is removed
        from the database, so the deleted count is always 0"""
        if not self.is_pending_deletion:
            self.schedule_deletion()
        return 0, {}

    def _hard_delete(self, using=None, keep_parents=False):
        """Permanently delete the user

        Only users pending deletion can be removed, schedule them first"""
        if not self.is_pending_deletion:
            raise ValueError(
                f"User {self.pk} is not pending deletion, "
                "call schedule_deletion() first"
            )
        return super().delete(using=using, keep_parents=keep_parents)

    def cancel_deletion(self) -> None:
        """Reactivate a user that was pending deletion"""
        self.is_active = True
        self.scheduled_deletion_at = None
        self.save(update_fields=["is_active", "scheduled_deletion_at"])


# The part of a key that says what it is, so a leaked one is easy to spot
API_KEY_PREFIX = "qsk"


def _hash_secret(secret: str) -> str:
    # Keys are long and random, so a plain hash is enough, unlike passwords
    return hashlib.sha256(secret.encode()).hexdigest()


class ApiKeyQuerySet(models.QuerySet):
    def usable(self, now=None):
        now = now or timezone.now()
        return self.filter(revoked_at__isnull=True).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        )


class ApiKey(models.Model):
    """A key for the API, a user can have any number, each named for what
    uses it so it can be revoked on its own

    Only a hash of the secret is kept, the key is shown once when it's made.
    A key is qsk_<lookup>_<secret>, the lookup finds the row without the
    secret. scopes is for limiting what a key can do, empty is everything
    the user can do; nothing checks it yet"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_keys",
    )
    name = models.CharField(max_length=100, help_text="What uses this key")
    lookup = models.CharField(max_length=12, unique=True, editable=False)
    secret_hash = models.CharField(max_length=64, editable=False)
    scopes = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    objects = ApiKeyQuerySet.as_manager()

    class Meta:
        ordering = ("user", "-created_at")
        verbose_name = "API key"

    def __str__(self) -> str:
        return f"{self.name} ({API_KEY_PREFIX}_{self.lookup}_…)"

    @classmethod
    def issue(cls, user, name: str, expires_at=None) -> tuple[ApiKey, str]:
        """Make a key, returning it and the full key, which is never
        available again"""
        lookup = secrets.token_hex(6)
        secret = secrets.token_urlsafe(32)
        key = cls.objects.create(
            user=user,
            name=name,
            lookup=lookup,
            secret_hash=_hash_secret(secret),
            expires_at=expires_at,
        )
        return key, f"{API_KEY_PREFIX}_{lookup}_{secret}"

    @classmethod
    def authenticate(cls, token: str):
        """The user a full key belongs to, or None"""
        prefix, _, rest = token.partition("_")
        lookup, _, secret = rest.partition("_")
        if prefix != API_KEY_PREFIX or not lookup or not secret:
            return None
        key = (
            cls.objects.usable()
            .select_related("user")
            .filter(lookup=lookup)
            .first()
        )
        if key is None or not secrets.compare_digest(
            key.secret_hash, _hash_secret(secret)
        ):
            return None
        if not key.user.is_active:
            return None
        now = timezone.now()
        # At most once a minute, so busy keys don't write on every request
        if key.last_used_at is None or key.last_used_at < now - timedelta(
            minutes=1
        ):
            ApiKey.objects.filter(pk=key.pk).update(last_used_at=now)
        return key.user

    @property
    def is_usable(self) -> bool:
        now = timezone.now()
        return self.revoked_at is None and (
            self.expires_at is None or self.expires_at > now
        )

    def revoke(self) -> None:
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.save(update_fields=["revoked_at"])
