import uuid
from datetime import UTC

from apps.logbook import query
from apps.logbook.models.logbook import match_all
from apps.logbook.models.qso import QSO
from django.conf import settings
from django.core.files.storage import storages
from django.db import models, transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone
from django.utils.text import slugify


class ExportFormat(models.TextChoices):
    ADI = "adi", "ADIF (.adi)"
    ADX = "adx", "ADIF XML (.adx)"
    JSON = "json", "JSON"


class ExportStatus(models.TextChoices):
    PENDING = "pending", "Waiting"
    RUNNING = "running", "Being built"
    READY = "ready", "Ready to download"
    FAILED = "failed", "Failed"
    # Downloadable until it's tidied away
    EXPIRED = "expired", "Expired"


def exports_storage():
    return storages["exports"]


def export_path(export: Export, filename: str) -> str:
    # The export's uuid makes the path impossible to guess, not that the
    # storage can be reached without signing in anyway
    return f"{export.owner_id}/{export.pk}/{filename}"


class Export(models.Model):
    """A log export, built by celery and kept for a while to download

    Exports go to their own storage, never public, see EXPORTS_S3_BUCKET"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="exports",
    )
    logbook = models.ForeignKey(
        "logbook.Logbook",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="exports",
    )
    # The logbook's name and filter when it was asked for, so the export is
    # what was asked for even if the logbook changes or goes
    name = models.CharField(max_length=100)
    filter = models.JSONField(default=match_all)
    format = models.CharField(max_length=10, choices=ExportFormat.choices)
    status = models.CharField(
        max_length=10,
        choices=ExportStatus.choices,
        default=ExportStatus.PENDING,
    )
    file = models.FileField(
        storage=exports_storage,
        upload_to=export_path,
        max_length=255,
        blank=True,
    )
    qso_count = models.PositiveIntegerField(null=True, blank=True)
    size = models.PositiveBigIntegerField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    # What went wrong, for staff, users are only told that it did
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    downloads = models.PositiveIntegerField(default=0)
    last_downloaded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = (
            models.Index(fields=["owner", "-created_at"]),
            models.Index(fields=["status", "expires_at"]),
        )

    def __str__(self) -> str:
        return f"{self.name} ({self.format}) for {self.owner}"

    def qsos(self):
        return QSO.objects.filter(owner_id=self.owner_id).filter(
            query.compile_filter(self.filter)
        )

    @property
    def filename(self) -> str:
        """What the download is called"""
        stamp = (self.created_at or timezone.now()).astimezone(UTC)
        name = slugify(self.name) or "log"
        return f"{name}-{stamp:%Y%m%d-%H%M%S}.{self.format}"

    @property
    def is_downloadable(self) -> bool:
        return (
            self.status == ExportStatus.READY
            and bool(self.file)
            and (self.expires_at is None or self.expires_at > timezone.now())
        )

    def delete_file(self) -> None:
        """Remove the file from storage once the export no longer points at
        it has been committed"""
        if not self.file:
            return
        storage, name = self.file.storage, self.file.name
        self.file = ""
        transaction.on_commit(lambda: storage.delete(name))


@receiver(post_delete, sender=Export)
def _delete_export_file(sender, instance: Export, **kwargs) -> None:
    """The file goes with the export, e.g. when the owner's account is"""
    instance.delete_file()
