from datetime import UTC, datetime

import pytest
from apps.callsigns.models import Callsign, CallsignAllocation


@pytest.fixture
def callsign(db):
    return Callsign.objects.create(call="M0WME")


@pytest.fixture
def allocate(db):
    """Factory for allocating a callsign to a user"""

    def _allocate(callsign, user, valid_from=None, valid_until=None, **kw):
        if valid_from is None:
            valid_from = datetime(2020, 1, 1, tzinfo=UTC)
        return CallsignAllocation.objects.create(
            callsign=callsign,
            user=user,
            valid_from=valid_from,
            valid_until=valid_until,
            **kw,
        )

    return _allocate


@pytest.fixture
def allocation(allocate, callsign, user):
    """M0WME held indefinitely by m0wme"""
    return allocate(callsign, user)
