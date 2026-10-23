import logging
from typing import ClassVar

import pytest
from apps.audit.models import AuditEntry
from apps.notifications import channels, services
from apps.notifications.models import Notification

pytestmark = pytest.mark.django_db


class Recorder(channels.Channel):
    name = "recorder"
    sent: ClassVar[list] = []

    def send(self, notification):
        self.sent.append(notification.pk)


class Broken(channels.Channel):
    name = "broken"

    def send(self, notification):
        raise ConnectionError("down")


@pytest.fixture
def recorder(settings):
    Recorder.sent = []
    channels._load.cache_clear()
    settings.NOTIFICATION_CHANNELS = {
        "log": "apps.notifications.channels.LogChannel",
        "recorder": f"{__name__}.Recorder",
        "broken": f"{__name__}.Broken",
    }
    settings.NOTIFICATION_DEFAULT_CHANNELS = ["recorder"]
    yield Recorder
    channels._load.cache_clear()


def notify(user, **kwargs):
    kwargs.setdefault("kind", "test.thing")
    kwargs.setdefault("title", "Something happened")
    return services.notify(user, **kwargs)


class TestNotify:
    def test_is_kept(self, user, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            notification = notify(
                user, body="Details", url="/somewhere/", data={"id": 1}
            )
        notification.refresh_from_db()
        assert notification.user == user
        assert (notification.kind, notification.title) == (
            "test.thing",
            "Something happened",
        )
        assert notification.body == "Details"
        assert notification.url == "/somewhere/"
        assert notification.data == {"id": 1}
        assert not notification.is_read

    def test_sent_through_the_log_by_default(
        self, user, caplog, django_capture_on_commit_callbacks
    ):
        caplog.set_level(logging.INFO, logger="apps.notifications")
        with django_capture_on_commit_callbacks(execute=True):
            notification = notify(user)
        notification.refresh_from_db()
        assert notification.sent_via == ["log"]
        assert "Something happened" in caplog.text

    def test_sent_through_preferred_channels(
        self, user, recorder, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            notification = notify(user)
        assert recorder.sent == [notification.pk]

    def test_only_sent_once_committed(
        self, user, recorder, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            notify(user)
        assert recorder.sent == []
        assert len(callbacks) == 1

    def test_not_sent_when_rolled_back(self, user, recorder):
        from django.db import transaction

        class Oops(Exception):
            pass

        with pytest.raises(Oops), transaction.atomic():
            notify(user)
            raise Oops
        assert not Notification.objects.exists()
        assert recorder.sent == []

    def test_queueing_failing_keeps_it(
        self, user, monkeypatch, django_capture_on_commit_callbacks
    ):
        from apps.notifications import tasks

        def broken(*args):
            raise ConnectionError("broker down")

        monkeypatch.setattr(tasks.send_notification, "delay", broken)
        with django_capture_on_commit_callbacks(execute=True):
            notification = notify(user)
        assert Notification.objects.get() == notification


class TestSend:
    def test_one_channel_failing_doesnt_stop_the_others(
        self, user, recorder, settings
    ):
        settings.NOTIFICATION_DEFAULT_CHANNELS = ["broken", "recorder", "log"]
        notification = notify(user)
        assert services.send(notification.pk) == ["recorder", "log"]
        assert recorder.sent == [notification.pk]

    def test_failed_channels_are_tried_again(self, user, recorder, settings):
        settings.NOTIFICATION_DEFAULT_CHANNELS = ["broken", "recorder"]
        notification = notify(user)
        services.send(notification.pk)
        services.send(notification.pk)
        # Sent once through the one that works, tried twice on the other
        assert recorder.sent == [notification.pk]
        notification.refresh_from_db()
        assert notification.sent_via == ["recorder"]

    def test_unknown_channel(self, user, recorder, settings):
        settings.NOTIFICATION_DEFAULT_CHANNELS = ["pigeon", "recorder"]
        notification = notify(user)
        assert services.send(notification.pk) == ["recorder"]

    def test_go_with_the_user(self, user, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            notify(user)
        user.schedule_deletion()
        user._hard_delete()
        assert not Notification.objects.exists()


class TestAuditing:
    def test_sending_is_recorded_for_staff(
        self, user, recorder, settings, django_capture_on_commit_callbacks
    ):
        settings.NOTIFICATION_DEFAULT_CHANNELS = ["broken", "recorder"]
        with django_capture_on_commit_callbacks(execute=True):
            notification = notify(user)
        entry = AuditEntry.objects.get()
        assert entry.action == "notification.sent"
        assert entry.user == user
        assert not entry.visible_to_user
        assert entry.object_id == str(notification.pk)
        assert entry.data == {
            "kind": "test.thing",
            "sent": ["recorder"],
            "failed": ["broken"],
        }
        assert entry.run.task == "apps.notifications.tasks.send_notification"


def test_channel_must_send():
    with pytest.raises(NotImplementedError):
        channels.Channel().send(None)
