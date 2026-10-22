from apps.notifications import services

from celery import shared_task


@shared_task
def send_notification(notification_id: str) -> list[str]:
    return services.send(notification_id)
