"""Telling users about things

notify() is all the rest of the app needs. The notification is saved with
whatever is being done, and sent through the user's channels once that has
been committed, so nobody is told about something that didn't happen"""

import logging

from apps.audit.recording import record
from apps.notifications import channels
from apps.notifications.models import Notification
from django.db import transaction

logger = logging.getLogger(__name__)


def notify(
    user,
    kind: str,
    title: str,
    body: str = "",
    url: str = "",
    data: dict | None = None,
) -> Notification:
    notification = Notification.objects.create(
        user=user,
        kind=kind,
        title=title,
        body=body,
        url=url,
        data=data or {},
    )
    transaction.on_commit(lambda: _queue(notification.pk))
    return notification


def _queue(notification_id) -> None:
    from apps.notifications.tasks import send_notification

    try:
        send_notification.delay(str(notification_id))
    except Exception:
        # It's saved, so the user can still see it in the app
        logger.exception("Couldn't queue notification %s", notification_id)


def send(notification_id) -> list[str]:
    """Send a notification through each of the user's channels it hasn't
    been sent through yet, returning the channels it went through

    One channel failing doesn't stop the others"""
    notification = Notification.objects.select_related("user").get(
        pk=notification_id
    )
    wanted = channels.preferred_channels(notification.user, notification.kind)
    sent, failed = [], []
    for name in wanted:
        if name in notification.sent_via:
            continue
        try:
            channels.get_channel(name).send(notification)
        except Exception:
            logger.exception(
                "Sending notification %s via %s failed", notification.pk, name
            )
            failed.append(name)
            continue
        sent.append(name)
        notification.sent_via = [*notification.sent_via, name]
        Notification.objects.filter(pk=notification.pk).update(
            sent_via=notification.sent_via
        )
    record(
        notification.user_id,
        "notification.sent",
        f"{notification.kind} sent via {', '.join(sent) or 'nothing'}",
        obj=notification,
        data={"kind": notification.kind, "sent": sent, "failed": failed},
    )
    return notification.sent_via
