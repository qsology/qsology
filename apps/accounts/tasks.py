import logging

from apps.audit.recording import record
from django.contrib.auth import get_user_model
from django.db import transaction

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def delete_scheduled_users() -> int:
    """Delete users whose scheduled deletion time has passed

    Run daily by celery beat. Returns the number of users deleted"""
    User = get_user_model()
    deleted = 0
    for user in User.objects.due_for_deletion().iterator():
        # Re-check the property so the model stays the source of truth for
        # what "pending deletion" means
        if not user.is_pending_deletion:
            continue
        with transaction.atomic():
            # Kept for staff, by the user's id only
            record(
                user.pk,
                "account.deleted",
                "Account deleted once its grace period was over",
                data={"scheduled_for": user.scheduled_deletion_at.isoformat()},
            )
            user._hard_delete()
        deleted += 1
    logger.info("Deleted %d users scheduled for deletion", deleted)
    return deleted
