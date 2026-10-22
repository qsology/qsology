"""Converting QSOs to and from ADIF fields, shared by ADI and ADX

A QSO becomes a flat mapping of ADIF field name to text, and back again.
Reading never throws a value away: anything that can't be read into its
column is kept, as it was, in extra under its ADIF name and the QSO is
flagged for review"""

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from apps.logbook import fields
from apps.logbook.fields import Kind
from django.conf import settings

PROGRAM_NAME = "qsology"
PROGRAM_VERSION = "2026.01"

LATITUDE_FIELDS = frozenset({"lat", "my_lat"})


def program_version() -> str:
    return getattr(settings, "QSOLOGY_VERSION", PROGRAM_VERSION)


# Writing


def format_date(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%d")


def format_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%H%M%S")


def format_location(value: Decimal, is_latitude: bool) -> str:
    """Write decimal degrees in the ADIF location format, e.g. N051 30.000"""
    value = Decimal(value)
    if is_latitude:
        hemisphere = "S" if value < 0 else "N"
    else:
        hemisphere = "W" if value < 0 else "E"
    value = abs(value)
    degrees = int(value)
    minutes = ((value - degrees) * 60).quantize(
        Decimal("0.001"), rounding=ROUND_HALF_EVEN
    )
    if minutes >= 60:
        degrees += 1
        minutes -= 60
    return f"{hemisphere}{degrees:03d} {minutes:06.3f}"


def format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%d %H%M%S")


def qso_to_adif(qso) -> dict[str, str]:
    """The QSO as ADIF fields, in a stable order

    Columns are written first, then anything in extra. A value in extra for
    a column, kept because it couldn't be read, is only written if the
    column itself is empty"""
    record: dict[str, str] = {}
    for spec in fields.FIELDS:
        value = getattr(qso, spec.name)
        if value is None or value == "":
            continue
        if spec.kind == Kind.DATETIME:
            record[fields.DATE_ADIF[spec.name]] = format_date(value)
            record[spec.adif] = format_time(value)
        elif spec.kind == Kind.COORDINATE:
            record[spec.adif] = format_location(
                value, spec.name in LATITUDE_FIELDS
            )
        elif spec.kind == Kind.DECIMAL:
            record[spec.adif] = fields.format_decimal(Decimal(value))
        else:
            record[spec.adif] = str(value)
    for key, value in sorted((qso.extra or {}).items()):
        if key not in record and value != "":
            record[key] = value
    record[fields.APP_UUID] = str(qso.uuid)
    return record


def header_fields(created: datetime) -> dict[str, str]:
    return {
        "ADIF_VER": fields.ADIF_VERSION,
        "PROGRAMID": PROGRAM_NAME,
        "PROGRAMVERSION": program_version(),
        "CREATED_TIMESTAMP": format_timestamp(created),
    }


# Reading


@dataclass
class Converted:
    """ADIF fields read into QSO values"""

    values: dict = field(default_factory=dict)
    extra: dict[str, str] = field(default_factory=dict)
    uuid: str | None = None
    problems: list[str] = field(default_factory=list)


_LOCATION_RE = re.compile(
    r"^([NSEW])\s*(\d{1,3})\s+(\d{1,2}(?:\.\d+)?)$", re.IGNORECASE
)


def parse_location(value: str, is_latitude: bool) -> Decimal:
    """Read an ADIF location, e.g. N051 30.000, as decimal degrees

    Plain decimal degrees are accepted too, as some loggers write them"""
    value = value.strip()
    match = _LOCATION_RE.match(value)
    if match:
        hemisphere = match.group(1).upper()
        if (hemisphere in "NS") != is_latitude:
            raise ValueError(f"{value!r} is the wrong kind of coordinate")
        minutes = Decimal(match.group(3))
        if minutes >= 60:
            raise ValueError(f"{value!r} has more than 60 minutes")
        degrees = int(match.group(2)) + minutes / 60
        if hemisphere in "SW":
            degrees = -degrees
    else:
        try:
            degrees = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"{value!r} isn't a location") from exc
        if not degrees.is_finite():
            raise ValueError(f"{value!r} isn't a location")
    limit = 90 if is_latitude else 180
    if not -limit <= degrees <= limit:
        raise ValueError(f"{value!r} is out of range")
    return degrees.quantize(Decimal("0.000001"))


