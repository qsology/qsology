"""Forms for logging and changing QSOs, importing and exporting

The QSO forms are built from apps.logbook.fields, so they take any field a
QSO has. Which of them a page shows depends on the view the user picked,
but a form sent with a field the page didn't show (from a page loaded in
the other view, say) still has it kept rather than dropped"""

import re
import uuid as uuid_module
from datetime import UTC, date, datetime, time, timedelta

from apps.logbook import bands, fields
from apps.logbook.models import QSO, ExportFormat
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

# What the entry form shows, in the order Tab moves through them. What
# changes every QSO comes first, so a contact is a few fields and Enter.
# What stays the same from one QSO to the next (frequency, mode, my
# callsign) is after it, and is kept for the next QSO
SIMPLE_FIELDS = (
    "call",
    "rst_sent",
    "rst_rcvd",
    "name",
    "comment",
    "freq",
    "band",
    "mode",
    "date",
    "time",
    "station_callsign",
)
ADVANCED_FIELDS = (
    "call",
    "rst_sent",
    "rst_rcvd",
    "name",
    "qth",
    "gridsquare",
    "comment",
    "freq",
    "band",
    "mode",
    "submode",
    "tx_pwr",
    "date",
    "time",
    "end_time",
    "station_callsign",
    "operator",
    "my_gridsquare",
    "sota_ref",
    "pota_ref",
    "wwff_ref",
    "my_sota_ref",
    "my_pota_ref",
    "my_wwff_ref",
    "contest_id",
    "srx",
    "stx",
    "srx_string",
    "stx_string",
    "notes",
)
# Kept from one QSO to the next on the entry form
STICKY_FIELDS = (
    "freq",
    "band",
    "mode",
    "submode",
    "tx_pwr",
    "station_callsign",
    "operator",
    "my_gridsquare",
    "my_sota_ref",
    "my_pota_ref",
    "my_wwff_ref",
    "contest_id",
)
# The date and time fields stand in for these
TIME_FIELDS = ("time_on", "time_off")
QSO_FIELDS = tuple(f.name for f in fields.FIELDS if f.name not in TIME_FIELDS)
FORM_FIELDS = (*QSO_FIELDS, "date", "time", "end_time")

# The fieldsets the QSO forms are split into, in the order they're first
# needed
GROUPS = {
    "The contact": (
        "call",
        "rst_sent",
        "rst_rcvd",
        "name",
        "qth",
        "gridsquare",
        "comment",
        "email",
        "qsl_via",
    ),
    "Frequency and mode": (
        "freq",
        "band",
        "mode",
        "submode",
        "tx_pwr",
        "rx_pwr",
        "freq_rx",
        "band_rx",
        "prop_mode",
        "sat_name",
        "sat_mode",
    ),
    "When (UTC)": ("date", "time", "end_time"),
    "Your station": (
        "station_callsign",
        "operator",
        "my_gridsquare",
        "my_rig",
        "my_antenna",
    ),
    "Their references": ("sota_ref", "pota_ref", "wwff_ref", "iota", "sig"),
    "Your references": (
        "my_sota_ref",
        "my_pota_ref",
        "my_wwff_ref",
        "my_iota",
        "my_sig",
    ),
    "Contest": (
        "contest_id",
        "srx",
        "stx",
        "srx_string",
        "stx_string",
        "contest_check",
        "contest_class",
        "arrl_sect",
        "precedence",
    ),
    "Notes": ("notes",),
}
GROUP_OF = {name: legend for legend, names in GROUPS.items() for name in names}

COMMON_MODES = (
    "SSB",
    "CW",
    "FM",
    "AM",
    "FT8",
    "FT4",
    "RTTY",
    "PSK",
    "JS8",
    "MFSK",
    "DIGITALVOICE",
    "OLIVIA",
    "SSTV",
)

_DATE_RE = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})$")
_TIME_RE = re.compile(r"^(\d{1,2}):?(\d{2})(?::?(\d{2}))?$")


def parse_date(text: str) -> date:
    match = _DATE_RE.match(text.strip())
    if not match:
        raise ValidationError("Enter the date as YYYY-MM-DD")
    try:
        return date(*map(int, match.groups()))
    except ValueError as exc:
        raise ValidationError("That date doesn't exist") from exc


def parse_time(text: str) -> time:
    match = _TIME_RE.match(text.strip())
    if not match:
        raise ValidationError("Enter the time as HH:MM, e.g. 14:05 or 1405")
    hour, minute, second = (int(part or 0) for part in match.groups())
    try:
        return time(hour, minute, second)
    except ValueError as exc:
        raise ValidationError("That time doesn't exist") from exc


