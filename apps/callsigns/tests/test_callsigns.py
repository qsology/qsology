import pytest
from apps.callsigns.models import Callsign
from django.core.exceptions import ValidationError
from django.db import IntegrityError

pytestmark = pytest.mark.django_db


class TestCallsign:
    def test_create(self, callsign):
        assert callsign.pk is not None
        assert str(callsign) == "M0WME"

    @pytest.mark.parametrize("value", ["m0wme", "M0wMe", " m0wme "])
    def test_normalized_to_uppercase(self, value):
        callsign = Callsign.objects.create(call=value)
        callsign.refresh_from_db()
        assert callsign.call == "M0WME"

    def test_unique_regardless_of_case(self, callsign):
        with pytest.raises(ValidationError):
            Callsign.objects.create(call="m0wme")

    @pytest.mark.parametrize(
        "value", ["", "MWME", "1234", "M0-WME", "ZL/M0WME", "M0WME/P"]
    )
    def test_invalid(self, value):
        with pytest.raises(ValidationError):
            Callsign.objects.create(call=value)

    def test_uppercase_enforced_by_database(self, callsign):
        # update() skips save(), the check constraint still catches it
        with pytest.raises(IntegrityError):
            Callsign.objects.filter(pk=callsign.pk).update(call="m0wme")

    def test_not_tied_to_a_user(self, callsign):
        assert callsign.holder_at() is None
        assert callsign.allocations.count() == 0

    def test_held_by(self, allocate, user, other_user):
        mine = Callsign.objects.create(call="M0WME")
        theirs = Callsign.objects.create(call="K4GBI")
        allocate(mine, user)
        allocate(theirs, other_user)

        assert list(Callsign.objects.held_by(user)) == [mine]
        assert list(Callsign.objects.held_by(other_user)) == [theirs]

    def test_user_with_no_callsigns(self, user):
        assert not Callsign.objects.held_by(user).exists()

    def test_user_with_many_callsigns(self, allocate, user):
        calls = [
            Callsign.objects.create(call=call)
            for call in ("M0WME", "2E0WME", "GB2WME")
        ]
        for callsign in calls:
            allocate(callsign, user)

        assert set(Callsign.objects.held_by(user)) == set(calls)
