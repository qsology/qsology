"""Exporting QSOs, in ADI, ADX or JSON

Exports stream a piece at a time so a large log never has to fit in memory.
They are built so they can't fail on anything stored in the log, so a user
can always get their data out.

Users ask for an export with request_export, celery builds it into the
exports storage and the user is told when it's ready to download"""

import io
import logging
import tempfile
from collections.abc import Iterator
from typing import TextIO

from apps.logbook.formats import adi, adx, jsonfmt
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files import File
from django.db import connection, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)

EXPORTERS = {
    "adi": adi.export,
    "adx": adx.export,
    "json": jsonfmt.export,
}

CONTENT_TYPES = {
    "adi": "text/plain; charset=utf-8",
    "adx": "application/xml; charset=utf-8",
    "json": "application/json; charset=utf-8",
}


def export(qsos, format: str, created=None) -> Iterator[str]:
    """Stream the QSOs in the format, a piece at a time

    qsos can be a queryset, which is ordered by time so exports are stable,
    or a list"""
    if format not in EXPORTERS:
        raise ValueError(f"Unknown export format {format!r}")
    if hasattr(qsos, "order_by"):
        qsos = qsos.order_by("time_on", "id")
    return EXPORTERS[format](qsos, created or timezone.now())


def export_to(stream: TextIO, qsos, format: str, created=None) -> None:
    for chunk in export(qsos, format, created):
        stream.write(chunk)


def export_text(qsos, format: str, created=None) -> str:
    return "".join(export(qsos, format, created))


# Built in the background


def request_export(owner, format: str, logbook=None):
    """Ask for an export of a logbook, the master log if none is given

    It's built by celery and the owner is told when it's ready. Asking again
    while the same export is still waiting gives back the one already asked
    for"""
    from apps.logbook.models import Export, ExportStatus, Logbook

    if format not in EXPORTERS:
        raise ValidationError({"format": f"Unknown export format {format!r}"})
    if logbook is None:
        logbook = Logbook.objects.master_for(owner)
    elif logbook.owner_id != owner.pk:
        raise ValidationError({"logbook": "This belongs to someone else"})
    with transaction.atomic():
        waiting = Export.objects.select_for_update().filter(
            owner=owner,
            logbook=logbook,
            format=format,
            status__in=[ExportStatus.PENDING, ExportStatus.RUNNING],
        )
        for export in waiting:
            if export.filter == logbook.filter:
                return export
        export = Export.objects.create(
            owner=owner,
            logbook=logbook,
            name=logbook.name,
            filter=logbook.filter,
            format=format,
        )
        transaction.on_commit(lambda: _queue(export.pk))
    return export


def _queue(export_id) -> None:
    from apps.logbook.tasks import build_export

    try:
        build_export.delay(str(export_id))
    except Exception:
        # Left waiting, tidy_exports queues it again
        logger.exception("Couldn't queue export %s", export_id)


def _claim(export_id):
    """Mark the export as being built, or None if it's being built
    elsewhere or is already done"""
    from apps.logbook.models import Export, ExportStatus

    now = timezone.now()
    with transaction.atomic():
        export = Export.objects.select_for_update().get(pk=export_id)
        stale = export.started_at is None or (
            export.started_at < now - settings.EXPORT_STALE_AFTER
        )
        if export.status == ExportStatus.PENDING or (
            export.status == ExportStatus.RUNNING and stale
        ):
            export.status = ExportStatus.RUNNING
            export.started_at = now
            export.attempts += 1
            export.save(update_fields=["status", "started_at", "attempts"])
            return export
    return None


def _write(export, stream) -> int:
    """Write the export to a binary stream, returning how many QSOs are in
    it

    It's read in one transaction, on PostgreSQL a repeatable read one, so
    the export is the log as it was at one moment even while it's being
    added to"""
    # Only possible at the start of a transaction, not inside another one
    snapshot = (
        connection.vendor == "postgresql" and not connection.in_atomic_block
    )
    with transaction.atomic():
        if snapshot:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
                )
        qsos = export.qsos()
        count = qsos.count()
        text = io.TextIOWrapper(stream, encoding="utf-8", newline="")
        export_to(text, qsos, export.format, created=export.created_at)
        text.flush()
        text.detach()
    return count


def build_export(export_id):
    """Build the export and tell the owner it's ready

    Returns the export, or None when there was nothing to do. Raises if it
    couldn't be built, it's left marked as being built for the caller to
    retry or give up on"""
    from apps.logbook.models import ExportStatus

    export = _claim(export_id)
    if export is None:
        return None
    with tempfile.TemporaryFile() as stream:
        count = _write(export, stream)
        size = stream.tell()
        stream.seek(0)
        # Saved under a new name each time, so a retry never overwrites
        export.file.save(export.filename, File(stream), save=False)
    now = timezone.now()
    with transaction.atomic():
        export.status = ExportStatus.READY
        export.qso_count = count
        export.size = size
        export.error = ""
        export.finished_at = now
        export.expires_at = now + settings.EXPORT_RETENTION
        export.save()
        _notify_ready(export)
    return export


def export_failed(export_id, error: Exception | str, *, final: bool) -> None:
    """Record that building the export went wrong. If it's not final it's
    left waiting to be tried again, otherwise the owner is told"""
    from apps.logbook.models import Export, ExportStatus

    with transaction.atomic():
        export = Export.objects.select_for_update().get(pk=export_id)
        if export.status not in (ExportStatus.PENDING, ExportStatus.RUNNING):
            return
        export.error = str(error)[:2000]
        if final:
            export.status = ExportStatus.FAILED
            export.finished_at = timezone.now()
        else:
            export.status = ExportStatus.PENDING
        export.save()
        if final:
            _notify_failed(export)


def download_path(export) -> str:
    return reverse("logbook:export-download", args=[export.pk])


def _notify_ready(export) -> None:
    from apps.notifications.services import notify

    plural = "" if export.qso_count == 1 else "s"
    notify(
        export.owner,
        "export.ready",
        f"Your export of {export.name} is ready",
        body=(
            f"{export.qso_count} QSO{plural} as {export.get_format_display()}"
            f", ready to download until "
            f"{export.expires_at:%Y-%m-%d %H:%M} UTC."
        ),
        url=download_path(export),
        data={"export": str(export.pk), "format": export.format},
    )


def _notify_failed(export) -> None:
    from apps.notifications.services import notify

    notify(
        export.owner,
        "export.failed",
        f"Your export of {export.name} couldn't be made",
        body="Please try again. Nothing in your log has been changed.",
        data={"export": str(export.pk), "format": export.format},
    )


def tidy_exports(now=None) -> dict[str, int]:
    """Remove downloads that have expired and queue exports that should
    have been built by now, run regularly by celery beat"""
    from apps.logbook.models import Export, ExportStatus

    now = now or timezone.now()
    expired = 0
    for export in Export.objects.filter(
        status=ExportStatus.READY, expires_at__lte=now
    ).iterator():
        with transaction.atomic():
            export.delete_file()
            export.status = ExportStatus.EXPIRED
            export.save(update_fields=["file", "status"])
        expired += 1
    stale = now - settings.EXPORT_STALE_AFTER
    stuck = Export.objects.filter(
        Q(status=ExportStatus.PENDING, created_at__lte=stale)
        | Q(status=ExportStatus.RUNNING, started_at__lte=stale)
    ).values_list("pk", flat=True)
    requeued = 0
    for export_id in stuck:
        _queue(export_id)
        requeued += 1
    return {"expired": expired, "requeued": requeued}
