"""Sanity checks run on every QSO

Problems found are warnings, never errors: a warning stops a QSO being logged
unless the user accepts it, and an accepted QSO is flagged for review rather
than being lost. Hard errors, things the log can't hold at all such as a
missing callsign, are left to model validation"""

import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from apps.callsigns import roles
from apps.callsigns.models import Callsign, parse_callsign
from apps.callsigns.roles import CallsignRole
from apps.locations import maidenhead
from apps.logbook import bands, fields
from django.utils import timezone

# Allow for the clock on the logging PC being a little out
FUTURE_TOLERANCE = timedelta(minutes=10)
EARLIEST_QSO = datetime(1900, 1, 1, tzinfo=UTC)
DUPLICATE_WINDOW = timedelta(minutes=10)

_CALL_RE = re.compile(r"^(?=.*[A-Z])(?=.*[0-9])[A-Z0-9/]+$")
# RST, optionally with a suffix or dB over S9, e.g. 59, 599K, 59+20, or a
# signed report in dB as the WSJT-X modes use, e.g. -10, +05
_RST_RE = re.compile(
    r"^(?:[1-5][1-9][1-9]?[A-Z]?(?:\+\d{1,2}(?:DB)?)?|[+-]\d{1,2})$",
    re.IGNORECASE,
)


class Code:
    CALL_FORMAT = "call_format"
    STATION_CALLSIGN_MISSING = "station_callsign_missing"
    STATION_CALLSIGN_FORMAT = "station_callsign_format"
    STATION_CALLSIGN_NOT_HELD = "station_callsign_not_held"
    BAND_FREQ_MISSING = "band_freq_missing"
    BAND_UNKNOWN = "band_unknown"
    FREQ_OUT_OF_BAND = "freq_out_of_band"
    BAND_FREQ_MISMATCH = "band_freq_mismatch"
    MODE_MISSING = "mode_missing"
    MODE_UNKNOWN = "mode_unknown"
    SUBMODE_MISMATCH = "submode_mismatch"
    TIME_IN_FUTURE = "time_in_future"
    TIME_TOO_EARLY = "time_too_early"
    TIME_OFF_BEFORE_ON = "time_off_before_on"
    GRIDSQUARE_INVALID = "gridsquare_invalid"
    RST_INVALID = "rst_invalid"
    DUPLICATE = "duplicate"
    SESSION_ENDED = "session_ended"
    UNREADABLE_VALUE = "unreadable_value"


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    field: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Issue:
        return cls(**data)


def _check_callsign(value: str, field: str, code: str) -> list[Issue]:
    if not value:
        return []
    if not _CALL_RE.match(value):
        return [Issue(code, f"{value} doesn't look like a callsign", field)]
    try:
        parse_callsign(value)
    except ValueError:
        return [Issue(code, f"{value} doesn't look like a callsign", field)]
    return []


def check_station(qso) -> list[Issue]:
    if not qso.station_callsign:
        return [
            Issue(
                Code.STATION_CALLSIGN_MISSING,
                "No station callsign, which callsign was used?",
                "station_callsign",
            )
        ]
    issues = _check_callsign(
        qso.station_callsign,
        "station_callsign",
        Code.STATION_CALLSIGN_FORMAT,
    )
    if issues or qso.time_on is None or qso.owner_id is None:
        return issues
    base = parse_callsign(qso.station_callsign).call
    callsign = Callsign.objects.filter(call=base).first()
    role = callsign.role_for(qso.owner, qso.time_on) if callsign else None
    if not roles.role_at_least(role, CallsignRole.LOGGER):
        issues.append(
            Issue(
                Code.STATION_CALLSIGN_NOT_HELD,
                f"{base} wasn't allocated to you when this contact was made",
                "station_callsign",
            )
        )
    return issues


def check_band(qso) -> list[Issue]:
    issues = []
    if not qso.band and qso.freq is None:
        issues.append(
            Issue(Code.BAND_FREQ_MISSING, "No band or frequency", "band")
        )
    for band_field, freq_field in (("band", "freq"), ("band_rx", "freq_rx")):
        band = getattr(qso, band_field)
        freq = getattr(qso, freq_field)
        if band and band not in bands.BANDS_BY_NAME:
            issues.append(
                Issue(Code.BAND_UNKNOWN, f"{band} isn't a band", band_field)
            )
        if freq is None:
            continue
        freq_band = bands.band_for_freq(freq)
        if freq_band is None:
            issues.append(
                Issue(
                    Code.FREQ_OUT_OF_BAND,
                    f"{fields.format_decimal(freq)} MHz isn't in a band",
                    freq_field,
                )
            )
        elif band and band in bands.BANDS_BY_NAME and band != freq_band:
            issues.append(
                Issue(
                    Code.BAND_FREQ_MISMATCH,
                    f"{fields.format_decimal(freq)} MHz is on {freq_band}, "
                    f"not {band}",
                    freq_field,
                )
            )
    return issues


