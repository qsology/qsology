from datetime import timedelta

import pytest
from apps.accounts.models import User
from apps.accounts.tasks import delete_scheduled_users
from apps.audit.models import AuditEntry
from config.celery import app
from django.conf import settings
from django.utils import timezone

pytestmark = pytest.mark.django_db


class TestDeleteScheduledUsers:
    def test_deletes_users_past_their_deletion_date(self, make_user):
        user = make_user()
        user.schedule_deletion(grace_period=timedelta(days=-1))

        assert delete_scheduled_users() == 1
        assert not User.objects.filter(pk=user.pk).exists()

    def test_keeps_users_still_in_grace_period(self, make_user):
        user = make_user()
        user.schedule_deletion(grace_period=timedelta(days=1))

        assert delete_scheduled_users() == 0
        assert User.objects.filter(pk=user.pk).exists()

    def test_keeps_active_and_deactivated_users(self, make_user):
        active = make_user()
        deactivated = make_user(is_active=False)

        assert delete_scheduled_users() == 0
        remaining = User.objects.filter(pk__in=[active.pk, deactivated.pk])
        assert remaining.count() == 2

    def test_keeps_reactivated_user_with_stale_date(self, make_user):
        user = make_user()
        user.schedule_deletion(grace_period=timedelta(days=-1))
        user.is_active = True
        user.save()

        assert delete_scheduled_users() == 0
        assert User.objects.filter(pk=user.pk).exists()

    def test_keeps_cancelled_deletion(self, make_user):
        user = make_user()
        user.schedule_deletion(grace_period=timedelta(days=-1))
        user.cancel_deletion()

        assert delete_scheduled_users() == 0
        assert User.objects.filter(pk=user.pk).exists()

    def test_only_deletes_expired_users_from_mixed_set(self, make_user):
        expired = [make_user() for _ in range(3)]
        for u in expired:
            u.schedule_deletion(grace_period=timedelta(hours=-1))
        waiting = make_user()
        waiting.schedule_deletion()
        active = make_user()

        assert delete_scheduled_users() == 3
        remaining = set(User.objects.values_list("pk", flat=True))
        assert remaining == {waiting.pk, active.pk}

    def test_user_deleted_once_grace_period_passes(
        self, make_user, monkeypatch
    ):
        user = make_user()
        user.schedule_deletion()
        assert delete_scheduled_users() == 0

        future = timezone.now() + settings.ACCOUNT_DELETION_GRACE_PERIOD
        future += timedelta(seconds=1)
        monkeypatch.setattr(timezone, "now", lambda: future)

        assert delete_scheduled_users() == 1
        assert not User.objects.filter(pk=user.pk).exists()

    def test_runs_via_celery(self, make_user):
        user = make_user()
        user.schedule_deletion(grace_period=timedelta(days=-1))

        result = delete_scheduled_users.delay()

        assert result.get() == 1
        assert not User.objects.filter(pk=user.pk).exists()


class TestBeatSchedule:
    def test_task_is_registered(self):
        assert "apps.accounts.tasks.delete_scheduled_users" in app.tasks

    def test_scheduled_every_24_hours(self):
        entry = app.conf.beat_schedule["delete-scheduled-users"]
        assert entry["task"] == "apps.accounts.tasks.delete_scheduled_users"
        assert entry["schedule"] == timedelta(hours=24)


class TestAuditing:
    def test_deletion_is_kept_for_staff_by_id(self, make_user):
        user = make_user()
        user.schedule_deletion(grace_period=timedelta(days=-1))
        user_pk = user.pk

        delete_scheduled_users.delay()

        entry = AuditEntry.objects.get()
        assert entry.action == "account.deleted"
        assert entry.user is None
        assert entry.user_pk == user_pk
        assert not entry.visible_to_user
        assert entry.run.task == "apps.accounts.tasks.delete_scheduled_users"
