from datetime import datetime
from typing import NamedTuple

from apps.callsigns import roles
from apps.callsigns.roles import CallsignRole
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, transaction
from django.db.models import F, Q
from django.db.models.functions import Upper
from django.utils import timezone

# A base callsign always has at least one letter and one digit, e.g. M0WME
validate_base_callsign = RegexValidator(
    r"^(?=.*[A-Z])(?=.*[0-9])[A-Z0-9]+$",
    "Callsigns must be letters and numbers, with at least one of each",
)
# Prefixes and suffixes are letters and/or numbers, e.g. ZL, VK9, P, MM
validate_callsign_part = RegexValidator(
    r"^[A-Z0-9]*$", "Prefixes and suffixes must be letters and numbers"
)


class CommonSuffix(models.TextChoices):
    """Suffixes offered to users, any other suffix can still be typed in"""

    PORTABLE = "P", "Portable - /P"
    MOBILE = "M", "Mobile - /M"
    AERONAUTICAL_MOBILE = "AM", "Aeronautical Mobile - /AM"
    MARITIME_MOBILE = "MM", "Maritime Mobile - /MM"


class ParsedCallsign(NamedTuple):
    prefix: str
    call: str
    suffix: str


def normalize_callsign(value: str) -> str:
    """Uppercase a callsign and strip any whitespace"""
    return "".join(value.split()).upper()


def normalize_callsign_part(value: str) -> str:
    """Normalize a prefix or suffix, dropping any "/" around it"""
    return normalize_callsign(value).strip("/")


def parse_callsign(value: str) -> ParsedCallsign:
    """Split a callsign such as ZL/M0WME/P into prefix, call and suffix

    With only two parts the longer one is taken as the call, so ZL/M0WME has a
    prefix and M0WME/P or W1AW/VE3 have a suffix. A tie is read as a suffix"""
    parts = normalize_callsign(value).split("/")
    if not all(parts) or len(parts) > 3:
        raise ValueError(f"{value!r} is not a valid callsign")
    if len(parts) == 1:
        return ParsedCallsign("", parts[0], "")
    if len(parts) == 3:
        return ParsedCallsign(*parts)
    first, second = parts
    if len(second) > len(first):
        return ParsedCallsign(first, second, "")
    return ParsedCallsign("", first, second)


def format_callsign(prefix: str, call: str, suffix: str) -> str:
    """Join the parts of a callsign back together, e.g. ZL/M0WME/P"""
    return "/".join(part for part in (prefix, call, suffix) if part)


class CallsignQuerySet(models.QuerySet):
    def held_by(self, user, at: datetime | None = None):
        """Callsigns allocated to the user at the given time, default now"""
        allocations = CallsignAllocation.objects.active(at).filter(user=user)
        return self.filter(allocations__in=allocations).distinct()


