"""QSOs, callsign look ups, exports and imports

QSOs are sent and given back in the same JSON as the JSON export: field
names as in apps.logbook.fields, times in ISO 8601 with a timezone, numbers
as strings or numbers, and ADIF fields without a column of their own in
extra"""

import uuid as uuid_module
from datetime import datetime
from typing import Any

from apps.logbook import exports, fields, importing, query, services
from apps.logbook import lookup as lookups
from apps.logbook.forms import ExportForm
from apps.logbook.models import (
    QSO,
    Export,
    ImportBatch,
    ImportRecord,
    ImportRecordStatus,
)
from apps.logbook.views import download_response
from django.conf import settings
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from ninja import File, Form, Query, Router, Schema, Status
from ninja.files import UploadedFile
from ninja.pagination import LimitOffsetPagination, paginate

router = Router(tags=["log"])


def _values(data: dict) -> dict:
    """QSO values from JSON, raising ValidationError naming each field that
    can't be read"""
    values, errors = {}, {}
    for key, value in data.items():
        if key == "extra":
            if not isinstance(value, dict):
                errors[key] = ["Must be an object of ADIF fields"]
            else:
                values[key] = value
            continue
        field = fields.BY_NAME.get(key)
        if field is None:
            errors[key] = ["Not a QSO field"]
            continue
        try:
            value = fields.from_json_value(field, value)
        except ValueError as exc:
            errors[key] = [str(exc)]
            continue
        values[key] = "" if value is None and field.is_text else value
    if errors:
        raise ValidationError(errors)
    return values


class QSOOut(Schema):
    uuid: uuid_module.UUID
    version: int
    review_status: str
    # Why it needs checking, if it does
    issues: list[dict]
    deleted: bool
    qso: dict[str, Any]

    @staticmethod
    def resolve_deleted(qso) -> bool:
        return qso.is_deleted

    @staticmethod
    def resolve_qso(qso) -> dict:
        data = qso.to_data()
        data.pop("uuid", None)
        return data


def _out(qso: QSO) -> QSOOut:
    return QSOOut.model_validate(qso)


class NewQSO(Schema):
    # QSO fields by name, call and time_on at least
    qso: dict[str, Any]
    # Give one to make sending again safe: a QSO already logged with it is
    # given back as it is
    uuid: uuid_module.UUID | None = None
    # Log it even with warnings, marked for review
    accept_warnings: bool = False


class QSOChanges(Schema):
    # The fields to change, null or "" empties one
    changes: dict[str, Any]
    # The version these changes are to, if it's changed since: 409
    version: int | None = None
    accept_warnings: bool = False


class QSOFilters(Schema):
    # All or part of a callsign
    call: str = ""
    # In the filter syntax, e.g. band:20m mode:CW date>=2024-01-01
    filter: str = ""
    deleted: bool = False


def _own(request, uuid):
    return get_object_or_404(QSO.all_objects, uuid=uuid, owner=request.user)


@router.get("/qsos", response=list[QSOOut], url_name="qso-list")
@paginate(LimitOffsetPagination)
def qso_list(request, filters: Query[QSOFilters]):
    """Your QSOs, newest first"""
    qsos = QSO.all_objects.filter(owner=request.user)
    if not filters.deleted:
        qsos = qsos.filter(deleted_at__isnull=True)
    call = "".join(filters.call.split()).upper()
    if call:
        qsos = qsos.filter(call__contains=call)
    if filters.filter:
        try:
            qsos = qsos.filter(
                query.compile_filter(query.parse(filters.filter))
            )
        except query.QueryError as exc:
            raise ValidationError({"filter": [str(exc)]}) from exc
    return qsos.order_by("-time_on", "-id")


