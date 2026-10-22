"""Every field a QSO can hold, and how it maps onto ADIF

The model, filters, templates and every import and export format work from
this list, so a field only needs adding here and on the model. ADIF fields
without a column of their own are kept in QSO.extra under their ADIF name"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

ADIF_VERSION = "3.1.5"
PROGRAM_ID = "QSOLOGY"


class Kind(StrEnum):
    STRING = "string"
    # Comma separated list, e.g. POTA_REF US-0001,US-0002
    LIST = "list"
    INT = "int"
    DECIMAL = "decimal"
    DATETIME = "datetime"
    # Latitude or longitude, held as decimal degrees
    COORDINATE = "coordinate"


@dataclass(frozen=True)
class Field:
    name: str
    adif: str | None
    kind: Kind
    label: str

    @property
    def is_numeric(self) -> bool:
        return self.kind in (Kind.INT, Kind.DECIMAL, Kind.COORDINATE)

    @property
    def is_text(self) -> bool:
        return self.kind in (Kind.STRING, Kind.LIST)


S, L, N, D, T, C = (
    Kind.STRING,
    Kind.LIST,
    Kind.INT,
    Kind.DECIMAL,
    Kind.DATETIME,
    Kind.COORDINATE,
)

FIELDS: tuple[Field, ...] = tuple(
    Field(*spec)
    for spec in (
        # The contact
        ("call", "CALL", S, "Call"),
        ("time_on", "TIME_ON", T, "Time on"),
        ("time_off", "TIME_OFF", T, "Time off"),
        ("band", "BAND", S, "Band"),
        ("band_rx", "BAND_RX", S, "RX band"),
        ("freq", "FREQ", D, "Frequency (MHz)"),
        ("freq_rx", "FREQ_RX", D, "RX frequency (MHz)"),
        ("mode", "MODE", S, "Mode"),
        ("submode", "SUBMODE", S, "Submode"),
        ("rst_sent", "RST_SENT", S, "RST sent"),
        ("rst_rcvd", "RST_RCVD", S, "RST received"),
        ("tx_pwr", "TX_PWR", D, "TX power (W)"),
        ("rx_pwr", "RX_PWR", D, "Their power (W)"),
        ("prop_mode", "PROP_MODE", S, "Propagation mode"),
        ("sat_name", "SAT_NAME", S, "Satellite"),
        ("sat_mode", "SAT_MODE", S, "Satellite mode"),
        # The station worked
        ("name", "NAME", S, "Name"),
        ("qth", "QTH", S, "QTH"),
        ("gridsquare", "GRIDSQUARE", S, "Grid square"),
        ("lat", "LAT", C, "Latitude"),
        ("lon", "LON", C, "Longitude"),
        ("country", "COUNTRY", S, "Country"),
        ("dxcc", "DXCC", N, "DXCC"),
        ("cqz", "CQZ", N, "CQ zone"),
        ("ituz", "ITUZ", N, "ITU zone"),
        ("cont", "CONT", S, "Continent"),
        ("state", "STATE", S, "State"),
        ("cnty", "CNTY", S, "County"),
        ("iota", "IOTA", S, "IOTA"),
        ("sota_ref", "SOTA_REF", S, "SOTA"),
        ("pota_ref", "POTA_REF", L, "POTA"),
        ("wwff_ref", "WWFF_REF", S, "WWFF"),
        ("sig", "SIG", S, "Special interest group"),
        ("sig_info", "SIG_INFO", S, "Special interest info"),
        ("email", "EMAIL", S, "Email"),
        ("qsl_via", "QSL_VIA", S, "QSL via"),
        # Our station
        ("station_callsign", "STATION_CALLSIGN", S, "Station callsign"),
        ("operator", "OPERATOR", S, "Operator"),
        ("my_gridsquare", "MY_GRIDSQUARE", S, "My grid square"),
        ("my_lat", "MY_LAT", C, "My latitude"),
        ("my_lon", "MY_LON", C, "My longitude"),
        ("my_altitude", "MY_ALTITUDE", D, "My altitude (m)"),
        ("my_country", "MY_COUNTRY", S, "My country"),
        ("my_dxcc", "MY_DXCC", N, "My DXCC"),
        ("my_cq_zone", "MY_CQ_ZONE", N, "My CQ zone"),
        ("my_itu_zone", "MY_ITU_ZONE", N, "My ITU zone"),
        ("my_state", "MY_STATE", S, "My state"),
        ("my_cnty", "MY_CNTY", S, "My county"),
        ("my_iota", "MY_IOTA", S, "My IOTA"),
        ("my_sota_ref", "MY_SOTA_REF", S, "My SOTA"),
        ("my_pota_ref", "MY_POTA_REF", L, "My POTA"),
        ("my_wwff_ref", "MY_WWFF_REF", S, "My WWFF"),
        ("my_sig", "MY_SIG", S, "My special interest group"),
        ("my_sig_info", "MY_SIG_INFO", S, "My special interest info"),
        ("my_rig", "MY_RIG", S, "My rig"),
        ("my_antenna", "MY_ANTENNA", S, "My antenna"),
        # Contests
        ("contest_id", "CONTEST_ID", S, "Contest"),
        ("srx", "SRX", N, "Serial received"),
        ("stx", "STX", N, "Serial sent"),
        ("srx_string", "SRX_STRING", S, "Exchange received"),
        ("stx_string", "STX_STRING", S, "Exchange sent"),
        ("contest_check", "CHECK", S, "Check"),
        ("contest_class", "CLASS", S, "Class"),
        ("arrl_sect", "ARRL_SECT", S, "ARRL section"),
        ("precedence", "PRECEDENCE", S, "Precedence"),
        # QSLs
        ("qsl_sent", "QSL_SENT", S, "QSL sent"),
        ("qsl_rcvd", "QSL_RCVD", S, "QSL received"),
        ("lotw_qsl_sent", "LOTW_QSL_SENT", S, "LoTW sent"),
        ("lotw_qsl_rcvd", "LOTW_QSL_RCVD", S, "LoTW received"),
        ("eqsl_qsl_sent", "EQSL_QSL_SENT", S, "eQSL sent"),
        ("eqsl_qsl_rcvd", "EQSL_QSL_RCVD", S, "eQSL received"),
        # Free text
        ("comment", "COMMENT", S, "Comment"),
        ("notes", "NOTES", S, "Notes"),
    )
)

BY_NAME: dict[str, Field] = {field.name: field for field in FIELDS}
BY_ADIF: dict[str, Field] = {
    field.adif: field for field in FIELDS if field.adif
}

# ADIF splits the date and time, these are read into the datetime fields
DATE_ADIF = {"time_on": "QSO_DATE", "time_off": "QSO_DATE_OFF"}

# ADIF names with a home on the model, anything else goes into extra
COLUMN_ADIF_NAMES = frozenset(BY_ADIF) | frozenset(DATE_ADIF.values())

# Fields kept by ADIF exports that are ours, prefixed APP_QSOLOGY_
APP_PREFIX = f"APP_{PROGRAM_ID}_"
APP_UUID = f"{APP_PREFIX}UUID"

EXTRA_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")

# Every field in the ADIF 3.1.5 specification, used to tell user defined
# fields apart from standard ones without a column of their own
ADIF_FIELDS = COLUMN_ADIF_NAMES | frozenset(
    [
        "ADDRESS",
        "ADDRESS_INTL",
        "AGE",
        "ALTITUDE",
        "ANT_AZ",
        "ANT_EL",
        "ANT_PATH",
        "A_INDEX",
        "AWARD_GRANTED",
        "AWARD_SUBMITTED",
        "BAND_RX",
        "CLUBLOG_QSO_UPLOAD_DATE",
        "CLUBLOG_QSO_UPLOAD_STATUS",
        "COMMENT_INTL",
        "CONTACTED_OP",
        "COUNTRY_INTL",
        "CREDIT_GRANTED",
        "CREDIT_SUBMITTED",
        "DARC_DOK",
        "DCL_QSLRDATE",
        "DCL_QSLSDATE",
        "DCL_QSL_RCVD",
        "DCL_QSL_SENT",
        "DISTANCE",
        "EQ_CALL",
        "EQSL_AG",
        "EQSL_QSLRDATE",
        "EQSL_QSLSDATE",
        "FISTS",
        "FISTS_CC",
        "FORCE_INIT",
        "GRIDSQUARE_EXT",
        "GUEST_OP",
        "HAMLOGEU_QSO_UPLOAD_DATE",
        "HAMLOGEU_QSO_UPLOAD_STATUS",
        "HAMQTH_QSO_UPLOAD_DATE",
        "HAMQTH_QSO_UPLOAD_STATUS",
        "HRDLOG_QSO_UPLOAD_DATE",
        "HRDLOG_QSO_UPLOAD_STATUS",
        "IOTA_ISLAND_ID",
        "K_INDEX",
        "LOTW_QSLRDATE",
        "LOTW_QSLSDATE",
        "MAX_BURSTS",
        "MORSE_KEY_INFO",
        "MORSE_KEY_TYPE",
        "MS_SHOWER",
        "MY_ANTENNA_INTL",
        "MY_ARRL_SECT",
        "MY_CITY",
        "MY_CITY_INTL",
        "MY_CNTY_ALT",
        "MY_COUNTRY_INTL",
        "MY_DARC_DOK",
        "MY_FISTS",
        "MY_GRIDSQUARE_EXT",
        "MY_IOTA_ISLAND_ID",
        "MY_MORSE_KEY_INFO",
        "MY_MORSE_KEY_TYPE",
        "MY_NAME",
        "MY_NAME_INTL",
        "MY_POSTAL_CODE",
        "MY_POSTAL_CODE_INTL",
        "MY_RIG_INTL",
        "MY_SIG_INTL",
        "MY_SIG_INFO_INTL",
        "MY_STREET",
        "MY_STREET_INTL",
        "MY_USACA_COUNTIES",
        "MY_VUCC_GRIDS",
        "NAME_INTL",
        "NOTES_INTL",
        "NR_BURSTS",
        "NR_PINGS",
        "OWNER_CALLSIGN",
        "PFX",
        "PUBLIC_KEY",
        "QRZCOM_QSO_DOWNLOAD_DATE",
        "QRZCOM_QSO_DOWNLOAD_STATUS",
        "QRZCOM_QSO_UPLOAD_DATE",
        "QRZCOM_QSO_UPLOAD_STATUS",
        "QSLMSG",
        "QSLMSG_INTL",
        "QSLMSG_RCVD",
        "QSLRDATE",
        "QSLSDATE",
        "QSL_RCVD_VIA",
        "QSL_SENT_VIA",
        "QSO_COMPLETE",
        "QSO_RANDOM",
        "QTH_INTL",
        "REGION",
        "RIG",
        "RIG_INTL",
        "RX_PWR",
        "SFI",
        "SIG_INTL",
        "SIG_INFO_INTL",
        "SILENT_KEY",
        "SKCC",
        "SWL",
        "TEN_TEN",
        "UKSMG",
        "USACA_COUNTIES",
        "VE_PROV",
        "VUCC_GRIDS",
        "WEB",
        "CNTY_ALT",
    ]
)


def is_intl(adif_name: str) -> bool:
    """International fields can hold any Unicode, and are only allowed in
    ADX, not ADI"""
    return adif_name.endswith("_INTL")


def is_app_field(adif_name: str) -> bool:
    return adif_name.startswith("APP_")


def is_userdef_field(adif_name: str) -> bool:
    return not is_app_field(adif_name) and adif_name not in ADIF_FIELDS


# Characters that can't be written to XML, so can't go into an ADX export
_UNSAFE_CHARS_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff￾￿]")


def has_unsafe_chars(value: str) -> bool:
    return bool(_UNSAFE_CHARS_RE.search(value))


def replace_unsafe_chars(value: str) -> str:
    return _UNSAFE_CHARS_RE.sub("�", value)


def format_decimal(value: Decimal) -> str:
    """Write a decimal without trailing zeros or an exponent"""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_datetime(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_json_value(field: Field, value):
    """A field value as it is written to JSON, None stays None"""
    if value is None:
        return None
    if field.kind == Kind.DATETIME:
        return format_datetime(value)
    if field.kind in (Kind.DECIMAL, Kind.COORDINATE):
        # Strings keep the exact value, floats wouldn't
        return format_decimal(Decimal(value))
    return value


def from_json_value(field: Field, value):
    """Read a field value from JSON

    Raises ValueError if the value can't be read as the field's kind"""
    if value is None:
        return None
    if field.kind == Kind.DATETIME:
        if not isinstance(value, str):
            raise ValueError(f"{field.name} must be an ISO 8601 string")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError(f"{field.name} must include a timezone")
        return parsed.astimezone(UTC)
    if field.kind in (Kind.DECIMAL, Kind.COORDINATE):
        if isinstance(value, bool) or not isinstance(value, str | int | float):
            raise ValueError(f"{field.name} must be a number")
        try:
            number = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(f"{field.name} must be a number") from exc
        if not number.is_finite():
            raise ValueError(f"{field.name} must be a finite number")
        return number
    if field.kind == Kind.INT:
        if isinstance(value, bool):
            raise ValueError(f"{field.name} must be a whole number")
        if isinstance(value, str):
            value = value.strip()
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field.name} must be a whole number") from exc
    if not isinstance(value, str):
        raise ValueError(f"{field.name} must be a string")
    return value
