from datetime import UTC, datetime, timedelta

import pytest
from apps.callsigns.models import Callsign, CallsignAllocation
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

pytestmark = pytest.mark.django_db

JAN_2020 = datetime(2020, 1, 1, tzinfo=UTC)
JAN_2021 = datetime(2021, 1, 1, tzinfo=UTC)
JAN_2022 = datetime(2022, 1, 1, tzinfo=UTC)


class TestAllocation:
    def test_indefinite(self, allocation):
        assert allocation.valid_until is None
        assert allocation.is_indefinite
        assert allocation.is_active()
        assert allocation.is_active(JAN_2020 + timedelta(days=36500))

    def test_valid_from_defaults_to_now(self, callsign, user):
        before = timezone.now()
        allocation = CallsignAllocation.objects.create(
            callsign=callsign, user=user
        )
        assert before <= allocation.valid_from <= timezone.now()

    def test_fixed_period(self, allocate, callsign, user):
        allocation = allocate(callsign, user, JAN_2020, JAN_2021)

        assert not allocation.is_indefinite
        assert not allocation.is_active(JAN_2020 - timedelta(seconds=1))
        assert allocation.is_active(JAN_2020)
        assert allocation.is_active(JAN_2021 - timedelta(seconds=1))
        # valid_until is exclusive
        assert not allocation.is_active(JAN_2021)

    @pytest.mark.parametrize("offset", [timedelta(0), timedelta(days=-1)])
    def test_valid_until_must_be_after_valid_from(
        self, allocate, callsign, user, offset
    ):
        with pytest.raises(ValidationError):
            allocate(callsign, user, JAN_2020, JAN_2020 + offset)

    def test_valid_until_enforced_by_database(self, allocation):
        with pytest.raises(IntegrityError):
            CallsignAllocation.objects.filter(pk=allocation.pk).update(
                valid_until=JAN_2020 - timedelta(days=1)
            )

    def test_str(self, allocate, callsign, user):
        assert str(allocate(callsign, user)).startswith("M0WME (2020-01-01")


class TestOverlap:
    def test_cannot_overlap_another_user(
        self, allocate, callsign, user, other_user
    ):
        allocate(callsign, user, JAN_2020, JAN_2022)
        with pytest.raises(ValidationError, match="already allocated"):
            allocate(callsign, other_user, JAN_2021)

    def test_cannot_overlap_same_user(self, allocate, callsign, user):
        allocate(callsign, user, JAN_2020, JAN_2022)
        with pytest.raises(ValidationError, match="already allocated"):
            allocate(callsign, user, JAN_2021, JAN_2022)

    def test_cannot_start_inside_indefinite(
        self, allocate, callsign, user, other_user
    ):
        allocate(callsign, user, JAN_2020)
        with pytest.raises(ValidationError):
            allocate(callsign, other_user, JAN_2022, JAN_2022 + timedelta(1))

    def test_indefinite_cannot_cover_later_allocation(
        self, allocate, callsign, user, other_user
    ):
        allocate(callsign, other_user, JAN_2022, JAN_2022 + timedelta(1))
        with pytest.raises(ValidationError):
            allocate(callsign, user, JAN_2020)

    def test_cannot_surround_another(
        self, allocate, callsign, user, other_user
    ):
        allocate(callsign, user, JAN_2021, JAN_2021 + timedelta(days=1))
        with pytest.raises(ValidationError):
            allocate(callsign, other_user, JAN_2020, JAN_2022)

    def test_back_to_back_allowed(self, allocate, callsign, user, other_user):
        allocate(callsign, user, JAN_2020, JAN_2021)
        allocate(callsign, other_user, JAN_2021)
        assert callsign.allocations.count() == 2

    def test_gap_allowed(self, allocate, callsign, user, other_user):
        allocate(callsign, user, JAN_2020, JAN_2021)
        allocate(callsign, other_user, JAN_2022)
        assert callsign.allocations.count() == 2

    def test_different_callsigns_can_overlap(self, allocate, user):
        first = Callsign.objects.create(call="M0WME")
        second = Callsign.objects.create(call="2E0WME")
        allocate(first, user)
        allocate(second, user)
        assert user.callsign_allocations.count() == 2

    def test_updating_itself_is_not_an_overlap(self, allocation):
        allocation.valid_until = JAN_2022
        allocation.save()
        allocation.refresh_from_db()
        assert allocation.valid_until == JAN_2022


