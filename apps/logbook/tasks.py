import logging

from apps.logbook import exports

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, acks_late=True)
def build_export(self, export_id: str) -> str | None:
    """Build an export, trying again a few times if it fails"""
    try:
        export = exports.build_export(export_id)
    except Exception as exc:
        final = self.request.retries >= self.max_retries
        logger.exception("Building export %s failed", export_id)
        exports.export_failed(export_id, exc, final=final)
        if final:
            raise
        raise self.retry(
            exc=exc, countdown=60 * 2**self.request.retries
        ) from exc
    return None if export is None else export.status


@shared_task
def tidy_exports() -> dict[str, int]:
    return exports.tidy_exports()
