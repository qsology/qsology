"""Who holds a callsign, and who can see its data, over time"""

from datetime import UTC, datetime, timedelta

import pytest
from apps.callsigns.models import (
    CallsignAllocation,
    CallsignShare,
    CallsignVariant,
)
from apps.callsigns.roles import CallsignRole
from django.core.exceptions import ValidationError

pytestmark = pytest.mark.django_db

# M0WME is held by m0wme until 01:00, then by k4gbi from 01:01
HANDOVER_END = datetime(2020, 1, 1, 1, 0, 0, tzinfo=UTC)
HANDOVER_START = datetime(2020, 1, 1, 1, 1, 0, tzinfo=UTC)
BEFORE = HANDOVER_END - timedelta(days=30)
GAP = HANDOVER_END + timedelta(seconds=30)
AFTER = HANDOVER_START + timedelta(days=30)


@pytest.fixture
def handover(allocate, callsign, user, other_user):
    first = allocate(
        callsign, user, HANDOVER_END - timedelta(days=365), HANDOVER_END
    )
    second = allocate(callsign, other_user, HANDOVER_START)
    return first, second


class TestHandover:
    def test_holder_at(self, handover, callsign, user, other_user):
        assert callsign.holder_at(BEFORE) == user
        assert callsign.holder_at(HANDOVER_END - timedelta(seconds=1)) == user
        assert callsign.holder_at(HANDOVER_END) is None
        assert callsign.holder_at(GAP) is None
        assert callsign.holder_at(HANDOVER_START) == other_user
        assert callsign.holder_at(AFTER) == other_user
        assert callsign.holder_at() == other_user

    def test_role_for_previous_holder(self, handover, callsign, user):
        assert callsign.role_for(user, BEFORE) == CallsignRole.OWNER
        assert callsign.role_for(user, AFTER) is None

    def test_role_for_new_holder(self, handover, callsign, other_user):
        assert callsign.role_for(other_user, BEFORE) is None
        assert callsign.role_for(other_user, AFTER) == CallsignRole.OWNER

    def test_nobody_has_a_role_in_the_gap(
        self, handover, callsign, user, other_user
    ):
        assert callsign.role_for(user, GAP) is None
        assert callsign.role_for(other_user, GAP) is None

    def test_variants_follow_the_callsign(
        self, handover, callsign, user, other_user
    ):
        variant = callsign.get_variant(prefix="ZL", suffix="P")

        assert variant.holder_at(BEFORE) == user
        assert variant.holder_at(AFTER) == other_user
        assert variant.role_for(user, BEFORE) == CallsignRole.OWNER
        assert variant.role_for(user, AFTER) is None
        assert variant.role_for(other_user, AFTER) == CallsignRole.OWNER

    def test_each_user_only_gets_their_own_period(
        self, handover, user, other_user
    ):
        first, second = handover
        assert list(CallsignAllocation.objects.accessible_to(user)) == [first]
        assert list(CallsignAllocation.objects.accessible_to(other_user)) == [
            second
        ]

    def test_previous_holder_keeps_history_after_handover(
        self, handover, callsign, user
    ):
        # m0wme no longer holds M0WME, but their period is still theirs
        assert callsign.holder_at() != user
        accessible = CallsignAllocation.objects.accessible_to(user)
        assert accessible.active(BEFORE).filter(callsign=callsign).exists()
        assert not accessible.active(AFTER).exists()

    def test_deleting_previous_holder_keeps_callsign(
        self, handover, callsign, user, other_user
    ):
        first, _ = handover
        user.schedule_deletion(grace_period=timedelta(0))
        user._hard_delete()

        assert not CallsignAllocation.objects.filter(pk=first.pk).exists()
        assert callsign.holder_at() == other_user
        assert callsign.holder_at(BEFORE) is None


