import uuid

from django.conf import settings
from django.db import models


class TaskRunStatus(models.TextChoices):
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    # Failed this time, and will be tried again
    RETRYING = "retrying", "Failed, trying again"
    FAILED = "failed", "Failed"
    # Rejected or ignored by the task, e.g. a duplicate
    IGNORED = "ignored", "Ignored"


class TaskRunQuerySet(models.QuerySet):
    def visible_to(self, viewer):
        """Task runs are for staff only, they hold arguments, results and
        errors. Users see what was done for them through AuditEntry"""
        if viewer.is_staff:
            return self
        return self.none()


class TaskRun(models.Model):
    """One run of a celery task, recorded for every task whatever it is

    Each try of a task that's retried is its own run. What a run did for a
    particular user is recorded as AuditEntry rows pointing at it"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # The task's name, e.g. apps.logbook.tasks.build_export
    task = models.CharField(max_length=200)
    # Celery's id, the same for each try of a task
    task_id = models.CharField(max_length=255, db_index=True)
    # 0 for the first try
    attempt = models.PositiveSmallIntegerField(default=0)
    args = models.JSONField(default=list, blank=True)
    kwargs = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=10,
        choices=TaskRunStatus.choices,
        default=TaskRunStatus.RUNNING,
    )
    # The worker it ran on, empty when run in the web app (e.g. eager)
    worker = models.CharField(max_length=255, blank=True)
    result = models.JSONField(null=True, blank=True)
    # What went wrong, for staff only
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    objects = TaskRunQuerySet.as_manager()

    class Meta:
        ordering = ("-started_at",)
        indexes = (
            models.Index(fields=["task", "-started_at"]),
            models.Index(fields=["started_at"]),
        )

    def __str__(self) -> str:
        return f"{self.task} ({self.status}) at {self.started_at}"

    @property
    def duration(self):
        if self.finished_at is None:
            return None
        return self.finished_at - self.started_at


class AuditEntryQuerySet(models.QuerySet):
    def visible_to(self, viewer):
        """Staff see everything, users only the entries about them that
        they're meant to see (their exports)"""
        if viewer.is_staff:
            return self
        return self.for_user(viewer)

    def for_user(self, user):
        """What a user sees of the work done for them"""
        return self.filter(user=user, visible_to_user=True)

    def about(self, obj):
        return self.filter(
            object_type=obj._meta.label_lower, object_id=str(obj.pk)
        )


class AuditEntry(models.Model):
    """Something a background task did for a user, e.g. built their export
    or deleted their account

    Users see the entries marked visible_to_user (their exports), staff see
    them all. The summary is what users are shown, so it says what happened
    in their terms and never includes an error. When an account is deleted
    the entries users could see go with it, the rest stay for staff, with
    only the user's id"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Empty when recorded outside a task, e.g. called from a shell
    run = models.ForeignKey(
        TaskRun,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="entries",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
    )
    # The user's id, kept once their account is deleted
    user_pk = models.PositiveBigIntegerField(null=True, blank=True)
    # What was done, e.g. export.built
    action = models.CharField(max_length=50)
    summary = models.CharField(max_length=500, blank=True)
    visible_to_user = models.BooleanField(default=False)
    # What it was done to, e.g. logbook.export and its id
    object_type = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    # Never anything personal, it outlives the account
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AuditEntryQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at",)
        verbose_name_plural = "audit entries"
        indexes = (
            models.Index(fields=["user", "visible_to_user", "-created_at"]),
            models.Index(fields=["user_pk", "-created_at"]),
            models.Index(fields=["object_type", "object_id"]),
            models.Index(fields=["created_at"]),
        )

    def __str__(self) -> str:
        return f"{self.action} for user {self.user_pk} at {self.created_at}"
