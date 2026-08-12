from datetime import timedelta

import pytest
from apps.accounts.models import User
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

pytestmark = pytest.mark.django_db


class TestUserModel:
    def test_custom_user_model_is_active(self):
        assert get_user_model() is User

    def test_create_user(self, user):
        assert user.pk is not None
        assert user.username == "m0wme"
        assert user.display_name == ("m0wme")
        assert user.email == "m0wme@qsology.test"
        assert user.is_active
        assert not user.is_staff
        assert not user.is_superuser
        assert user.check_password("correct-horse-battery-staple")

    def test_create_superuser(self):
        admin = User.objects.create_superuser(
            username="admin", email="admin@qsology.test", password="pw"
        )
        assert admin.is_staff
        assert admin.is_superuser

    def test_display_name_is_optional(self, make_user):
        user = make_user()
        user.full_clean()
        assert user.display_name == ""

    def test_display_name_max_length(self):
        assert User._meta.get_field("display_name").max_length == 150

    def test_new_user_is_not_scheduled_for_deletion(self, user):
        assert user.scheduled_deletion_at is None
        assert not user.is_pending_deletion


class TestIsPendingDeletion:
    @pytest.mark.parametrize(
        ("is_active", "scheduled", "expected"),
        [
            (True, False, False),
            (False, False, False),  # Deactivated but not being deleted
            (True, True, False),  # Scheduled but still active
            (False, True, True),
        ],
    )
    def test_is_pending_deletion(self, user, is_active, scheduled, expected):
        user.is_active = is_active
        user.scheduled_deletion_at = timezone.now() if scheduled else None
        assert user.is_pending_deletion is expected


class TestScheduleDeletion:
    def test_schedule_deletion_marks_user_pending(self, user):
        before = timezone.now()
        user.schedule_deletion()
        user.refresh_from_db()

        assert not user.is_active
        assert user.is_pending_deletion
        grace = settings.ACCOUNT_DELETION_GRACE_PERIOD
        assert before + grace <= user.scheduled_deletion_at
        assert user.scheduled_deletion_at <= timezone.now() + grace

    def test_schedule_deletion_custom_grace_period(self, user):
        before = timezone.now()
        user.schedule_deletion(grace_period=timedelta(days=1))
        user.refresh_from_db()

        assert before + timedelta(days=1) <= user.scheduled_deletion_at
        assert user.scheduled_deletion_at < before + timedelta(days=2)

    def test_scheduled_user_cannot_log_in(self, client, user):
        user.schedule_deletion()
        assert not client.login(
            username="m0wme", password="correct-horse-battery-staple"
        )

    def test_schedule_deletion_does_not_delete(self, user):
        user.schedule_deletion(grace_period=timedelta(0))
        assert User.objects.filter(pk=user.pk).exists()

    def test_cancel_deletion(self, user):
        user.schedule_deletion()
        user.cancel_deletion()
        user.refresh_from_db()

        assert user.is_active
        assert user.scheduled_deletion_at is None
        assert not user.is_pending_deletion


class TestDeletionQuerySet:
    def test_pending_deletion(self, make_user):
        active = make_user()
        deactivated = make_user(is_active=False)
        pending = make_user()
        pending.schedule_deletion()

        result = set(User.objects.pending_deletion())
        assert result == {pending}
        assert active not in result
        assert deactivated not in result

    def test_due_for_deletion_only_includes_expired(self, make_user):
        expired = make_user()
        expired.schedule_deletion(grace_period=timedelta(days=-1))
        in_grace = make_user()
        in_grace.schedule_deletion(grace_period=timedelta(days=1))

        assert list(User.objects.due_for_deletion()) == [expired]

    def test_due_for_deletion_respects_now(self, make_user):
        user = make_user()
        user.schedule_deletion(grace_period=timedelta(days=1))
        later = timezone.now() + timedelta(days=2)

        assert list(User.objects.due_for_deletion(now=later)) == [user]

    def test_due_for_deletion_matches_is_pending_deletion(self, make_user):
        # A user re-activated without clearing the date is no longer pending
        user = make_user()
        user.schedule_deletion(grace_period=timedelta(days=-1))
        user.is_active = True
        user.save()

        assert not user.is_pending_deletion
        assert not User.objects.due_for_deletion().exists()


class TestDeleteUser:
    def test_delete_schedules_instead_of_deleting(self, user):
        before = timezone.now()
        result = user.delete()
        user.refresh_from_db()

        assert result == (0, {})
        assert user.is_pending_deletion
        assert not user.is_active
        grace = settings.ACCOUNT_DELETION_GRACE_PERIOD
        assert user.scheduled_deletion_at >= before + grace

    def test_delete_keeps_existing_schedule(self, user):
        user.schedule_deletion(grace_period=timedelta(days=1))
        scheduled_at = user.scheduled_deletion_at

        user.delete()
        user.refresh_from_db()

        assert user.scheduled_deletion_at == scheduled_at

    def test_delete_then_hard_delete(self, user):
        """Schedule via delete(), then use is_pending_deletion to remove"""
        user.delete()
        user.refresh_from_db()
        assert user.is_pending_deletion

        pk = user.pk
        count, _ = user._hard_delete()

        assert count == 1
        assert not User.objects.filter(pk=pk).exists()

    def test_hard_delete_refuses_user_not_pending(self, user):
        with pytest.raises(ValueError, match="not pending deletion"):
            user._hard_delete()

        assert User.objects.filter(pk=user.pk).exists()

    def test_hard_delete_refuses_reactivated_user(self, user):
        user.schedule_deletion()
        user.is_active = True
        user.save()

        with pytest.raises(ValueError):
            user._hard_delete()

        assert User.objects.filter(pk=user.pk).exists()


class TestQuerySetDelete:
    def test_bulk_delete_schedules_instead_of_deleting(self, make_user):
        users = [make_user() for _ in range(3)]

        result = User.objects.filter(pk__in=[u.pk for u in users]).delete()

        assert result == (0, {})
        assert User.objects.count() == 3
        assert User.objects.pending_deletion().count() == 3

    def test_bulk_delete_keeps_existing_schedule(self, make_user):
        scheduled = make_user()
        scheduled.schedule_deletion(grace_period=timedelta(days=1))
        scheduled_at = scheduled.scheduled_deletion_at
        make_user()

        User.objects.all().delete()
        scheduled.refresh_from_db()

        assert scheduled.scheduled_deletion_at == scheduled_at
        assert User.objects.pending_deletion().count() == 2

    def test_bulk_delete_deactivates_user_with_stale_date(self, make_user):
        user = make_user()
        user.schedule_deletion()
        user.is_active = True
        user.save()

        User.objects.filter(pk=user.pk).delete()
        user.refresh_from_db()

        assert user.is_pending_deletion

    def test_bulk_delete_only_touches_queryset(self, make_user):
        target = make_user()
        other = make_user()

        User.objects.filter(pk=target.pk).delete()
        other.refresh_from_db()

        assert other.is_active
        assert other.scheduled_deletion_at is None

    def test_hard_delete_only_removes_pending_users(self, make_user):
        pending = make_user()
        pending.schedule_deletion()
        active = make_user()

        count, _ = User.objects.all()._hard_delete()

        assert count == 1
        assert list(User.objects.all()) == [active]

    def test_hard_delete_not_on_manager(self):
        assert not hasattr(User.objects, "_hard_delete")
        assert not hasattr(User.objects, "delete")
