from django.apps import AppConfig


class AuditConfig(AppConfig):
    name = "apps.audit"
    label = "audit"

    def ready(self) -> None:
        # Records every celery task run, see apps.audit.recording
        from apps.audit import recording  # noqa: F401
