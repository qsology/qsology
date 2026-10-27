"""The log's pages: logging and changing QSOs, exports and imports

Every page works as plain forms and links. htmx and the page's own scripts
(static/js) only add to that, asking for part of a page instead of all of
it, see apps.web.htmx. Nothing typed into a QSO is ever thrown away: a form
that can't be logged comes back with everything still in it"""

import json

from apps.logbook import exports, fields, forms, importing, query, services
from apps.logbook import lookup as lookups
from apps.logbook.models import (
    QSO,
    Export,
    ImportBatch,
    ImportRecord,
    ImportRecordStatus,
    QSOConflict,
    ReviewStatus,
)
from apps.logbook.services import SanityCheckError
from apps.web.htmx import is_htmx, login_required
from apps.web.parity import api
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import F
from django.http import (
    FileResponse,
    HttpResponse,
    HttpResponseRedirect,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import (
    require_GET,
    require_http_methods,
    require_POST,
)
from storages.backends.s3 import S3Storage

# QSOs sent after the session ran out, kept until they're logged
SESSION_UNSENT = "unsent_qsos"
MAX_UNSENT = 50
# What the last QSO had in the fields that are kept for the next one
SESSION_STICKY = "log_sticky"
RECENT = 20
PAGE_SIZE = 50


def _advanced(request) -> bool:
    return request.user.ui_mode == "advanced"


def _entry_fields(request) -> tuple[str, ...]:
    return forms.ADVANCED_FIELDS if _advanced(request) else forms.SIMPLE_FIELDS


def _recent(user):
    return QSO.objects.filter(owner=user).order_by("-time_on", "-id")[:RECENT]


def _unsent(request) -> list[dict]:
    return list(request.session.get(SESSION_UNSENT, []))


def _forget_unsent(request, qso_uuid) -> None:
    unsent = [
        entry
        for entry in _unsent(request)
        if entry.get("uuid") != str(qso_uuid)
    ]
    request.session[SESSION_UNSENT] = unsent


def _keep_unsent(request) -> None:
    """Keep a QSO sent while signed out, to log once signed in again"""
    entry = {
        name: request.POST.get(name, "")
        for name in (*forms.FORM_FIELDS, "uuid", "shown")
        if request.POST.get(name, "")
    }
    if entry.get("call"):
        unsent = [
            other
            for other in _unsent(request)
            if other.get("uuid") != entry.get("uuid")
        ]
        # Enough for a run of QSOs logged before noticing, without letting
        # the session grow for good
        request.session[SESSION_UNSENT] = [*unsent, entry][-MAX_UNSENT:]


# ADIF names of the fields, for the log's script to save QSOs it hasn't
# been able to send as an ADIF file
ADIF_NAMES = json.dumps(
    {
        field.name: field.adif
        for field in fields.FIELDS
        if field.adif and field.name not in forms.TIME_FIELDS
    }
)


def _log_page(
    request,
    form,
    *,
    status=200,
    warnings=None,
    lookup=None,
    unsent=None,
    focus=None,
):
    """The log page. focus is the field the cursor starts in, as the page
    is new. Never in a part of a page, where it would move the cursor"""
    if focus in form.fields:
        form.fields[focus].widget.attrs["autofocus"] = True
    call = form.fields.get("call")
    if call is not None:
        # Looked up when the cursor leaves the callsign, into #lookup.
        # Only that panel changes, never the form, see static/js/log.js
        call.widget.attrs.update(
            {
                "hx-get": reverse("logbook:lookup"),
                "hx-trigger": "change",
                "hx-target": "#lookup",
                "hx-include": "this",
                "hx-sync": "this:replace",
            }
        )
    station = form.fields.get("station_callsign")
    if station is not None:
        station.widget.attrs["list"] = "my-calls"
    return render(
        request,
        "logbook/log.html",
        {
            "form": form,
            "warnings": warnings or [],
            "lookup": lookup,
            "recent": _recent(request.user),
            "unsent": unsent,
            "unsent_count": len(_unsent(request)),
            "modes": forms.COMMON_MODES,
            "my_calls": _my_calls(request.user),
            "adif_names": ADIF_NAMES,
        },
        status=status,
    )


def _my_calls(user) -> list[str]:
    from apps.callsigns.models import Callsign

    return list(Callsign.objects.held_by(user).values_list("call", flat=True))


@require_http_methods(["GET", "POST"])
@api("qso-create")
def log(request):
    """The log: a form for the next QSO, and the last few logged

    Sent with Enter, the QSO is logged and the form comes back empty for
    the next, keeping the frequency, mode and the like. The look up button
    fills in what's known about the callsign without logging anything"""
    if not request.user.is_authenticated:
        if is_htmx(request):
            return HttpResponse("Signed out", status=401)
        if request.method == "POST":
            _keep_unsent(request)
            messages.info(
                request,
                "You'd been signed out, so the QSO wasn't logged yet. Sign "
                "in and it'll be there to log.",
            )
        return redirect_to_login(reverse("logbook:log"))

    if request.method == "GET":
        unsent = _unsent(request)
        if unsent:
            # The first QSO sent while signed out, ready to log
            entry = unsent[0]
            form = forms.QSOForm(
                initial={k: v for k, v in entry.items() if k != "shown"},
                field_names=_entry_fields(request),
            )
            return _log_page(request, form, unsent=entry)
        initial = forms.entry_initial(
            request.user, request.session.get(SESSION_STICKY)
        )
        form = forms.QSOForm(
            initial=initial, field_names=_entry_fields(request)
        )
        return _log_page(request, form, focus="call")

    if request.POST.get("action") == "discard-unsent":
        _forget_unsent(request, request.POST.get("uuid"))
        messages.info(request, "That QSO has been thrown away.")
        return redirect("logbook:log")

    form = forms.QSOForm.for_post(request.POST)
    if request.POST.get("action") == "lookup":
        return _lookup_into_form(request, form)

    warnings = []
    if form.is_valid():
        existed = (
            form.qso_uuid is not None
            and QSO.all_objects.filter(
                uuid=form.qso_uuid, owner=request.user
            ).exists()
        )
        try:
            qso = services.log_qso(
                request.user,
                form.values(),
                accept_warnings=form.cleaned_data["accept_warnings"],
                qso_uuid=form.qso_uuid,
                actor=request.user,
            )
        except SanityCheckError as exc:
            warnings = exc.issues
        except ValidationError as exc:
            form.add_service_errors(exc)
        else:
            return _logged(request, form, qso, existed)

    if is_htmx(request):
        return render(
            request,
            "logbook/_problem.html",
            {"form": form, "warnings": warnings},
            status=422,
        )
    return _log_page(request, form, status=422, warnings=warnings)


def _logged(request, form, qso, existed):
    _forget_unsent(request, qso.uuid)
    request.session[SESSION_STICKY] = {
        name: form.data.get(name, "")
        for name in forms.STICKY_FIELDS
        if form.data.get(name, "")
    }
    if is_htmx(request):
        return render(
            request,
            "logbook/_qso_row.html",
            {"qso": qso, "already": existed},
            status=200 if existed else 201,
        )
    if existed:
        messages.info(
            request,
            f"{qso.call} was already logged, it was sent twice. Change it "
            "from its page if it needs changing.",
        )
    else:
        messages.success(request, f"Logged {qso.call}.")
    return redirect("logbook:log")


def _lookup_into_form(request, form):
    """Fill the form's empty fields from a look up, without logging"""
    data = {key: request.POST.get(key, "") for key in request.POST}
    found = lookups.lookup(request.user, data.get("call", ""))
    for name, value in found.fill.items():
        if not data.get(name):
            data[name] = value
    initial = {key: value for key, value in data.items() if value}
    filled = forms.QSOForm(initial=initial, field_names=form.field_names)
    # On to the first field still to fill in
    empty = [n for n in form.field_names[1:] if not initial.get(n)]
    return _log_page(
        request, filled, lookup=found, focus=empty[0] if empty else None
    )


@require_GET
@login_required
@api("callsign-lookup")
def lookup(request):
    found = lookups.lookup(request.user, request.GET.get("call", ""))
    template = (
        "logbook/_lookup.html" if is_htmx(request) else "logbook/lookup.html"
    )
    return render(request, template, {"lookup": found})


# QSOs


def _search(request, data):
    form = forms.QSOSearchForm(data)
    qsos = QSO.all_objects.filter(owner=request.user)
    error = ""
    if form.is_valid():
        if not form.cleaned_data["deleted"]:
            qsos = qsos.filter(deleted_at__isnull=True)
        call = "".join(form.cleaned_data["q"].split()).upper()
        if call:
            qsos = qsos.filter(call__contains=call)
        text = form.cleaned_data["filter"]
        if text:
            try:
                qsos = qsos.filter(query.compile_filter(query.parse(text)))
            except query.QueryError as exc:
                error = str(exc)
                form.add_error("filter", error)
    return form, qsos.order_by("-time_on", "-id")


@require_GET
@login_required
@api("qso-list")
def qso_list(request):
    form, qsos = _search(request, request.GET)
    page = Paginator(qsos, PAGE_SIZE).get_page(request.GET.get("page"))
    query_string = request.GET.copy()
    query_string.pop("page", None)
    return render(
        request,
        "logbook/qso_list.html",
        {
            "form": form,
            "page": page,
            "query_string": query_string.urlencode(),
            "needs_review": QSO.objects.filter(owner=request.user)
            .needs_review()
            .count(),
        },
    )


def _own_qso(request, uuid):
    return get_object_or_404(QSO.all_objects, uuid=uuid, owner=request.user)


@require_http_methods(["GET", "POST"])
@login_required
@api("qso-get", "qso-update")
def qso_detail(request, uuid):
    qso = _own_qso(request, uuid)
    advanced = _advanced(request)
    warnings, conflict = [], False
    if request.method == "POST" and not qso.is_deleted:
        form = forms.QSOForm.for_post(request.POST)
        if form.is_valid():
            try:
                services.update_qso(
                    qso,
                    form.values(include_empty=True),
                    accept_warnings=form.cleaned_data["accept_warnings"],
                    expected_version=_int(request.POST.get("version")),
                    actor=request.user,
                    reason="Changed on the website",
                )
            except SanityCheckError as exc:
                warnings = exc.issues
            except QSOConflict:
                conflict = True
            except ValidationError as exc:
                form.add_service_errors(exc)
            else:
                messages.success(request, f"{qso.call} has been saved.")
                return redirect("logbook:qso", uuid=qso.uuid)
        # Reload, so a retry is against what's saved now
        qso = _own_qso(request, uuid)
    else:
        form = forms.QSOForm(
            initial=forms.initial_from_qso(qso),
            field_names=forms.edit_field_names(qso, advanced),
        )
    return render(
        request,
        "logbook/qso_detail.html",
        {
            "qso": qso,
            "form": form,
            "warnings": warnings,
            "conflict": conflict,
            "modes": forms.COMMON_MODES,
            "my_calls": _my_calls(request.user),
            "revisions": qso.revisions.select_related("actor").order_by(
                "-version"
            )
            if advanced
            else None,
        },
        status=422 if (form.errors or warnings or conflict) else 200,
    )


def _int(value):
    try:
        return int(value)
    except TypeError, ValueError:
        return None


@require_http_methods(["GET", "POST"])
@login_required
@api("qso-delete")
def qso_delete(request, uuid):
    """Asks first, a QSO deleted can be brought back from its page"""
    qso = _own_qso(request, uuid)
    if request.method == "POST":
        qso.delete(actor=request.user, reason="Deleted on the website")
        messages.success(
            request,
            f"{qso.call} has been deleted. It can be brought back from its "
            "page.",
        )
        return redirect("logbook:qso", uuid=qso.uuid)
    return render(request, "logbook/qso_delete.html", {"qso": qso})


@require_POST
@login_required
@api("qso-restore")
def qso_restore(request, uuid):
    qso = _own_qso(request, uuid)
    qso.restore(actor=request.user, reason="Restored on the website")
    messages.success(request, f"{qso.call} is back in the log.")
    return redirect("logbook:qso", uuid=qso.uuid)


@require_POST
@login_required
@api("qso-review")
def qso_review(request, uuid):
    qso = _own_qso(request, uuid)
    if qso.review_status == ReviewStatus.NEEDS_REVIEW:
        qso.mark_reviewed(actor=request.user)
        messages.success(request, f"{qso.call} is marked as checked.")
    return redirect("logbook:qso", uuid=qso.uuid)


# Exports


@require_http_methods(["GET", "POST"])
@login_required
@api("export-list", "export-create")
def export_list(request):
    form = forms.ExportForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            export = exports.request_export(
                request.user,
                form.cleaned_data["format"],
                logbook=form.cleaned_data.get("logbook"),
            )
        except ValidationError as exc:
            form.add_error(None, exc.messages)
        else:
            messages.success(
                request,
                f"Your {export.get_format_display()} export is being made. "
                "It'll be listed here to download when it's ready.",
            )
            return redirect("logbook:export-list")
    return render(
        request,
        "logbook/export_list.html",
        {
            "form": form,
            "exports": Export.objects.filter(owner=request.user)[:50],
            "retention_days": settings.EXPORT_RETENTION.days,
        },
        status=422 if form.errors else 200,
    )


@require_GET
@login_required
@api("export-get")
def export_detail(request, pk):
    """One export, which its row on the exports page asks for while it's
    being made, to show when it's ready"""
    export = get_object_or_404(Export, pk=pk, owner=request.user)
    if is_htmx(request):
        return render(request, "logbook/_export_row.html", {"export": export})
    return render(request, "logbook/export_detail.html", {"export": export})


@require_GET
@never_cache
@login_required
@api("export-download")
def download_export(request, pk):
    """Download an export, only ever by its owner

    From S3 this hands out a link to the file that only works for a few
    minutes, see EXPORT_DOWNLOAD_URL_TTL, anywhere else the file is sent
    from here"""
    export = get_object_or_404(Export, pk=pk, owner=request.user)
    return download_response(export)


def download_response(export):
    if not export.is_downloadable:
        return HttpResponse(
            "This export isn't available to download.", status=410
        )
    Export.objects.filter(pk=export.pk).update(
        downloads=F("downloads") + 1, last_downloaded_at=timezone.now()
    )
    content_type = exports.CONTENT_TYPES[export.format]
    storage = export.file.storage
    if isinstance(storage, S3Storage):
        url = storage.url(
            export.file.name,
            parameters={
                "ResponseContentDisposition": (
                    f'attachment; filename="{export.filename}"'
                ),
                "ResponseContentType": content_type,
            },
            expire=storage.querystring_expire,
        )
        response = HttpResponseRedirect(url)
    else:
        response = FileResponse(
            export.file.open("rb"),
            as_attachment=True,
            filename=export.filename,
            content_type=content_type,
        )
    # The signed link is as good as the file until it expires
    response["Referrer-Policy"] = "no-referrer"
    return response


# Imports


@require_http_methods(["GET", "POST"])
@login_required
@api("import-list", "import-create")
def import_list(request):
    form = forms.ImportForm(
        request.POST or None,
        request.FILES or None,
        max_bytes=settings.IMPORT_MAX_BYTES,
    )
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["file"]
        batch = importing.import_file(
            request.user,
            upload.read(),
            filename=upload.name,
            accept_warnings=form.cleaned_data["accept_warnings"],
            actor=request.user,
        )
        return redirect("logbook:import", pk=batch.pk)
    return render(
        request,
        "logbook/import_list.html",
        {
            "form": form,
            "batches": ImportBatch.objects.filter(owner=request.user)[:50],
        },
        status=422 if form.errors else 200,
    )


@require_GET
@login_required
@api("import-get")
def import_detail(request, pk):
    batch = get_object_or_404(ImportBatch, pk=pk, owner=request.user)
    waiting = batch.records.filter(
        status__in=[ImportRecordStatus.HELD, ImportRecordStatus.FAILED]
    )
    page = Paginator(waiting, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request,
        "logbook/import_detail.html",
        {"batch": batch, "page": page},
    )


@require_POST
@login_required
@api("import-record-log")
def import_record_log(request, pk, record_pk):
    """Log a record that was held for its warnings, or try a failed one
    again"""
    record = get_object_or_404(
        ImportRecord.objects.select_related("batch"),
        pk=record_pk,
        batch__pk=pk,
        batch__owner=request.user,
    )
    record = importing.retry_record(record, actor=request.user)
    if is_htmx(request):
        return render(
            request, "logbook/_import_record.html", {"record": record}
        )
    if record.status == ImportRecordStatus.LOGGED:
        messages.success(request, f"Record {record.index + 1} is logged.")
    else:
        messages.error(
            request,
            f"Record {record.index + 1} still can't be logged: "
            f"{record.error or 'see below'}",
        )
    return redirect("logbook:import", pk=pk)
