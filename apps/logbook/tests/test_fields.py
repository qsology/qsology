"""The field registry, bands and modes"""

import itertools
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from apps.logbook import bands, fields
from apps.logbook.fields import Kind
from apps.logbook.models import QSO
from django.db import models


class TestRegistry:
    def test_every_field_is_on_the_model(self):
        for field in fields.FIELDS:
            assert QSO._meta.get_field(field.name)

    @pytest.mark.parametrize(
        ("kind", "model_field"),
        [
            (Kind.STRING, (models.CharField, models.TextField)),
            (Kind.LIST, models.CharField),
            (Kind.INT, (models.IntegerField, models.PositiveSmallIntegerField)),
            (Kind.DECIMAL, models.DecimalField),
            (Kind.COORDINATE, models.DecimalField),
            (Kind.DATETIME, models.DateTimeField),
        ],
    )
    def test_kinds_match_the_model(self, kind, model_field):
        for field in fields.FIELDS:
            if field.kind == kind:
                assert isinstance(QSO._meta.get_field(field.name), model_field)

    def test_names_are_unique(self):
        names = [field.name for field in fields.FIELDS]
        adif_names = [field.adif for field in fields.FIELDS]
        assert len(set(names)) == len(names)
        assert len(set(adif_names)) == len(adif_names)

    def test_adif_names_are_known(self):
        assert fields.COLUMN_ADIF_NAMES <= fields.ADIF_FIELDS
        assert {"QSO_DATE", "QSO_DATE_OFF"} <= fields.COLUMN_ADIF_NAMES

    def test_text_fields_reject_control_characters(self):
        for field in fields.FIELDS:
            if field.is_text:
                validators = QSO._meta.get_field(field.name).validators
                assert any(
                    getattr(v, "__name__", "") == "validate_safe_text"
                    for v in validators
                ), field.name

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("EPC", True),
            ("MY_THING", True),
            ("QSLMSG", False),
            ("NAME_INTL", False),
            ("APP_N1MM_X", False),
            ("CALL", False),
        ],
    )
    def test_userdef_fields(self, name, expected):
        assert fields.is_userdef_field(name) is expected


class TestValues:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("14.074000", "14.074"),
            ("100", "100"),
            ("1E+2", "100"),
            ("0.000", "0"),
            ("-0.5", "-0.5"),
            ("10.10", "10.1"),
        ],
    )
    def test_format_decimal(self, value, expected):
        assert fields.format_decimal(Decimal(value)) == expected

    def test_format_datetime_is_utc(self):
        eastern = timezone(timedelta(hours=-5))
        value = datetime(2024, 6, 1, 7, 0, tzinfo=eastern)
        assert fields.format_datetime(value) == "2024-06-01T12:00:00Z"

    @pytest.mark.parametrize(
        ("name", "value", "expected"),
        [
            ("freq", "14.074", Decimal("14.074")),
            ("freq", 14, Decimal("14")),
            ("freq", 14.074, Decimal("14.074")),
            ("dxcc", "291", 291),
            ("dxcc", 291, 291),
            ("call", "K1ABC", "K1ABC"),
            (
                "time_on",
                "2024-06-01T12:00:00Z",
                datetime(2024, 6, 1, 12, tzinfo=UTC),
            ),
            (
                "time_on",
                "2024-06-01T07:00:00-05:00",
                datetime(2024, 6, 1, 12, tzinfo=UTC),
            ),
            ("freq", None, None),
        ],
    )
    def test_from_json_value(self, name, value, expected):
        assert fields.from_json_value(fields.BY_NAME[name], value) == expected

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("freq", "fast"),
            ("freq", "NaN"),
            ("freq", "Infinity"),
            ("freq", True),
            ("freq", [14]),
            ("dxcc", "2.5"),
            ("dxcc", True),
            ("dxcc", {"value": 1}),
            ("call", 5),
            ("time_on", "2024-06-01T12:00:00"),
            ("time_on", "yesterday"),
            ("time_on", 1717243200),
        ],
    )
    def test_from_json_value_rejects(self, name, value):
        with pytest.raises(ValueError):
            fields.from_json_value(fields.BY_NAME[name], value)


class TestBands:
    def test_bands_dont_overlap(self):
        ordered = sorted(bands.BANDS, key=lambda band: band.low)
        for lower, upper in itertools.pairwise(ordered):
            assert lower.high < upper.low

    @pytest.mark.parametrize(
        ("freq", "band"),
        [
            ("3.573", "80m"),
            ("7.074", "40m"),
            ("14.074", "20m"),
            ("28.5", "10m"),
            ("432.1", "70cm"),
            ("14.5", None),
            (None, None),
        ],
    )
    def test_band_for_freq(self, freq, band):
        freq = Decimal(freq) if freq else None
        assert bands.band_for_freq(freq) == band

    def test_normalize(self):
        assert bands.normalize_band(" 20M ") == "20m"

    def test_submodes_belong_to_one_mode(self):
        seen = {}
        for mode, submodes in bands.MODES.items():
            for submode in submodes:
                assert submode not in seen, (submode, mode, seen.get(submode))
                seen[submode] = mode

    @pytest.mark.parametrize(
        ("submode", "mode"),
        [
            ("USB", "SSB"),
            ("FT4", "MFSK"),
            ("JS8", "MFSK"),
            ("PSK31", "PSK"),
            ("DMR", "DIGITALVOICE"),
            ("OLIVIA 8/250", "OLIVIA"),
            ("JT9H FAST", "JT9"),
        ],
    )
    def test_submode_to_mode(self, submode, mode):
        assert bands.SUBMODE_TO_MODE[submode] == mode