def _field_for(name: str) -> forms.Field:
    """A form field for a QSO field, nothing is required but the call"""
    if name == "band":
        return forms.ChoiceField(
            label="Band",
            required=False,
            choices=[("", "From frequency")]
            + [(band.name, band.name) for band in bands.BANDS],
        )
    spec = fields.BY_NAME[name]
    # Never autocomplete="off": Firefox then doesn't keep what was typed
    # when Back is pressed, and a QSO that failed to send is lost
    attrs = {"spellcheck": "false"}
    if spec.is_numeric:
        attrs["inputmode"] = "decimal" if spec.kind != "int" else "numeric"
    if name in ("call", "station_callsign", "operator", "mode", "submode"):
        attrs["autocapitalize"] = "characters"
    if name == "mode":
        attrs["list"] = "modes"
    if name in ("comment", "notes", "name", "qth"):
        attrs["spellcheck"] = "true"
        attrs.pop("autocapitalize", None)
    model_field = QSO._meta.get_field(name)
    max_length = getattr(model_field, "max_length", None)
    if name == "notes":
        widget = forms.Textarea(attrs={**attrs, "rows": 2})
    else:
        widget = forms.TextInput(attrs=attrs)
    return forms.CharField(
        label=spec.label,
        required=name == "call",
        max_length=max_length,
        strip=False,
        widget=widget,
    )


