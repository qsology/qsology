from apps.callsigns import services
from apps.callsigns.forms import AddCallsignForm, ChangeCallsignForm
from apps.callsigns.models import CallsignAllocation
from apps.web.forms import add_errors
from apps.web.parity import api
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST


def _allocations(user):
    return (
        CallsignAllocation.objects.filter(user=user)
        .select_related("callsign")
        .order_by("-valid_from")
    )


@require_http_methods(["GET", "POST"])
@login_required
@api("callsign-list", "callsign-create")
def callsign_list(request):
    form = AddCallsignForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            allocation = services.add_callsign(request.user, **form.values)
        except ValidationError as exc:
            add_errors(form, exc)
        else:
            messages.success(request, f"{allocation.callsign} has been added.")
            return redirect("callsigns:list")
    now = timezone.now()
    allocations = list(_allocations(request.user))
    return render(
        request,
        "callsigns/list.html",
        {
            "form": form,
            "current": [a for a in allocations if a.is_active(now)],
            "other": [a for a in allocations if not a.is_active(now)],
        },
        status=422 if form.errors else 200,
    )


@require_http_methods(["GET", "POST"])
@login_required
@api("callsign-get", "callsign-update")
def callsign_change(request, pk):
    allocation = get_object_or_404(_allocations(request.user), pk=pk)
    initial = {
        "valid_until": allocation.valid_until and allocation.valid_until.date(),
        "is_primary": allocation.is_primary,
    }
    form = ChangeCallsignForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            services.change_callsign(allocation, **form.values)
        except ValidationError as exc:
            add_errors(form, exc)
        else:
            messages.success(request, f"{allocation.callsign} has been saved.")
            return redirect("callsigns:list")
    return render(
        request,
        "callsigns/change.html",
        {"form": form, "allocation": allocation},
        status=422 if form.errors else 200,
    )


@require_POST
@login_required
@api("callsign-end")
def callsign_end(request, pk):
    allocation = get_object_or_404(_allocations(request.user), pk=pk)
    if allocation.is_active():
        try:
            allocation.end()
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(
                request, f"{allocation.callsign} is no longer held from now."
            )
    return redirect("callsigns:list")
