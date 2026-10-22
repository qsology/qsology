import uuid

from django.conf import settings
from django.db import models


class Notification(models.Model):
    """Something a user is told about

    Every notification is kept, whichever ways it's sent, so there's a
    record of it for the user to see in the app"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    # What it's about, e.g. export.ready, for channels and preferences to
    # pick out
    kind = models.CharField(max_length=50)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    # Where in the app to go to act on it
    url = models.CharField(max_length=500, blank=True)
    data = models.JSONField(default=dict, blank=True)
    # The channels it has been sent through
    sent_via = models.JSONField(default=list, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = (models.Index(fields=["user", "-created_at"]),)

    def __str__(self) -> str:
        return f"{self.kind} for {self.user}: {self.title}"

    @property
    def is_read(self) -> bool:
        return self.read_at is not None
