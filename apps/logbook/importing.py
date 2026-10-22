"""Importing QSOs from ADI, ADX or JSON files

Nothing in an import is ever lost. The whole file is kept with the batch,
and every record in it either becomes a QSO or is kept as an ImportRecord:
held if it has warnings that weren't accepted, failed if it can't be logged
at all, or a duplicate if it is already in the log. Held and failed records
can be logged later once they have been looked at"""

import logging
import uuid as uuid_module

from apps.logbook import fields, services
from apps.logbook.formats import adi, adif, adx, jsonfmt
from apps.logbook.models import (
    QSO,
    ImportBatch,
    ImportFormat,
    ImportRecord,
    ImportRecordStatus,
)
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

logger = logging.getLogger(__name__)

PARSERS = {
    ImportFormat.ADI: adi.parse,
    ImportFormat.ADX: adx.parse,
    ImportFormat.JSON: jsonfmt.parse,
}

EXTENSIONS = {
    ".adi": ImportFormat.ADI,
    ".adif": ImportFormat.ADI,
    ".adx": ImportFormat.ADX,
    ".json": ImportFormat.JSON,
}


def decode(content: bytes | str) -> str:
    """Decode a file, never failing

    UTF-8 is tried first, then Windows-1252 which older loggers write, and
    finally Latin-1 which can decode anything"""
    if isinstance(content, str):
        return content
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1")


def guess_format(filename: str, text: str) -> str:
    for extension, format in EXTENSIONS.items():
        if filename.lower().endswith(extension):
            return format
    start = text.lstrip("﻿ \t\r\n")[:200].lower()
    if start.startswith("{"):
        return ImportFormat.JSON
    if start.startswith("<?xml") or start.startswith("<adx"):
        return ImportFormat.ADX
    return ImportFormat.ADI


def _values_from(format: str, data: dict) -> tuple[dict, str | None, list]:
    """QSO values from a parsed record, along with its uuid and any problems

    Values that can't be read are kept in extra rather than dropped"""
    if format == ImportFormat.JSON:
        values, extra, problems = {}, {}, []
        for key, value in data.items():
            if key == "uuid":
                continue
            if key == "extra" and isinstance(value, dict):
                extra.update(
                    {
                        adif.clean_field_name(name): str(item)
                        for name, item in value.items()
                        if item not in (None, "")
                    }
                )
                continue
            if key not in fields.BY_NAME:
                # Kept rather than dropped, under an ADIF style name
                if value not in (None, ""):
                    name = adif.clean_field_name(key)
                    extra[name] = (
                        value if isinstance(value, str) else str(value)
                    )
                    problems.append(f"Unknown field {key!r} kept as {name}")
                continue
            try:
                values[key] = fields.from_json_value(fields.BY_NAME[key], value)
            except ValueError as exc:
                problems.append(str(exc))
                if value not in (None, ""):
                    extra[fields.BY_NAME[key].adif] = (
                        value if isinstance(value, str) else str(value)
                    )
        values["extra"] = extra
        return values, data.get("uuid"), problems
    converted = adif.adif_to_qso(data)
    values = dict(converted.values, extra=converted.extra)
    return values, converted.uuid, converted.problems


def _valid_uuid(value) -> uuid_module.UUID | None:
    try:
        return uuid_module.UUID(str(value)) if value else None
    except ValueError:
        return None


