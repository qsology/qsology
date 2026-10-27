"""Adding and changing the callsigns a user holds, for the pages and the
API alike

TODO: Nothing checks a callsign is really the user's yet. Anyone can add
one nobody else holds at the time"""

from datetime import datetime

from apps.callsigns.models import (
    Callsign,
    CallsignAllocation,
    normalize_callsign,
    parse_callsign,
)
from django.core.exceptions import ValidationError
from django.db import transaction


def add_callsign(
    user,
    call: str,
    valid_from: datetime,
    valid_until: datetime | None = None,
    is_primary: bool = False,
) -> CallsignAllocation:
    """Record that the user holds a callsign from valid_from, until
    valid_until or for good

    Raises ValidationError if it's not a base callsign or someone holds it
    then. Made primary, it takes over from any other primary callsign the
    user has at the same time"""
    try:
        parsed = parse_callsign(call)
    except ValueError as exc:
        raise ValidationError({"call": "That isn't a callsign"}) from exc
    if parsed.prefix or parsed.suffix:
        raise ValidationError(
            {
                "call": (
                    f"Add the callsign without a prefix or suffix, e.g. "
                    f"{parsed.call}. They're added when you log with them"
                )
            }
        )
    with transaction.atomic():
        callsign = Callsign.objects.filter(
            call=normalize_callsign(parsed.call)
        ).first()
        if callsign is None:
            callsign = Callsign(call=parsed.call)
            callsign.save()
        allocation = CallsignAllocation(
            callsign=callsign,
            user=user,
            valid_from=valid_from,
            valid_until=valid_until,
        )
        allocation.save()
        if is_primary:
            allocation.make_primary()
    return allocation


def change_callsign(
    allocation: CallsignAllocation,
    *,
    valid_until: datetime | None,
    is_primary: bool,
) -> CallsignAllocation:
    with transaction.atomic():
        allocation.valid_until = valid_until
        if not is_primary:
            allocation.is_primary = False
        allocation.save()
        if is_primary and not allocation.is_primary:
            allocation.make_primary()
    return allocation


def primary_call(user, at: datetime | None = None) -> str:
    """The user's primary callsign at the time, default now, or the only
    one they hold, or nothing"""
    allocations = list(
        CallsignAllocation.objects.active(at)
        .filter(user=user)
        .select_related("callsign")
    )
    for allocation in allocations:
        if allocation.is_primary:
            return allocation.callsign.call
    if len(allocations) == 1:
        return allocations[0].callsign.call
    return ""
