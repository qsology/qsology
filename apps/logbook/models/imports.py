from apps.logbook.models.qso import QSO, ProtectedOperation
from django.conf import settings
from django.db import models


class ImportFormat(models.TextChoices):
    ADI = "adi", "ADIF (.adi)"
    ADX = "adx", "ADIF XML (.adx)"
    JSON = "json", "JSON"


class ImportRecordStatus(models.TextChoices):
    # Failed sanity checks and is waiting for the user to accept it
    HELD = "held", "Held for review"
    # Couldn't be read or stored at all, kept so nothing is lost
    FAILED = "failed", "Failed"
    # Already in the log
    DUPLICATE = "duplicate", "Duplicate"
    # Was held or failed and has since been logged
    LOGGED = "logged", "Logged"


class ImportBatch(models.Model):
    """A file of QSOs imported in one go

    The whole file is kept as uploaded. Records that weren't logged straight
    away are kept as ImportRecords, so every record in the file is either a
    QSO or waiting to be looked at"""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="import_batches",
    )
    format = models.CharField(max_length=8, choices=ImportFormat.choices)
    filename = models.CharField(max_length=255, blank=True)
    original = models.TextField(help_text="The file exactly as imported")
    accept_warnings = models.BooleanField(default=False)
    header = models.JSONField(default=dict, blank=True)
    total = models.PositiveIntegerField(default=0)
    logged = models.PositiveIntegerField(default=0)
    duplicates = models.PositiveIntegerField(default=0)
    held = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("owner", "-created_at")
        verbose_name_plural = "import batches"

    def __str__(self) -> str:
        return f"{self.filename or self.format} ({self.created_at:%Y-%m-%d})"

    def delete(self, *args, **kwargs):
        """Only a batch with nothing left waiting can be deleted, its QSOs
        are kept either way"""
        if self.records.filter(
            status__in=[ImportRecordStatus.HELD, ImportRecordStatus.FAILED]
        ).exists():
            raise ProtectedOperation(
                "This import still has records waiting to be looked at"
            )
        return super().delete(*args, **kwargs)


class ImportRecord(models.Model):
    """A record from an import that wasn't logged straight away"""

    batch = models.ForeignKey(
        ImportBatch, on_delete=models.CASCADE, related_name="records"
    )
    index = models.PositiveIntegerField(help_text="Position in the file")
    status = models.CharField(max_length=16, choices=ImportRecordStatus.choices)
    raw = models.TextField(help_text="The record exactly as it was in the file")
    data = models.JSONField(
        default=dict, blank=True, help_text="The QSO values read from it"
    )
    issues = models.JSONField(default=list, blank=True)
    error = models.TextField(blank=True)
    qso = models.ForeignKey(
        QSO,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("batch", "index")
        constraints = (
            models.UniqueConstraint(
                fields=["batch", "index"], name="import_record_unique_index"
            ),
        )

    def __str__(self) -> str:
        return f"{self.batch_id} #{self.index} {self.status}"