def _log_record(
    batch: ImportBatch,
    data: dict,
    raw: str,
    accept_warnings: bool,
    actor,
) -> tuple[str, QSO | None, list, str, list[str]]:
    """Try to log one record

    Returns the status, the QSO, any issues holding it, an error if it
    failed and any problems reading it"""
    values, record_uuid, problems = _values_from(batch.format, data)
    qso_uuid = _valid_uuid(record_uuid)
    if qso_uuid is not None:
        existing = QSO.all_objects.filter(uuid=qso_uuid).first()
        if existing is not None and existing.owner_id == batch.owner_id:
            return ImportRecordStatus.DUPLICATE, existing, [], "", problems
        if existing is not None:
            # Someone else's QSO, log this one as a new QSO
            qso_uuid = None
    try:
        with transaction.atomic():
            qso = services.log_qso(
                batch.owner,
                values,
                accept_warnings=accept_warnings,
                qso_uuid=qso_uuid,
                actor=actor,
                reason=f"Imported from {batch.filename or batch.format}",
                import_batch=batch,
                source_record=raw,
            )
    except services.SanityCheckError as exc:
        issues = [issue.as_dict() for issue in exc.issues]
        return ImportRecordStatus.HELD, None, issues, "", problems
    except (ValidationError, ValueError, TypeError) as exc:
        messages = getattr(exc, "messages", None) or [str(exc)]
        return (
            ImportRecordStatus.FAILED,
            None,
            [],
            "; ".join(messages),
            problems,
        )
    return ImportRecordStatus.LOGGED, qso, [], "", problems


def import_file(
    owner,
    content: bytes | str,
    *,
    format: str | None = None,
    filename: str = "",
    accept_warnings: bool = False,
    actor=None,
) -> ImportBatch:
    """Import a file of QSOs, returning the batch with its counts

    With accept_warnings, records with warnings are logged and flagged for
    review rather than held"""
    text = decode(content)
    format = format or guess_format(filename, text)
    batch = ImportBatch.objects.create(
        owner=owner,
        format=format,
        filename=filename[:255],
        original=text,
        accept_warnings=accept_warnings,
    )
    try:
        parsed = PARSERS[format](text)
    except ValueError as exc:
        # The file can't be read at all, keep it whole as a failed record
        ImportRecord.objects.create(
            batch=batch,
            index=0,
            status=ImportRecordStatus.FAILED,
            raw=text,
            error=str(exc),
        )
        batch.failed = batch.total = 1
        batch.finished_at = timezone.now()
        batch.save()
        return batch

    batch.header = parsed.header
    for record in parsed.records:
        data = record.data if format == ImportFormat.JSON else record.fields
        status, qso, issues, error, problems = _log_record(
            batch, data, record.raw, accept_warnings, actor or owner
        )
        notes = [*record.problems, *problems]
        error = "; ".join(filter(None, [*notes, error]))
        batch.total += 1
        if status == ImportRecordStatus.LOGGED:
            batch.logged += 1
            if not notes:
                continue
        if status == ImportRecordStatus.DUPLICATE:
            batch.duplicates += 1
        elif status == ImportRecordStatus.HELD:
            batch.held += 1
        else:
            batch.failed += 1
        ImportRecord.objects.create(
            batch=batch,
            index=record.index,
            status=status,
            raw=record.raw,
            data=data,
            issues=issues,
            error=error,
            qso=qso,
        )
    batch.finished_at = timezone.now()
    batch.save()
    logger.info(
        "Import %s: %d records, %d logged, %d duplicates, %d held, %d failed",
        batch.pk,
        batch.total,
        batch.logged,
        batch.duplicates,
        batch.held,
        batch.failed,
    )
    return batch


COUNTERS = {
    ImportRecordStatus.LOGGED: "logged",
    ImportRecordStatus.DUPLICATE: "duplicates",
    ImportRecordStatus.HELD: "held",
    ImportRecordStatus.FAILED: "failed",
}


def retry_record(
    record: ImportRecord, *, accept_warnings: bool = True, actor=None
) -> ImportRecord:
    """Try logging a held or failed record again

    By default its warnings are accepted, so it is logged and flagged for
    review"""
    if record.status not in (
        ImportRecordStatus.HELD,
        ImportRecordStatus.FAILED,
    ):
        return record
    batch = record.batch
    previous = record.status
    status, qso, issues, error, problems = _log_record(
        batch, record.data, record.raw, accept_warnings, actor or batch.owner
    )
    with transaction.atomic():
        record.status = status
        record.qso = qso
        record.issues = issues
        record.error = "; ".join(filter(None, [*problems, error]))
        record.save()
        if status != previous:
            ImportBatch.objects.filter(pk=batch.pk).update(
                **{
                    COUNTERS[previous]: F(COUNTERS[previous]) - 1,
                    COUNTERS[status]: F(COUNTERS[status]) + 1,
                }
            )
    return record