def check_mode(qso) -> list[Issue]:
    if not qso.mode:
        if qso.submode in bands.SUBMODE_TO_MODE:
            mode = bands.SUBMODE_TO_MODE[qso.submode]
            message = f"No mode, {qso.submode} should be logged as {mode}"
        else:
            message = "No mode"
        return [Issue(Code.MODE_MISSING, message, "mode")]
    if qso.mode not in bands.MODES:
        if qso.mode in bands.SUBMODE_TO_MODE:
            mode = bands.SUBMODE_TO_MODE[qso.mode]
            message = f"{qso.mode} is a submode of {mode}"
        else:
            message = f"{qso.mode} isn't a known mode"
        return [Issue(Code.MODE_UNKNOWN, message, "mode")]
    if qso.submode and qso.submode not in bands.MODES[qso.mode]:
        return [
            Issue(
                Code.SUBMODE_MISMATCH,
                f"{qso.submode} isn't a submode of {qso.mode}",
                "submode",
            )
        ]
    return []


def check_times(qso, now: datetime | None = None) -> list[Issue]:
    if qso.time_on is None:
        return []
    now = now or timezone.now()
    issues = []
    if qso.time_on > now + FUTURE_TOLERANCE:
        issues.append(
            Issue(
                Code.TIME_IN_FUTURE, "The contact is in the future", "time_on"
            )
        )
    if qso.time_on < EARLIEST_QSO:
        issues.append(
            Issue(
                Code.TIME_TOO_EARLY,
                "The contact is from before 1900",
                "time_on",
            )
        )
    if qso.time_off is not None and qso.time_off < qso.time_on:
        issues.append(
            Issue(
                Code.TIME_OFF_BEFORE_ON,
                "The contact ends before it starts",
                "time_off",
            )
        )
    if (
        qso.session_id is not None
        and qso.session.ended_at is not None
        and qso.time_on > qso.session.ended_at
    ):
        issues.append(
            Issue(
                Code.SESSION_ENDED,
                f"The session {qso.session} had already ended",
                "time_on",
            )
        )
    return issues


def check_formats(qso) -> list[Issue]:
    issues = _check_callsign(qso.call, "call", Code.CALL_FORMAT)
    for field in ("gridsquare", "my_gridsquare"):
        value = getattr(qso, field)
        if value and not maidenhead.is_valid_grid(value):
            issues.append(
                Issue(
                    Code.GRIDSQUARE_INVALID,
                    f"{value} isn't a valid grid square",
                    field,
                )
            )
    for field in ("rst_sent", "rst_rcvd"):
        value = getattr(qso, field)
        if value and not _RST_RE.match(value):
            issues.append(
                Issue(Code.RST_INVALID, f"{value} isn't a valid report", field)
            )
    for key in sorted(qso.extra or {}):
        if key in fields.COLUMN_ADIF_NAMES:
            issues.append(
                Issue(
                    Code.UNREADABLE_VALUE,
                    f"{key} {qso.extra[key]!r} couldn't be read, it has been "
                    "kept as it was",
                    key.lower(),
                )
            )
    return issues


def check_duplicate(qso) -> list[Issue]:
    if qso.owner_id is None or qso.time_on is None or not qso.call:
        return []
    others = qso.__class__.objects.filter(
        owner_id=qso.owner_id,
        call=qso.call,
        band=qso.band,
        mode=qso.mode,
        time_on__gte=qso.time_on - DUPLICATE_WINDOW,
        time_on__lte=qso.time_on + DUPLICATE_WINDOW,
    )
    if qso.pk is not None:
        others = others.exclude(pk=qso.pk)
    if others.exists():
        return [
            Issue(
                Code.DUPLICATE,
                f"{qso.call} was already logged on {qso.band or 'this band'} "
                f"{qso.mode} within {DUPLICATE_WINDOW.seconds // 60} minutes",
                "call",
            )
        ]
    return []


CHECKS = (
    check_formats,
    check_station,
    check_band,
    check_mode,
    check_times,
    check_duplicate,
)


def check(qso) -> list[Issue]:
    """Every problem with the QSO, empty if it looks fine"""
    issues = []
    for run in CHECKS:
        issues.extend(run(qso))
    return issues
