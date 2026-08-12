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
