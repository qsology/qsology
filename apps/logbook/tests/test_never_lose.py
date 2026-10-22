"""A log entry is never lost

Property based tests over generated QSOs and files, checking that:

* any QSO the log will hold can be exported in every format, and reading
  the export back gives the same QSO
* any file at all can be imported without an error, and every record in it
  ends up as a QSO or kept as an ImportRecord
* whatever is done to QSOs, every one is still there and its history
  rebuilds exactly how it is now"""

import string
import uuid as uuid_module
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from apps.callsigns.models import Callsign, CallsignAllocation
from apps.logbook import exports, fields, importing, services
from apps.logbook.fields import Kind
from apps.logbook.formats import adi, adx, jsonfmt
from apps.logbook.models import (
    QSO,
    ImportRecordStatus,
    QSOEvent,
)
from apps.logbook.models.qso import RESERVED_EXTRA_KEYS
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.extra.django import TestCase
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    consumes,
    invariant,
    multiple,
    rule,
)

# Free text, anything the log accepts: no control characters other than
# tab, newline and carriage return
text_chars = st.characters(
    blacklist_categories=("Cs", "Cc"),
    blacklist_characters="￾￿",
) | st.sampled_from("\t\n\r")


def text(max_size: int, min_size: int = 0):
    return st.text(text_chars, min_size=min_size, max_size=max_size)


codes = st.text(string.ascii_uppercase + string.digits + "-/", max_size=10)
callsigns = st.from_regex(r"\A[A-Z0-9]{1,3}[0-9][A-Z]{1,4}(/[A-Z0-9]{1,3})?\Z")


def decimals(places: int, low, high):
    return st.decimals(
        min_value=Decimal(low),
        max_value=Decimal(high),
        places=places,
        allow_nan=False,
        allow_infinity=False,
    )


times = st.datetimes(
    min_value=datetime(1900, 1, 1),
    max_value=datetime(2100, 1, 1),
    timezones=st.just(UTC),
).map(lambda value: value.replace(microsecond=0))


def _max_length(name: str) -> int:
    return QSO._meta.get_field(name).max_length or 1000


def field_values(field: fields.Field):
    name = field.name
    if field.kind == Kind.DATETIME:
        return times
    if field.kind == Kind.COORDINATE:
        limit = 90 if name in ("lat", "my_lat") else 180
        return decimals(6, -limit, limit)
    if field.kind == Kind.DECIMAL:
        places = QSO._meta.get_field(name).decimal_places
        return decimals(places, 0, 99999)
    if field.kind == Kind.INT:
        if name in ("cqz", "my_cq_zone"):
            return st.integers(1, 40)
        if name in ("ituz", "my_itu_zone"):
            return st.integers(1, 90)
        return st.integers(0, 2**31 - 1)
    if name in ("call", "station_callsign", "operator"):
        return callsigns
    if name in ("comment", "notes", "name", "qth"):
        return text(60)
    return text(min(_max_length(name), 20)) | codes.map(
        lambda value: value[: _max_length(name)]
    )


extra_keys = st.from_regex(r"\A[A-Z](?:[A-Z0-9_]{0,10}[A-Z0-9])?\Z").filter(
    lambda key: (
        key not in fields.COLUMN_ADIF_NAMES
        and key not in RESERVED_EXTRA_KEYS
        and not key.startswith(fields.APP_PREFIX)
    )
)


@st.composite
def generated_qsos(draw):
    """A QSO the log will hold, normalized as it would be when saved"""
    optional = [
        field
        for field in fields.FIELDS
        if field.name not in ("call", "time_on", "time_off")
    ]
    chosen = draw(st.lists(st.sampled_from(optional), unique=True))
    values = {field.name: draw(field_values(field)) for field in chosen}
    values["call"] = draw(callsigns)
    values["time_on"] = draw(times)
    if draw(st.booleans()):
        values["time_off"] = values["time_on"] + timedelta(
            seconds=draw(st.integers(0, 2 * 24 * 60 * 60))
        )
    values["extra"] = draw(
        st.dictionaries(extra_keys, text(30, min_size=1), max_size=5)
    )
    qso = QSO(uuid=uuid_module.uuid4(), **values)
    qso.normalize()
    return qso