class QSOForm(forms.Form):
    """A QSO as typed in. Fields are text and are read the way the API and
    imports read them, so the form, API and files agree on what's valid

    shown is the fields the page showed, an edit only changes those"""

    uuid = forms.UUIDField(widget=forms.HiddenInput, required=False)
    shown = forms.CharField(widget=forms.HiddenInput, required=False)
    accept_warnings = forms.BooleanField(
        required=False,
        label="Log it anyway, I've checked these",
    )

    def __init__(self, *args, field_names=FORM_FIELDS, now=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.now = now or timezone.now()
        self.field_names = tuple(field_names)
        for name in self.field_names:
            if name == "date":
                self.fields["date"] = forms.CharField(
                    label="Date (UTC)",
                    required=False,
                    widget=forms.TextInput(
                        attrs={"placeholder": "Today", "inputmode": "numeric"}
                    ),
                )
            elif name in ("time", "end_time"):
                self.fields[name] = forms.CharField(
                    label="Time (UTC)" if name == "time" else "End (UTC)",
                    required=False,
                    widget=forms.TextInput(
                        attrs={
                            "placeholder": "Now" if name == "time" else "",
                            "inputmode": "numeric",
                        }
                    ),
                )
            else:
                self.fields[name] = _field_for(name)
        self.fields["shown"].initial = ",".join(self.field_names)

    @classmethod
    def for_post(cls, data, **kwargs):
        """The form for a sent form: the fields it says it showed, which
        are emptied if they were left empty, and any other field sent with
        a value, which is kept rather than dropped"""
        shown = [
            name
            for name in data.get("shown", "").split(",")
            if name in FORM_FIELDS
        ]
        if not shown:
            return cls(data, field_names=FORM_FIELDS, **kwargs)
        sent = [
            name
            for name in FORM_FIELDS
            if name not in shown and str(data.get(name, "")).strip()
        ]
        return cls(data, field_names=[*shown, *sent], **kwargs)

    @property
    def groups(self) -> list[tuple[str, list]]:
        """The shown fields in their fieldsets, keeping the order they're
        in, which is the order Tab moves through them"""
        groups: dict[str, list] = {}
        for name in self.field_names:
            legend = GROUP_OF.get(name, "Other details")
            groups.setdefault(legend, []).append(self[name])
        return list(groups.items())

    def clean_call(self):
        value = "".join(self.cleaned_data["call"].split()).upper()
        if not value:
            raise ValidationError("Enter the callsign")
        return value

    def clean(self):
        cleaned = super().clean()
        for name in self.field_names:
            if name in ("date", "time", "end_time"):
                continue
            value = cleaned.get(name)
            if value in (None, ""):
                continue
            try:
                cleaned[name] = fields.from_json_value(
                    fields.BY_NAME[name], value.strip()
                )
            except ValueError as exc:
                self.add_error(name, _plain(str(exc), name))
        self._clean_times(cleaned)
        return cleaned

    def _clean_times(self, cleaned) -> None:
        if "date" not in self.field_names and "time" not in self.field_names:
            return
        now = self.now.astimezone(UTC).replace(microsecond=0)
        date_text = (cleaned.get("date") or "").strip()
        time_text = (cleaned.get("time") or "").strip()
        try:
            day = parse_date(date_text) if date_text else now.date()
        except ValidationError as exc:
            self.add_error("date", exc)
            return
        if not time_text:
            if date_text and day != now.date():
                self.add_error("time", "Enter the time")
                return
            cleaned["time_on"] = now
        else:
            try:
                cleaned["time_on"] = datetime.combine(
                    day, parse_time(time_text), tzinfo=UTC
                )
            except ValidationError as exc:
                self.add_error("time", exc)
                return
        end_text = (cleaned.get("end_time") or "").strip()
        if "end_time" in self.field_names:
            cleaned["time_off"] = None
        if end_text:
            try:
                end = datetime.combine(
                    cleaned["time_on"].date(), parse_time(end_text), tzinfo=UTC
                )
            except ValidationError as exc:
                self.add_error("end_time", exc)
                return
            # A QSO going past midnight ends the next day
            if end < cleaned["time_on"]:
                end += timedelta(days=1)
            cleaned["time_off"] = end

    def values(self, *, include_empty: bool = False) -> dict:
        """The QSO field values the form holds

        Without include_empty, fields left empty are left out, so a new QSO
        takes them from elsewhere. With it, they're there to clear"""
        values = {}
        for name in self.field_names:
            if name in ("date", "time", "end_time"):
                continue
            value = self.cleaned_data.get(name)
            if value in (None, "") and not include_empty:
                continue
            if value == "" and fields.BY_NAME[name].is_numeric:
                value = None
            values[name] = value
        for name in TIME_FIELDS:
            if name in self.cleaned_data:
                values[name] = self.cleaned_data[name]
        return values

    @property
    def qso_uuid(self):
        return self.cleaned_data.get("uuid")

    def add_service_errors(self, exc: ValidationError) -> None:
        """Errors from saving, against the fields the user can see"""
        mapping = {"time_on": "time", "time_off": "end_time"}
        if not hasattr(exc, "error_dict"):
            self.add_error(None, exc.messages)
            return
        for name, errors in exc.message_dict.items():
            name = mapping.get(name, name)
            self.add_error(name if name in self.fields else None, errors)


def _plain(message: str, name: str) -> str:
    """Field errors read as "must be a number", the label says which"""
    prefix = f"{name} "
    if message.startswith(prefix):
        message = message[len(prefix) :]
    return message[:1].upper() + message[1:]


def entry_initial(user, previous: dict | None = None, now=None) -> dict:
    """What a new QSO starts with: a fresh uuid, and what's kept from the
    QSO before it"""
    from apps.callsigns.services import primary_call

    initial = {"uuid": uuid_module.uuid4()}
    for name, value in (previous or {}).items():
        if name in STICKY_FIELDS and value not in (None, ""):
            initial[name] = value
    if "station_callsign" not in initial:
        initial["station_callsign"] = primary_call(user, now)
    return initial


def initial_from_qso(qso: QSO) -> dict:
    initial = {"uuid": qso.uuid}
    for name in QSO_FIELDS:
        value = fields.to_json_value(fields.BY_NAME[name], getattr(qso, name))
        if value not in (None, ""):
            initial[name] = value
    if qso.time_on:
        initial["date"] = qso.time_on.strftime("%Y-%m-%d")
        initial["time"] = qso.time_on.strftime("%H:%M:%S")
    if qso.time_off:
        initial["end_time"] = qso.time_off.strftime("%H:%M:%S")
    return initial


def edit_field_names(qso: QSO, advanced: bool) -> tuple[str, ...]:
    """What the edit page shows: the view's fields, and every other field
    the QSO has a value in, so nothing is hidden"""
    base = ADVANCED_FIELDS if advanced else SIMPLE_FIELDS
    names = list(base)
    if qso.time_off and "end_time" not in names:
        names.append("end_time")
    for name in QSO_FIELDS:
        if name not in names and getattr(qso, name) not in (None, ""):
            names.append(name)
    return tuple(names)


class ExportForm(forms.Form):
    format = forms.ChoiceField(
        choices=ExportFormat.choices,
        initial=ExportFormat.ADI,
        widget=forms.RadioSelect,
        help_text="ADIF (.adi) is what most logging programs and LoTW take",
    )
    logbook = forms.ModelChoiceField(
        queryset=None,
        required=False,
        empty_label="Every QSO",
        label="Which QSOs",
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        logbooks = user.logbooks.filter(is_master=False)
        self.fields["logbook"].queryset = logbooks
        if not logbooks.exists():
            del self.fields["logbook"]


class ImportForm(forms.Form):
    file = forms.FileField(
        label="ADIF file",
        help_text=".adi, .adif or .adx, as saved by your logging program",
        widget=forms.ClearableFileInput(
            attrs={"accept": ".adi,.adif,.adx,.json,text/plain"}
        ),
    )
    accept_warnings = forms.BooleanField(
        required=False,
        label=("Log QSOs with warnings straight away, marked for me to review"),
        help_text=(
            "Otherwise they're held for you to look at, and logged once "
            "you say so"
        ),
    )

    def __init__(self, *args, max_bytes: int, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_bytes = max_bytes

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if upload.size > self.max_bytes:
            raise ValidationError(
                f"The file is too big, the most is "
                f"{self.max_bytes // (1024 * 1024)} MB. Split it and import "
                "each part"
            )
        return upload


class QSOSearchForm(forms.Form):
    q = forms.CharField(
        label="Callsign",
        required=False,
        help_text="All or part of a callsign",
        widget=forms.TextInput(
            attrs={
                "type": "search",
                "autocapitalize": "characters",
                "spellcheck": "false",
            }
        ),
    )
    filter = forms.CharField(
        label="Filter",
        required=False,
        help_text="e.g. band:20m mode:CW,SSB call:M0* date>=2024-01-01",
        widget=forms.TextInput(attrs={"spellcheck": "false"}),
    )
    deleted = forms.BooleanField(label="Show deleted QSOs", required=False)