class TestSharing:
    def test_share_with(self, allocation, other_user):
        share = allocation.share_with(other_user, CallsignRole.MANAGER)

        assert share.role == CallsignRole.MANAGER
        assert allocation.role_for(other_user) == CallsignRole.MANAGER

    def test_share_with_changes_role(self, allocation, other_user):
        allocation.share_with(other_user, CallsignRole.VIEWER)
        allocation.share_with(other_user, CallsignRole.LOGGER)

        assert allocation.shares.count() == 1
        assert allocation.role_for(other_user) == CallsignRole.LOGGER

    def test_holder_is_owner(self, allocation, user):
        assert allocation.role_for(user) == CallsignRole.OWNER

    def test_unshared_user_has_no_role(self, allocation, make_user):
        assert allocation.role_for(make_user()) is None

    def test_cannot_share_with_holder(self, allocation, user):
        with pytest.raises(ValidationError, match="holder"):
            allocation.share_with(user, CallsignRole.VIEWER)

    def test_default_role_is_viewer(self, allocation, other_user):
        share = CallsignShare.objects.create(
            allocation=allocation, user=other_user
        )
        assert share.role == CallsignRole.VIEWER

    def test_invalid_role(self, allocation, other_user):
        with pytest.raises(ValidationError):
            allocation.share_with(other_user, "admin")

    def test_one_share_per_user(self, allocation, other_user):
        allocation.share_with(other_user, CallsignRole.VIEWER)
        with pytest.raises(ValidationError):
            CallsignShare.objects.create(allocation=allocation, user=other_user)

    def test_qsl_manager(self, allocation, callsign, other_user):
        """k4gbi as QSL manager for M0WME, including its variants"""
        allocation.share_with(other_user, CallsignRole.MANAGER)
        variant = callsign.get_variant(suffix="P")

        assert callsign.role_for(other_user) == CallsignRole.MANAGER
        assert variant.role_for(other_user) == CallsignRole.MANAGER

    def test_shares_end_with_the_allocation(
        self, handover, callsign, make_user
    ):
        first, _ = handover
        manager = make_user()
        first.share_with(manager, CallsignRole.MANAGER)

        assert callsign.role_for(manager, BEFORE) == CallsignRole.MANAGER
        assert callsign.role_for(manager, AFTER) is None

    def test_shares_not_passed_to_next_holder(self, handover, callsign, user):
        _, second = handover
        assert not second.shares.exists()
        assert callsign.role_for(user, AFTER) is None

    def test_str(self, allocation, other_user):
        share = allocation.share_with(other_user, CallsignRole.LOGGER)
        assert str(share) == "M0WME shared with k4gbi (logger)"


class TestAccessibleTo:
    def test_holder(self, allocation, user):
        assert list(CallsignAllocation.objects.accessible_to(user)) == [
            allocation
        ]

    def test_no_access(self, allocation, other_user):
        assert not CallsignAllocation.objects.accessible_to(other_user).exists()

    @pytest.mark.parametrize(
        ("role", "minimum", "expected"),
        [
            (CallsignRole.VIEWER, CallsignRole.VIEWER, True),
            (CallsignRole.VIEWER, CallsignRole.LOGGER, False),
            (CallsignRole.LOGGER, CallsignRole.LOGGER, True),
            (CallsignRole.MANAGER, CallsignRole.LOGGER, True),
            (CallsignRole.MANAGER, CallsignRole.OWNER, False),
            (CallsignRole.OWNER, CallsignRole.OWNER, True),
        ],
    )
    def test_shared_minimum_role(
        self, allocation, other_user, role, minimum, expected
    ):
        allocation.share_with(other_user, role)
        accessible = CallsignAllocation.objects.accessible_to(
            other_user, minimum
        )
        assert accessible.exists() is expected

    def test_holder_always_meets_minimum(self, allocation, user):
        assert CallsignAllocation.objects.accessible_to(
            user, CallsignRole.OWNER
        ).exists()

    def test_no_duplicates_with_many_shares(
        self, allocation, other_user, make_user
    ):
        allocation.share_with(other_user, CallsignRole.VIEWER)
        allocation.share_with(make_user(), CallsignRole.VIEWER)

        assert CallsignAllocation.objects.accessible_to(other_user).count() == 1


class TestVariantsAcrossUsers:
    def test_variants_shared_between_holders(
        self, handover, callsign, user, other_user
    ):
        """The same variant row is used by both holders, ownership comes
        from the time it was used"""
        variant = callsign.get_variant(suffix="P")

        assert CallsignVariant.objects.lookup("M0WME/P").get() == variant
        assert variant.holder_at(BEFORE) == user
        assert variant.holder_at(AFTER) == other_user
