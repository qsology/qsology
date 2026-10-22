import itertools
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from apps.callsigns.models import Callsign, CallsignAllocation
from apps.locations.models import Location, Reference, ReferenceProgram
from apps.logbook import services
from apps.logbook.models import LogSession

# When the test QSOs are made, well after M0WME was allocated
T0 = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def station(db, user):
    """M0WME, held by m0wme since 2020"""
    callsign = Callsign.objects.create(call="M0WME")
    CallsignAllocation.objects.create(
        callsign=callsign,
        user=user,
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
    )
    return callsign


@pytest.fixture
def qso_values():
    """Factory for the values of a QSO with nothing wrong with it"""

    def _values(**overrides):
        values = {
            "call": "K1ABC",
            "time_on": T0,
            "freq": Decimal("14.074"),
            "mode": "FT8",
            "rst_sent": "-10",
            "rst_rcvd": "-12",
            "station_callsign": "M0WME",
        }
        values.update(overrides)
        return values

    return _values


@pytest.fixture
def log(user, station, qso_values):
    """Factory for logging QSOs for m0wme, each 30 minutes after the last
    so they are never duplicates of each other"""
    times = itertools.count()

    def _log(owner=None, accept_warnings=False, **overrides):
        overrides.setdefault(
            "time_on", T0 + timedelta(minutes=30 * next(times))
        )
        return services.log_qso(
            owner or user,
            qso_values(**overrides),
            accept_warnings=accept_warnings,
        )

    return _log


@pytest.fixture
def qso(log):
    return log()


@pytest.fixture
def park(db):
    return Reference.objects.create(
        program=ReferenceProgram.POTA,
        code="US-0001",
        name="Acadia National Park",
        latitude=Decimal("44.31"),
        longitude=Decimal("-68.2034"),
        gridsquare="FN54vh",
        dxcc_code=291,
    )


@pytest.fixture
def location(user, park):
    location = Location.objects.create(
        owner=user,
        name="Acadia",
        latitude=park.latitude,
        longitude=park.longitude,
        gridsquare=park.gridsquare,
    )
    location.references.add(park)
    return location


@pytest.fixture
def contest(user):
    return LogSession.objects.create(
        owner=user,
        name="CQ WW CW 2024",
        contest_id="CQ-WW-CW",
        station_callsign="M0WME",
        uses_serials=True,
        stx_string="14",
        started_at=T0 - timedelta(hours=1),
    )