@router.post(
    "/qsos", response={200: QSOOut, 201: QSOOut}, url_name="qso-create"
)
def qso_create(request, payload: NewQSO):
    """Log a QSO. 201 if it's new, 200 if one with its uuid was already
    logged. 422 with warnings if it has any, send it again with
    accept_warnings to log it anyway"""
    existed = (
        payload.uuid is not None
        and QSO.all_objects.filter(
            uuid=payload.uuid, owner=request.user
        ).exists()
    )
    qso = services.log_qso(
        request.user,
        _values(payload.qso),
        accept_warnings=payload.accept_warnings,
        qso_uuid=payload.uuid,
        actor=request.user,
        reason="Logged through the API",
    )
    return Status(200 if existed else 201, _out(qso))


@router.get("/qsos/{uuid}", response=QSOOut, url_name="qso-get")
def qso_get(request, uuid: uuid_module.UUID):
    return _out(_own(request, uuid))


@router.patch("/qsos/{uuid}", response=QSOOut, url_name="qso-update")
def qso_update(request, uuid: uuid_module.UUID, payload: QSOChanges):
    """Change a QSO. Every change is kept, see its revisions"""
    qso = services.update_qso(
        _own(request, uuid),
        _values(payload.changes),
        accept_warnings=payload.accept_warnings,
        expected_version=payload.version,
        actor=request.user,
        reason="Changed through the API",
    )
    return _out(qso)


@router.delete("/qsos/{uuid}", response=QSOOut, url_name="qso-delete")
def qso_delete(request, uuid: uuid_module.UUID):
    """Delete a QSO. It can be brought back with restore"""
    qso = _own(request, uuid)
    qso.delete(actor=request.user, reason="Deleted through the API")
    return _out(qso)


@router.post("/qsos/{uuid}/restore", response=QSOOut, url_name="qso-restore")
def qso_restore(request, uuid: uuid_module.UUID):
    qso = _own(request, uuid)
    qso.restore(actor=request.user, reason="Restored through the API")
    return _out(qso)


@router.post("/qsos/{uuid}/checked", response=QSOOut, url_name="qso-review")
def qso_review(request, uuid: uuid_module.UUID):
    """Say a QSO's warnings have been checked, they won't flag it again"""
    qso = _own(request, uuid)
    if qso.issues:
        qso.mark_reviewed(actor=request.user)
    return _out(qso)


class Revision(Schema):
    version: int
    action: str
    reason: str
    created_at: datetime
    qso: dict[str, Any]


@router.get(
    "/qsos/{uuid}/revisions", response=list[Revision], url_name="qso-revisions"
)
def qso_revisions(request, uuid: uuid_module.UUID):
    """The QSO as it was after every change, newest first"""
    return [
        Revision(
            version=revision.version,
            action=revision.action,
            reason=revision.reason,
            created_at=revision.created_at,
            qso=revision.data,
        )
        for revision in _own(request, uuid).revisions.order_by("-version")
    ]


class LookupOut(Schema):
    call: str
    # What a QSO with them would be filled in with
    fill: dict[str, str]
    # QSOs with them before, and the last few
    count: int
    previous: list[QSOOut]
    # Every field QRZ.com sent, if it was asked
    qrz: dict[str, str] | None
    qrz_message: str


@router.get("/lookup/{call}", response=LookupOut, url_name="callsign-lookup")
def callsign_lookup(request, call: str):
    """What's known about a callsign: your QSOs with it, and QRZ.com if
    you've set up a login there"""
    found = lookups.lookup(request.user, call)
    return LookupOut(
        call=found.call,
        fill=found.fill,
        count=found.count,
        previous=found.previous,
        qrz=found.qrz.raw if found.qrz else None,
        qrz_message=found.qrz_message,
    )


class ExportOut(Schema):
    id: uuid_module.UUID
    name: str
    format: str
    status: str
    qso_count: int | None
    size: int | None
    created_at: datetime
    finished_at: datetime | None
    expires_at: datetime | None
    downloadable: bool

    @staticmethod
    def resolve_downloadable(export) -> bool:
        return export.is_downloadable


class ExportRequest(Schema):
    # adi, adx or json
    format: str
    # A logbook's id, every QSO if not given
    logbook: int | None = None


def _export_out(export: Export) -> ExportOut:
    return ExportOut.model_validate(export)