def read_back(format: str, text_: str) -> tuple[QSO, str | None]:
    """The single QSO in an export, read back as the importer would"""
    parser = {"adi": adi.parse, "adx": adx.parse, "json": jsonfmt.parse}
    parsed = parser[format](text_)
    assert len(parsed.records) == 1
    record = parsed.records[0]
    assert record.problems == []
    data = record.data if format == "json" else record.fields
    values, qso_uuid, problems = importing._values_from(format, data)
    assert problems == []
    qso = QSO()
    for key, value in values.items():
        setattr(qso, key, value)
    qso.normalize()
    return qso, qso_uuid


def comparable(qso: QSO, format: str) -> dict:
    data = qso.to_data()
    data.pop("uuid")
    if format != "json":
        # ADIF writes positions to a thousandth of a minute
        for name in ("lat", "lon", "my_lat", "my_lon"):
            if name in data:
                data[name] = round(Decimal(data[name]) * 60000)
    if format == "adi":
        # International fields are only allowed in ADX
        data["extra"] = {
            key: value
            for key, value in data.get("extra", {}).items()
            if not fields.is_intl(key)
        }
        if not data["extra"]:
            data.pop("extra")
    return data


class TestEveryQSOExports:
    """Pure format round trips, so they can run many examples quickly"""

    @settings(max_examples=settings.default.max_examples * 5, deadline=None)
    @given(qso=generated_qsos())
    def test_adi_round_trip(self, qso):
        self.check("adi", qso)

    @settings(max_examples=settings.default.max_examples * 5, deadline=None)
    @given(qso=generated_qsos())
    def test_adx_round_trip(self, qso):
        self.check("adx", qso)

    @settings(max_examples=settings.default.max_examples * 5, deadline=None)
    @given(qso=generated_qsos())
    def test_json_round_trip(self, qso):
        self.check("json", qso)

    def check(self, format, qso):
        created = datetime(2024, 1, 1, tzinfo=UTC)
        first = exports.export_text([qso], format, created)
        back, qso_uuid = read_back(format, first)
        assert qso_uuid == str(qso.uuid)
        assert comparable(back, format) == comparable(qso, format)
        # Once read back, exporting and reading again changes nothing
        back.uuid = qso.uuid
        second = exports.export_text([back], format, created)
        again, _ = read_back(format, second)
        again.uuid = qso.uuid
        assert exports.export_text([again], format, created) == second


