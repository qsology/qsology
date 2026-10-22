"""Sanity checks, each warning on its own"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from apps.callsigns.roles import CallsignRole
from apps.logbook import sanity
from apps.logbook.models import QSO, LogSession
from apps.logbook.sanity import Code
from django.utils import timezone

from .conftest import T0

pytestmark = pytest.mark.django_db


@pytest.fixture
def build(user, station, qso_values):
    """An unsaved, normalized QSO with nothing wrong with it"""

    def _build(owner=None, **overrides):
        qso = QSO(owner=owner or user, **qso_values(**overrides))
        qso.normalize()
        return qso

    return _build


def codes(qso) -> list[str]:
    return [issue.code for issue in sanity.check(qso)]


def test_a_good_qso_has_no_issues(build):
    assert codes(build()) == []


class TestCallsigns:
    @pytest.mark.parametrize(
        "call", ["K1ABC", "M0WME/P", "VP2E/K1ABC", "4X4AA"]
    )
    def test_good_calls(self, build, call):
        assert Code.CALL_FORMAT not in codes(build(call=call))

    @pytest.mark.parametrize("call", ["ABCDEF", "12345", "K1-ABC", "K1//A"])
    def test_odd_calls(self, build, call):
        assert Code.CALL_FORMAT in codes(build(call=call))

    def test_no_station_callsign(self, build):
        assert codes(build(station_callsign="")) == [
            Code.STATION_CALLSIGN_MISSING
        ]

    def test_odd_station_callsign(self, build):
        assert codes(build(station_callsign="NOTACALL")) == [
            Code.STATION_CALLSIGN_FORMAT
        ]

    def test_portable_station_callsign_is_held(self, build):
        assert codes(build(station_callsign="ZL/M0WME/P")) == []

    def test_unknown_station_callsign(self, build):
        assert codes(build(station_callsign="G4XYZ")) == [
            Code.STATION_CALLSIGN_NOT_HELD
        ]

    def test_someone_elses_station_callsign(self, build, other_user):
        assert codes(build(owner=other_user)) == [
            Code.STATION_CALLSIGN_NOT_HELD
        ]

    def test_before_the_callsign_was_held(self, build):
        old = datetime(2019, 12, 31, tzinfo=UTC)
        assert codes(build(time_on=old)) == [Code.STATION_CALLSIGN_NOT_HELD]

    @pytest.mark.parametrize(
        ("role", "allowed"),
        [
            (CallsignRole.VIEWER, False),
            (CallsignRole.LOGGER, True),
            (CallsignRole.MANAGER, True),
        ],
    )
    def test_shared_callsign(self, build, station, other_user, role, allowed):
        station.allocations.get().share_with(other_user, role)
        issues = codes(build(owner=other_user))
        assert (Code.STATION_CALLSIGN_NOT_HELD not in issues) is allowed


class TestBand:
    def test_neither_band_nor_freq(self, build):
        assert codes(build(freq=None)) == [Code.BAND_FREQ_MISSING]

    def test_band_without_freq_is_fine(self, build):
        assert codes(build(freq=None, band="20m")) == []

    def test_unknown_band(self, build):
        assert codes(build(freq=None, band="21m")) == [Code.BAND_UNKNOWN]

    def test_freq_out_of_band(self, build):
        assert codes(build(freq=Decimal("14.5"))) == [Code.FREQ_OUT_OF_BAND]

    def test_band_and_freq_disagree(self, build):
        qso = build(freq=Decimal("7.074"), band="20m")
        assert codes(qso) == [Code.BAND_FREQ_MISMATCH]

    def test_rx_band_and_freq_disagree(self, build):
        qso = build(freq_rx=Decimal("7.074"), band_rx="20m")
        issues = sanity.check(qso)
        assert [(issue.code, issue.field) for issue in issues] == [
            (Code.BAND_FREQ_MISMATCH, "freq_rx")
        ]

    @pytest.mark.parametrize(
        ("freq", "band"),
        [
            ("0.1360", "2190m"),
            ("1.8", "160m"),
            ("2.0", "160m"),
            ("5.3515", "60m"),
            ("14.35", "20m"),
            ("50.313", "6m"),
            ("144.3", "2m"),
            ("10368.1", "3cm"),
        ],
    )
    def test_band_edges(self, build, freq, band):
        qso = build(freq=Decimal(freq))
        assert qso.band == band
        assert codes(qso) == []


class TestMode:
    def test_no_mode(self, build):
        assert codes(build(mode="")) == [Code.MODE_MISSING]

    def test_submode_without_mode(self, build):
        issues = sanity.check(build(mode="", submode="USB"))
        assert issues[0].code == Code.MODE_MISSING
        assert "SSB" in issues[0].message

    def test_unknown_mode(self, build):
        assert codes(build(mode="BANANA")) == [Code.MODE_UNKNOWN]

    def test_submode_given_as_mode(self, build):
        issues = sanity.check(build(mode="FT4"))
        assert issues[0].code == Code.MODE_UNKNOWN
        assert "MFSK" in issues[0].message

    @pytest.mark.parametrize(
        ("mode", "submode"),
        [("SSB", "USB"), ("MFSK", "FT4"), ("OLIVIA", "OLIVIA 8/250")],
    )
    def test_good_submodes(self, build, mode, submode):
        assert codes(build(mode=mode, submode=submode, rst_sent="59")) == []

    def test_submode_of_another_mode(self, build):
        assert codes(build(mode="SSB", submode="FT4")) == [
            Code.SUBMODE_MISMATCH
        ]


class TestTimes:
    def test_in_the_future(self, build):
        later = timezone.now() + timedelta(hours=1)
        assert codes(build(time_on=later)) == [Code.TIME_IN_FUTURE]

    def test_slightly_fast_clock_is_fine(self, build):
        soon = timezone.now() + timedelta(minutes=5)
        assert codes(build(time_on=soon)) == []

    def test_too_early(self, build, station):
        station.allocations.update(valid_from=datetime(1, 1, 1, tzinfo=UTC))
        old = datetime(1899, 12, 31, tzinfo=UTC)
        assert codes(build(time_on=old)) == [Code.TIME_TOO_EARLY]

    def test_ends_before_it_starts(self, build):
        qso = build(time_off=T0 - timedelta(minutes=1))
        assert codes(qso) == [Code.TIME_OFF_BEFORE_ON]

    def test_after_session_ended(self, build, user):
        session = LogSession.objects.create(
            owner=user,
            name="Evening",
            started_at=T0 - timedelta(hours=2),
            ended_at=T0 - timedelta(hours=1),
        )
        assert codes(build(session=session)) == [Code.SESSION_ENDED]


class TestFormats:
    @pytest.mark.parametrize("grid", ["ZZ99", "FN3", "IO91wm1"])
    def test_bad_grid(self, build, grid):
        assert codes(build(gridsquare=grid)) == [Code.GRIDSQUARE_INVALID]
        assert codes(build(my_gridsquare=grid)) == [Code.GRIDSQUARE_INVALID]

    @pytest.mark.parametrize(
        "rst",
        ["59", "599", "579", "339", "59+20", "59+10dB", "599K", "-10", "+05"],
    )
    def test_good_reports(self, build, rst):
        assert codes(build(rst_sent=rst, rst_rcvd=rst)) == []

    @pytest.mark.parametrize("rst", ["69", "5", "0", "five nine", "-100"])
    def test_bad_reports(self, build, rst):
        assert Code.RST_INVALID in codes(build(rst_sent=rst))

    def test_unreadable_values(self, build):
        qso = build(extra={"FREQ": "fourteen", "QSLMSG": "TNX"})
        issues = sanity.check(qso)
        assert [(i.code, i.field) for i in issues] == [
            (Code.UNREADABLE_VALUE, "freq")
        ]


class TestDuplicates:
    def test_same_call_band_mode_close_together(self, build, log):
        log()
        later = T0 + timedelta(minutes=9)
        assert codes(build(time_on=later)) == [Code.DUPLICATE]

    def test_far_enough_apart(self, build, log):
        log(time_on=T0)
        assert codes(build(time_on=T0 + timedelta(minutes=11))) == []

    @pytest.mark.parametrize(
        "change",
        [
            {"call": "K2ABC"},
            {"freq": Decimal("7.074")},
            {"mode": "CW", "rst_sent": "599", "rst_rcvd": "599"},
        ],
    )
    def test_different_contact(self, build, log, change):
        log(time_on=T0)
        assert codes(build(**change)) == []

    def test_not_a_duplicate_of_itself(self, log):
        qso = log(time_on=T0)
        assert codes(qso) == []

    def test_deleted_qsos_dont_count(self, build, log):
        log(time_on=T0).delete()
        assert codes(build()) == []

    def test_other_users_dont_count(self, build, other_user, log):
        log(time_on=T0, owner=other_user, accept_warnings=True)
        assert codes(build()) == []


def test_issue_round_trips_through_json():
    issue = sanity.Issue("duplicate", "message", "call")
    assert sanity.Issue.from_dict(issue.as_dict()) == issue