@router.get("/exports", response=list[ExportOut], url_name="export-list")
@paginate(LimitOffsetPagination)
def export_list(request):
    return Export.objects.filter(owner=request.user)


@router.post("/exports", response={202: ExportOut}, url_name="export-create")
def export_create(request, payload: ExportRequest):
    """Ask for an export. It's made in the background, fetch it to see
    when it's ready to download"""
    form = ExportForm(payload.dict(), user=request.user)
    if not form.is_valid():
        raise ValidationError(form.errors.as_data())
    export = exports.request_export(
        request.user,
        form.cleaned_data["format"],
        logbook=form.cleaned_data.get("logbook"),
    )
    return Status(202, _export_out(export))


@router.get("/exports/{export_id}", response=ExportOut, url_name="export-get")
def export_get(request, export_id: uuid_module.UUID):
    return _export_out(
        get_object_or_404(Export, pk=export_id, owner=request.user)
    )


@router.get("/exports/{export_id}/download", url_name="export-download")
def export_download(request, export_id: uuid_module.UUID):
    """The file, or a redirect to a link to it that works for a few
    minutes. 410 if it isn't ready or has expired"""
    return download_response(
        get_object_or_404(Export, pk=export_id, owner=request.user)
    )


class ImportRecordOut(Schema):
    id: int
    # Where it was in the file, from 0
    index: int
    status: str
    raw: str
    issues: list[dict]
    error: str
    qso: uuid_module.UUID | None


class ImportOut(Schema):
    id: int
    filename: str
    format: str
    total: int
    logged: int
    duplicates: int
    held: int
    failed: int
    created_at: datetime
    finished_at: datetime | None


class ImportDetail(ImportOut):
    # Records held for their warnings or that failed, waiting to be looked
    # at
    waiting: list[ImportRecordOut]


def _record_out(record: ImportRecord) -> ImportRecordOut:
    return ImportRecordOut(
        id=record.pk,
        index=record.index,
        status=record.status,
        raw=record.raw,
        issues=record.issues,
        error=record.error,
        qso=record.qso.uuid if record.qso_id else None,
    )


@router.get("/imports", response=list[ImportOut], url_name="import-list")
@paginate(LimitOffsetPagination)
def import_list(request):
    return ImportBatch.objects.filter(owner=request.user)


@router.post("/imports", response={201: ImportDetail}, url_name="import-create")
def import_create(
    request,
    file: File[UploadedFile],
    accept_warnings: Form[bool] = False,
):
    """Import an ADIF (.adi or .adx) or JSON file, sent as multipart form
    data. Every record is logged, or kept waiting to be looked at"""
    if file.size > settings.IMPORT_MAX_BYTES:
        raise ValidationError(
            {"file": [f"At most {settings.IMPORT_MAX_BYTES} bytes"]}
        )
    batch = importing.import_file(
        request.user,
        file.read(),
        filename=file.name or "",
        accept_warnings=accept_warnings,
        actor=request.user,
    )
    return Status(201, _import_detail(batch))


def _import_detail(batch: ImportBatch) -> ImportDetail:
    waiting = batch.records.filter(
        status__in=[ImportRecordStatus.HELD, ImportRecordStatus.FAILED]
    ).select_related("qso")
    return ImportDetail(
        **ImportOut.model_validate(batch).dict(),
        waiting=[_record_out(record) for record in waiting],
    )


@router.get("/imports/{batch_id}", response=ImportDetail, url_name="import-get")
def import_get(request, batch_id: int):
    return _import_detail(
        get_object_or_404(ImportBatch, pk=batch_id, owner=request.user)
    )


@router.post(
    "/imports/{batch_id}/records/{record_id}/log",
    response=ImportRecordOut,
    url_name="import-record-log",
)
def import_record_log(request, batch_id: int, record_id: int):
    """Log a record held for its warnings, marked for review, or try a
    failed one again"""
    record = get_object_or_404(
        ImportRecord.objects.select_related("batch"),
        pk=record_id,
        batch__pk=batch_id,
        batch__owner=request.user,
    )
    return _record_out(importing.retry_record(record, actor=request.user))