class TestImportsNeverFail(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="importer", password=None
        )

    def check(self, content, format=None):
        batch = importing.import_file(self.user, content, format=format)
        batch.refresh_from_db()
        assert batch.original == importing.decode(content)
        assert batch.total == (
            batch.logged + batch.duplicates + batch.held + batch.failed
        )
        kept = batch.records.exclude(status=ImportRecordStatus.LOGGED)
        assert kept.count() == batch.duplicates + batch.held + batch.failed
        assert batch.qsos.count() == batch.logged
        for record in kept:
            # The raw record, or the whole file if it couldn't be read
            assert record.raw or batch.original == ""

    @settings(
        max_examples=settings.default.max_examples * 2,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    @given(content=st.binary(max_size=500) | text(500))
    def test_any_content(self, content):
        for format in ("adi", "adx", "json", None):
            self.check(content, format)

    adif_fields = st.tuples(
        st.sampled_from(
            [
                "CALL",
                "QSO_DATE",
                "TIME_ON",
                "TIME_OFF",
                "FREQ",
                "BAND",
                "MODE",
                "LAT",
                "DXCC",
                "CQZ",
                "STATION_CALLSIGN",
                "QSLMSG",
                "APP_QSOLOGY_UUID",
                "EOR",
                "eor",
                "weird name",
            ]
        ),
        text(12) | st.from_regex(r"\A\d{8}\Z") | st.from_regex(r"\A\d{4}\Z"),
        st.integers(-2, 3),
    )

    @settings(
        max_examples=settings.default.max_examples * 2,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    @given(
        records=st.lists(st.lists(adif_fields, max_size=8), max_size=5),
        header=st.booleans(),
    )
    def test_adif_like_files(self, records, header):
        """ADI files with wrong lengths, bad values and missing parts"""
        parts = ["head <EOH>\n"] if header else []
        for record in records:
            for name, value, error in record:
                parts.append(f"<{name}:{max(0, len(value) + error)}>{value} ")
            parts.append("<EOR>\n")
        self.check("".join(parts), "adi")


class LogMachine(RuleBasedStateMachine):
    """Random sequences of logging, changing, deleting and restoring"""

    qsos = Bundle("qsos")
    deleted = Bundle("deleted")

    def __init__(self):
        super().__init__()
        self.user = get_user_model().objects.create_user(
            username=f"op{uuid_module.uuid4().hex[:12]}", password=None
        )
        callsign, _ = Callsign.objects.get_or_create(call="M0WME")
        CallsignAllocation.objects.filter(callsign=callsign).delete()
        CallsignAllocation.objects.create(
            callsign=callsign,
            user=self.user,
            valid_from=datetime(1900, 1, 1, tzinfo=UTC),
        )
        self.logged = 0

    @rule(target=qsos, values=generated_qsos(), accept=st.booleans())
    def log(self, values, accept):
        data = {
            field.name: getattr(values, field.name) for field in fields.FIELDS
        }
        data["extra"] = values.extra
        try:
            qso = services.log_qso(self.user, data, accept_warnings=accept)
        except (services.SanityCheckError, ValidationError) as exc:
            # Refused, either for warnings that weren't accepted or for
            # something the log can't hold at all. Nothing may be saved
            if isinstance(exc, services.SanityCheckError):
                assert not accept
            assert self.qso_count() == self.logged
            return multiple()
        self.logged += 1
        return qso

    def qso_count(self) -> int:
        return QSO.all_objects.filter(owner=self.user).count()

    @rule(qso=qsos, comment=text(40))
    def edit(self, qso, comment):
        qso.refresh_from_db()
        services.update_qso(qso, {"comment": comment}, accept_warnings=True)

    @rule(target=deleted, qso=consumes(qsos))
    def delete(self, qso):
        qso.refresh_from_db()
        qso.delete()
        return qso

    @rule(target=qsos, qso=consumes(deleted))
    def restore(self, qso):
        qso.refresh_from_db()
        qso.restore()
        return qso

    @rule(qso=qsos, data=st.data())
    def revert(self, qso, data):
        qso.refresh_from_db()
        version = data.draw(st.integers(1, qso.version))
        services.revert_qso(qso, qso.revisions.get(version=version))

    @rule(qso=qsos)
    def stale_save_is_refused(self, qso):
        stale = QSO.all_objects.get(pk=qso.pk)
        qso.refresh_from_db()
        qso.save()
        try:
            stale.save()
        except Exception as exc:
            assert type(exc).__name__ == "QSOConflict"
        else:
            raise AssertionError("A stale QSO was saved")

    @invariant()
    def nothing_is_lost(self):
        assert self.qso_count() == self.logged

    @invariant()
    def history_matches(self):
        for qso in QSO.all_objects.filter(owner=self.user):
            versions = list(qso.revisions.values_list("version", flat=True))
            assert versions == list(range(1, qso.version + 1))
            latest = qso.revisions.last()
            assert latest.data == qso.to_data(include_empty=True)
            events = QSOEvent.objects.filter(qso_uuid=qso.uuid).count()
            assert events == qso.version

    @invariant()
    def master_log_exports_everything(self):
        live = QSO.objects.filter(owner=self.user)
        for format in ("adi", "adx", "json"):
            text_ = exports.export_text(live, format)
            parser = {"adi": adi.parse, "adx": adx.parse}.get(
                format, jsonfmt.parse
            )
            assert len(parser(text_).records) == live.count()


class TestLogMachine(TestCase):
    def test_machine(self):
        LogMachine.TestCase.settings = settings(
            max_examples=max(10, settings.default.max_examples // 5),
            stateful_step_count=settings.default.stateful_step_count // 4,
            deadline=None,
            suppress_health_check=[
                HealthCheck.too_slow,
            ],
        )
        LogMachine.TestCase("runTest").runTest()
