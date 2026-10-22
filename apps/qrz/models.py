from django.conf import settings
from django.db import models


class QRZCredential(models.Model):
    """A user's QRZ.com login, used for callsign lookups

    TODO: The password is kept in plain text for now, it is to move to a
    secrets store such as OpenBao"""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="qrz_credential",
    )
    username = models.CharField(max_length=100)
    password = models.CharField(max_length=255)
    is_enabled = models.BooleanField(default=True)
    # The XML API hands out a session key at login which lasts a while,
    # reusing it saves logging in for every lookup
    session_key = models.CharField(max_length=64, blank=True, editable=False)
    last_error = models.CharField(max_length=255, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "QRZ credential"

    def __str__(self) -> str:
        return f"QRZ login {self.username} for {self.user}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            previous = (
                QRZCredential.objects.filter(pk=self.pk)
                .values("username", "password")
                .first()
            )
            # A new login invalidates the old session
            if previous and previous != {
                "username": self.username,
                "password": self.password,
            }:
                self.session_key = ""
        self.full_clean()
        super().save(*args, **kwargs)
