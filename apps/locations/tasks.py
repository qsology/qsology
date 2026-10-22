import logging

from apps.locations import importers

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def refresh_references() -> dict[str, str]:
    """Reload every reference list, run weekly by celery beat

    One list failing doesn't stop the others from loading"""
    results = {}
    for source in importers.SOURCES:
        try:
            results[source] = str(importers.refresh(source))
        except Exception as exc:
            logger.exception("Refreshing %s failed", source)
            results[source] = f"failed: {exc}"
    return results