class Callsign(models.Model):
    """A base callsign, e.g. M0WME

    Kept separate from users as a callsign can move between users over time,
    who holds it and when is recorded by CallsignAllocation"""

    call = models.CharField(
        max_length=16,
        unique=True,
        validators=[validate_base_callsign],
        help_text="Base callsign without any prefix or suffix",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = CallsignQuerySet.as_manager()

    class Meta:
        ordering = ("call",)
        constraints = (
            models.CheckConstraint(
                condition=Q(call=Upper("call")),
                name="callsign_call_uppercase",
            ),
        )

    def __str__(self) -> str:
        return self.call

    def save(self, *args, **kwargs):
        self.call = normalize_callsign(self.call)
        self.full_clean()
        super().save(*args, **kwargs)

    def allocation_at(self, at: datetime | None = None):
        """The allocation covering the given time, default now"""
        return self.allocations.active(at).select_related("user").first()

    def holder_at(self, at: datetime | None = None):
        """The user holding this callsign at the given time, default now"""
        allocation = self.allocation_at(at)
        return allocation.user if allocation else None

    def role_for(self, user, at: datetime | None = None) -> str | None:
        """The user's role on this callsign at the given time, default now"""
        allocation = self.allocation_at(at)
        return allocation.role_for(user) if allocation else None

    def get_variant(
        self,
        prefix: str = "",
        suffix: str = "",
        is_reciprocal: bool | None = None,
    ):
        """Get or create a variant of this callsign, e.g. ZL/M0WME/P

        A prefix is assumed to be reciprocal operation unless told otherwise"""
        prefix = normalize_callsign_part(prefix)
        suffix = normalize_callsign_part(suffix)
        if is_reciprocal is None:
            is_reciprocal = bool(prefix)
        variant, _ = self.variants.get_or_create(
            prefix=prefix,
            suffix=suffix,
            defaults={"is_reciprocal": is_reciprocal},
        )
        return variant


class CallsignAllocationQuerySet(models.QuerySet):
    def active(self, at: datetime | None = None):
        """Allocations valid at the given time, default now"""
        at = at or timezone.now()
        return self.filter(
            Q(valid_until__isnull=True) | Q(valid_until__gt=at),
            valid_from__lte=at,
        )

    def overlapping(self, start: datetime, end: datetime | None):
        """Allocations that overlap the period from start until end

        An end of None is an indefinite period"""
        queryset = self.filter(
            Q(valid_until__isnull=True) | Q(valid_until__gt=start)
        )
        if end is not None:
            queryset = queryset.filter(valid_from__lt=end)
        return queryset

    def primary(self):
        return self.filter(is_primary=True)

    def accessible_to(self, user, minimum: str = CallsignRole.VIEWER):
        """Allocations the user holds, or has been shared with at least the
        minimum role

        Data recorded against a callsign should only be visible to a user
        when it falls inside one of these allocations"""
        shared = Q(
            shares__user=user,
            shares__role__in=roles.roles_at_least(minimum),
        )
        return self.filter(Q(user=user) | shared).distinct()


class CallsignAllocation(models.Model):
    """A callsign held by a user for a period of time

    The period runs from valid_from up to, but not including, valid_until so
    one allocation can end at the moment the next starts. No valid_until means
    the callsign is held indefinitely. A callsign can only be held by one user
    at a time"""

    callsign = models.ForeignKey(
        Callsign, on_delete=models.CASCADE, related_name="allocations"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="callsign_allocations",
    )
    valid_from = models.DateTimeField(default=timezone.now)
    valid_until = models.DateTimeField(
        null=True, blank=True, help_text="Leave blank for indefinite"
    )
    is_primary = models.BooleanField(
        default=False, help_text="The user's main callsign"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = CallsignAllocationQuerySet.as_manager()

    class Meta:
        ordering = ("callsign", "valid_from")
        constraints = (
            models.CheckConstraint(
                condition=(
                    Q(valid_until__isnull=True)
                    | Q(valid_until__gt=F("valid_from"))
                ),
                name="callsign_allocation_valid_until_after_valid_from",
            ),
        )

    def __str__(self) -> str:
        until = self.valid_until.isoformat() if self.valid_until else "∞"
        return f"{self.callsign} ({self.valid_from.isoformat()} - {until})"

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.valid_from is None or self.callsign_id is None:
            return
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValidationError(
                {"valid_until": "Valid until must be after valid from"}
            )
        others = CallsignAllocation.objects.exclude(pk=self.pk).overlapping(
            self.valid_from, self.valid_until
        )
        if others.filter(callsign_id=self.callsign_id).exists():
            raise ValidationError(
                "This callsign is already allocated during this period"
            )
        if (
            self.is_primary
            and self.user_id is not None
            and others.primary().filter(user_id=self.user_id).exists()
        ):
            raise ValidationError(
                {"is_primary": "User already has a primary callsign then"}
            )

    @property
    def is_indefinite(self) -> bool:
        return self.valid_until is None

    def is_active(self, at: datetime | None = None) -> bool:
        """Is the allocation valid at the given time, default now?"""
        at = at or timezone.now()
        return self.valid_from <= at and (
            self.valid_until is None or self.valid_until > at
        )

    def make_primary(self) -> None:
        """Make this the user's primary callsign

        Any of the user's other primary allocations overlapping this one are
        made secondary"""
        with transaction.atomic():
            CallsignAllocation.objects.exclude(pk=self.pk).filter(
                user_id=self.user_id, is_primary=True
            ).overlapping(self.valid_from, self.valid_until).update(
                is_primary=False
            )
            self.is_primary = True
            self.save()

    def renew(self, valid_until: datetime | None) -> None:
        """Extend the allocation, None renews it indefinitely"""
        self.valid_until = valid_until
        self.save()

    def end(self, at: datetime | None = None) -> None:
        """End the allocation at the given time, default now"""
        self.valid_until = at or timezone.now()
        self.save()

    def role_for(self, user) -> str | None:
        """The user's role on the callsign during this allocation"""
        if user.pk == self.user_id:
            return CallsignRole.OWNER
        share = self.shares.filter(user=user).first()
        return share.role if share else None

    def share_with(self, user, role: str):
        """Share the callsign with another user, or change their role"""
        share, _ = CallsignShare.objects.update_or_create(
            allocation=self, user=user, defaults={"role": role}
        )
        return share


class CallsignVariantQuerySet(models.QuerySet):
    def for_callsign(self, value: str):
        """Every variant of a callsign, prefixed and suffixed or not

        Accepts a base callsign or any variant of it, e.g. ZL/M0WME/P finds
        M0WME, M0WME/P, ZL/M0WME and so on"""
        return self.filter(callsign__call=parse_callsign(value).call)

    def lookup(self, value: str):
        """The variant matching the callsign exactly"""
        return self.filter(full_call=normalize_callsign(value))

    def plain(self):
        """Variants with no prefix or suffix"""
        return self.filter(prefix="", suffix="")

    def with_prefix(self, prefix: str | None = None):
        """Variants with the given prefix, or any prefix if none is given"""
        if prefix is None:
            return self.exclude(prefix="")
        return self.filter(prefix=normalize_callsign_part(prefix))

    def with_suffix(self, suffix: str | None = None):
        """Variants with the given suffix, or any suffix if none is given"""
        if suffix is None:
            return self.exclude(suffix="")
        return self.filter(suffix=normalize_callsign_part(suffix))

    def reciprocal(self):
        return self.filter(is_reciprocal=True)


class CallsignVariant(models.Model):
    """A callsign as used on air, e.g. M0WME, M0WME/P, ZL/M0WME/P

    Variants don't expire, who they belong to follows the allocations of the
    base callsign"""

    callsign = models.ForeignKey(
        Callsign, on_delete=models.CASCADE, related_name="variants"
    )
    prefix = models.CharField(
        max_length=8,
        blank=True,
        validators=[validate_callsign_part],
        help_text="e.g. ZL when operating in New Zealand",
    )
    suffix = models.CharField(
        max_length=8,
        blank=True,
        validators=[validate_callsign_part],
        help_text="e.g. P for portable, any suffix is allowed",
    )
    # The full callsign, kept in step with the parts on save
    full_call = models.CharField(max_length=40, unique=True, editable=False)
    is_reciprocal = models.BooleanField(
        default=False,
        help_text="Used when operating from another country",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = CallsignVariantQuerySet.as_manager()

    class Meta:
        ordering = ("full_call",)
        constraints = (
            models.UniqueConstraint(
                fields=["callsign", "prefix", "suffix"],
                name="callsign_variant_unique_parts",
            ),
            models.CheckConstraint(
                condition=(
                    Q(prefix=Upper("prefix"))
                    & Q(suffix=Upper("suffix"))
                    & Q(full_call=Upper("full_call"))
                ),
                name="callsign_variant_uppercase",
            ),
        )

    def __str__(self) -> str:
        return self.full_call

    def save(self, *args, **kwargs):
        self.prefix = normalize_callsign_part(self.prefix)
        self.suffix = normalize_callsign_part(self.suffix)
        self.full_call = format_callsign(
            self.prefix, self.callsign.call, self.suffix
        )
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def is_plain(self) -> bool:
        return not self.prefix and not self.suffix

    @property
    def common_suffix(self) -> CommonSuffix | None:
        """The common suffix, if the suffix is one"""
        if self.suffix in CommonSuffix.values:
            return CommonSuffix(self.suffix)
        return None

    def holder_at(self, at: datetime | None = None):
        return self.callsign.holder_at(at)

    def role_for(self, user, at: datetime | None = None) -> str | None:
        return self.callsign.role_for(user, at)


class CallsignShare(models.Model):
    """A callsign shared with another user, e.g. a QSL manager

    Shares belong to an allocation, so they end with it and are never passed
    on to the next holder of the callsign"""

    allocation = models.ForeignKey(
        CallsignAllocation, on_delete=models.CASCADE, related_name="shares"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="callsign_shares",
    )
    role = models.CharField(
        max_length=16,
        choices=CallsignRole.choices,
        default=CallsignRole.VIEWER,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=["allocation", "user"],
                name="callsign_share_unique_user",
            ),
        )

    def __str__(self) -> str:
        callsign = self.allocation.callsign
        return f"{callsign} shared with {self.user} ({self.role})"

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.allocation_id and self.allocation.user_id == self.user_id:
            raise ValidationError(
                {"user": "A callsign can't be shared with its holder"}
            )
