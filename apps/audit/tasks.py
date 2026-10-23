import logging

from apps.audit.models import AuditEntry, TaskRun
from django.conf import settings
from django.utils import timezone

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def tidy_task_runs(now=None) -> dict[str, int]:
    """Remove task runs, and what they did, older than
    TASK_AUDIT_RETENTION, run daily by celery beat"""
    cutoff = (now or timezone.now()) - settings.TASK_AUDIT_RETENTION
    # Entries go with their runs, those recorded outside a task on their own
    _, deleted = TaskRun.objects.filter(started_at__lt=cutoff).delete()
    entries, _ = AuditEntry.objects.filter(
        run__isnull=True, created_at__lt=cutoff
    ).delete()
    result = {
        "runs": deleted.get("audit.TaskRun", 0),
        "entries": deleted.get("audit.AuditEntry", 0) + entries,
    }
    logger.info("Removed %(runs)d task runs and %(entries)d entries", result)
    return result