class TestRenewAndEnd:
    def test_renew(self, allocate, callsign, user):
        allocation = allocate(callsign, user, JAN_2020, JAN_2021)
        allocation.renew(JAN_2022)
        allocation.refresh_from_db()

        assert allocation.valid_until == JAN_2022
        assert allocation.is_active(JAN_2021)

    def test_renew_indefinitely(self, allocate, callsign, user):
        allocation = allocate(callsign, user, JAN_2020, JAN_2021)
        allocation.renew(None)
        assert allocation.is_indefinite

    def test_renew_cannot_overlap_next_holder(
        self, allocate, callsign, user, other_user
    ):
        allocation = allocate(callsign, user, JAN_2020, JAN_2021)
        allocate(callsign, other_user, JAN_2021)

        with pytest.raises(ValidationError):
            allocation.renew(JAN_2022)

    def test_end(self, allocation):
        allocation.end(JAN_2021)
        allocation.refresh_from_db()

        assert allocation.valid_until == JAN_2021
        assert not allocation.is_active()

    def test_end_defaults_to_now(self, allocation):
        allocation.end()
        assert not allocation.is_active()
        assert allocation.valid_until <= timezone.now()


class TestActive:
    def test_active(self, allocate, user):
        current = allocate(Callsign.objects.create(call="M0WME"), user)
        expired = allocate(
            Callsign.objects.create(call="2E0WME"), user, JAN_2020, JAN_2021
        )
        future = allocate(
            Callsign.objects.create(call="GB2WME"),
            user,
            timezone.now() + timedelta(days=1),
        )

        assert list(CallsignAllocation.objects.active()) == [current]
        assert set(CallsignAllocation.objects.active(JAN_2020)) == {
            current,
            expired,
        }
        assert future not in CallsignAllocation.objects.active()


class TestPrimary:
    def test_defaults_to_secondary(self, allocation):
        assert not allocation.is_primary

    def test_one_primary_at_a_time(self, allocate, user):
        allocate(Callsign.objects.create(call="M0WME"), user, is_primary=True)
        with pytest.raises(ValidationError, match="primary"):
            allocate(
                Callsign.objects.create(call="2E0WME"), user, is_primary=True
            )

    def test_primary_can_change_over_time(self, allocate, user):
        old = allocate(
            Callsign.objects.create(call="2E0WME"),
            user,
            JAN_2020,
            JAN_2021,
            is_primary=True,
        )
        new = allocate(
            Callsign.objects.create(call="M0WME"),
            user,
            JAN_2021,
            is_primary=True,
        )
        primary = CallsignAllocation.objects.primary().filter(user=user)

        assert list(primary.active(JAN_2020)) == [old]
        assert list(primary.active(JAN_2022)) == [new]

    def test_different_users_each_have_a_primary(
        self, allocate, user, other_user
    ):
        allocate(Callsign.objects.create(call="M0WME"), user, is_primary=True)
        allocate(
            Callsign.objects.create(call="K4GBI"), other_user, is_primary=True
        )
        assert CallsignAllocation.objects.primary().count() == 2

    def test_make_primary(self, allocate, user):
        first = allocate(
            Callsign.objects.create(call="M0WME"), user, is_primary=True
        )
        second = allocate(Callsign.objects.create(call="2E0WME"), user)

        second.make_primary()
        first.refresh_from_db()
        second.refresh_from_db()

        assert second.is_primary
        assert not first.is_primary

    def test_make_primary_leaves_earlier_primary(self, allocate, user):
        old = allocate(
            Callsign.objects.create(call="2E0WME"),
            user,
            JAN_2020,
            JAN_2021,
            is_primary=True,
        )
        new = allocate(Callsign.objects.create(call="M0WME"), user, JAN_2021)

        new.make_primary()
        old.refresh_from_db()

        assert old.is_primary

    def test_make_primary_leaves_other_users(self, allocate, user, other_user):
        theirs = allocate(
            Callsign.objects.create(call="K4GBI"), other_user, is_primary=True
        )
        mine = allocate(Callsign.objects.create(call="M0WME"), user)

        mine.make_primary()
        theirs.refresh_from_db()

        assert theirs.is_primary
