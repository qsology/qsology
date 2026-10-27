"""The callsigns you hold"""

from datetime import date, datetime

from apps.callsigns import services
from apps.callsigns.forms import AddCallsignForm, ChangeCallsignForm
from apps.callsigns.models import CallsignAllocation
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Router, Schema, Status

router = Router(tags=["callsigns"])


class Allocation(Schema):
    id: int
    call: str
    valid_from: datetime
    valid_until: datetime | None
    is_primary: bool
    is_current: bool


class NewAllocation(Schema):
    call: str
    # Days run from midnight UTC. Today if not given
    valid_from: date | None = None
    valid_until: date | None = None
    is_primary: bool = False


class AllocationChanges(Schema):
    valid_until: date | None
    is_primary: bool


def _out(allocation) -> Allocation:
    return Allocation(
        id=allocation.pk,
        call=allocation.callsign.call,
        valid_from=allocation.valid_from,
        valid_until=allocation.valid_until,
        is_primary=allocation.is_primary,
        is_current=allocation.is_active(),
    )


def _own(request, allocation_id):
    return get_object_or_404(
        CallsignAllocation.objects.select_related("callsign"),
        pk=allocation_id,
        user=request.user,
    )


@router.get("", response=list[Allocation], url_name="callsign-list")
def callsign_list(request):
    """Every callsign you hold, have held or will hold"""
    allocations = (
        CallsignAllocation.objects.filter(user=request.user)
        .select_related("callsign")
        .order_by("-valid_from")
    )
    return [_out(allocation) for allocation in allocations]


@router.post("", response={201: Allocation}, url_name="callsign-create")
def callsign_create(request, payload: NewAllocation):
    """Add a callsign you hold, without any prefix or suffix"""
    form = AddCallsignForm(
        {
            "call": payload.call,
            "valid_from": payload.valid_from or timezone.now().date(),
            "valid_until": payload.valid_until or "",
            "is_primary": payload.is_primary,
        }
    )
    if not form.is_valid():
        raise ValidationError(form.errors.as_data())
    return Status(201, _out(services.add_callsign(request.user, **form.values)))


@router.get("/{allocation_id}", response=Allocation, url_name="callsign-get")
def callsign_get(request, allocation_id: int):
    return _out(_own(request, allocation_id))


@router.patch(
    "/{allocation_id}", response=Allocation, url_name="callsign-update"
)
def callsign_update(request, allocation_id: int, payload: AllocationChanges):
    """Change when you hold it until, or make it your main callsign"""
    allocation = _own(request, allocation_id)
    form = ChangeCallsignForm(
        {
            "valid_until": payload.valid_until or "",
            "is_primary": payload.is_primary,
        }
    )
    if not form.is_valid():
        raise ValidationError(form.errors.as_data())
    return _out(services.change_callsign(allocation, **form.values))


@router.post(
    "/{allocation_id}/end", response=Allocation, url_name="callsign-end"
)
def callsign_end(request, allocation_id: int):
    """You no longer hold the callsign, from now"""
    allocation = _own(request, allocation_id)
    if allocation.is_active():
        allocation.end()
    return _out(allocation)