def parse_date(value: str) -> date:
    value = value.strip()
    if not re.fullmatch(r"\d{8}", value):
        raise ValueError(f"{value!r} isn't a date, ADIF dates are YYYYMMDD")
    return datetime.strptime(value, "%Y%m%d").date()


def parse_time(value: str) -> time:
    value = value.strip()
    if re.fullmatch(r"\d{4}", value):
        return datetime.strptime(value, "%H%M").time()
    if re.fullmatch(r"\d{6}", value):
        return datetime.strptime(value, "%H%M%S").time()
    raise ValueError(f"{value!r} isn't a time, ADIF times are HHMM or HHMMSS")


def _parse_decimal(value: str) -> Decimal:
    try:
        number = Decimal(value.strip())
    except InvalidOperation as exc:
        raise ValueError(f"{value!r} isn't a number") from exc
    if not number.is_finite():
        raise ValueError(f"{value!r} isn't a number")
    return number


def _parse_int(value: str) -> int:
    number = _parse_decimal(value)
    if number != number.to_integral_value():
        raise ValueError(f"{value!r} isn't a whole number")
    return int(number)


def _max_length(name: str) -> int | None:
    from apps.logbook.models import QSO

    return getattr(QSO._meta.get_field(name), "max_length", None)


def _read_value(spec: fields.Field, value: str):
    if spec.kind == Kind.INT:
        return _parse_int(value)
    if spec.kind == Kind.DECIMAL:
        return _parse_decimal(value)
    if spec.kind == Kind.COORDINATE:
        return parse_location(value, spec.name in LATITUDE_FIELDS)
    max_length = _max_length(spec.name)
    if max_length is not None and len(value) > max_length:
        raise ValueError(f"is longer than {max_length} characters")
    return value


def _read_datetime(record: dict, name: str, converted: Converted) -> None:
    """Combine an ADIF date and time into one of the datetime fields"""
    date_key = fields.DATE_ADIF[name]
    time_key = fields.BY_NAME[name].adif
    date_value = record.get(date_key)
    time_value = record.get(time_key)
    if name == "time_off" and not date_value and time_value:
        # No end date, so it ends on the day it started, or the next day if
        # it ran past midnight
        start = converted.values.get("time_on")
        if start is None:
            converted.extra[time_key] = time_value
            converted.problems.append(f"{time_key} has no date")
            return
        try:
            end = datetime.combine(start.date(), parse_time(time_value), UTC)
        except ValueError as exc:
            converted.extra[time_key] = time_value
            converted.problems.append(f"{time_key}: {exc}")
            return
        if end < start:
            end += timedelta(days=1)
        converted.values[name] = end
        return
    if not date_value and not time_value:
        return
    try:
        if not date_value or not time_value:
            missing = time_key if date_value else date_key
            raise ValueError(f"{missing} is missing")
        converted.values[name] = datetime.combine(
            parse_date(date_value), parse_time(time_value), UTC
        )
    except ValueError as exc:
        if date_value:
            converted.extra[date_key] = date_value
        if time_value:
            converted.extra[time_key] = time_value
        converted.problems.append(f"{date_key}/{time_key}: {exc}")


def clean_field_name(name: str) -> str:
    """Make any field name usable as an extra key, e.g. 'my field' becomes
    MY_FIELD"""
    name = re.sub(r"[^A-Z0-9_]", "_", name.strip().upper())
    if not name or not name[0].isalpha():
        name = f"X_{name}"
    return name[:64].rstrip("_") or "X"


def adif_to_qso(record: dict[str, str]) -> Converted:
    """Read ADIF fields into QSO values"""
    converted = Converted()
    record = {clean_field_name(key): value for key, value in record.items()}
    # time_on first, as time_off can need it
    for name in ("time_on", "time_off"):
        _read_datetime(record, name, converted)
    used = set(fields.DATE_ADIF.values()) | {
        fields.BY_NAME[name].adif for name in fields.DATE_ADIF
    }
    for key, value in record.items():
        if key in used:
            continue
        if key == fields.APP_UUID:
            converted.uuid = value.strip()
            continue
        # An empty field says nothing, so isn't kept
        if value == "":
            continue
        spec = fields.BY_ADIF.get(key)
        if spec is None:
            converted.extra[key] = value
            continue
        try:
            converted.values[spec.name] = _read_value(spec, value)
        except ValueError as exc:
            converted.extra[key] = value
            converted.problems.append(f"{key}: {exc}")
    return converted
