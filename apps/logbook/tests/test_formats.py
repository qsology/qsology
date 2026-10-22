"""ADI, ADX and JSON, written and read"""

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from apps.logbook import exports
from apps.logbook.formats import adi, adif, adx, jsonfmt
from apps.logbook.models import QSO

CREATED = datetime(2024, 7, 1, 9, 30, tzinfo=UTC)
UUID = uuid.UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture
def sample():
    """An unsaved QSO with a bit of everything in it"""
    return QSO(
        uuid=UUID,
        call="K1ABC",
        time_on=datetime(2024, 6, 1, 12, 0, 3, tzinfo=UTC),
        time_off=datetime(2024, 6, 1, 12, 1, 0, tzinfo=UTC),
        band="20m",
        freq=Decimal("14.074000"),
        mode="FT8",
        rst_sent="-10",
        name="Józef",
        lat=Decimal("-33.856785"),
        lon=Decimal("151.215296"),
        dxcc=291,
        station_callsign="M0WME/P",
        my_pota_ref="US-0001,US-0002",
        comment="line one\r\nline two",
        extra={
            "QSLMSG": "TNX",
            "NAME_INTL": "Józef",
            "EPC": "1234",
            "APP_N1MM_EXCHANGE1": "14",
        },
    )


class TestWritingValues:
    @pytest.mark.parametrize(
        ("value", "is_latitude", "expected"),
        [
            ("51.5", True, "N051 30.000"),
            ("-33.856785", True, "S033 51.407"),
            ("151.215296", False, "E151 12.918"),
            ("-0.1", False, "W000 06.000"),
            ("0", True, "N000 00.000"),
            ("-180", False, "W180 00.000"),
            # Minutes that round up to 60 carry into the degrees
            ("10.99999999", True, "N011 00.000"),
        ],
    )
    def test_format_location(self, value, is_latitude, expected):
        assert adif.format_location(Decimal(value), is_latitude) == expected

    def test_record_order_and_values(self, sample):
        record = adif.qso_to_adif(sample)
        assert list(record)[:6] == [
            "CALL",
            "QSO_DATE",
            "TIME_ON",
            "QSO_DATE_OFF",
            "TIME_OFF",
            "BAND",
        ]
        assert record["FREQ"] == "14.074"
        assert record["TIME_ON"] == "120003"
        assert record["QSO_DATE_OFF"] == "20240601"
        assert record["DXCC"] == "291"
        assert record["APP_QSOLOGY_UUID"] == str(UUID)

    def test_column_wins_over_extra(self, sample):
        sample.extra["FREQ"] = "garbage"
        assert adif.qso_to_adif(sample)["FREQ"] == "14.074"

    def test_extra_used_when_column_is_empty(self, sample):
        sample.freq = None
        sample.extra["FREQ"] = "garbage"
        assert adif.qso_to_adif(sample)["FREQ"] == "garbage"


class TestADIWriter:
    def test_golden(self, sample):
        text = exports.export_text([sample], "adi", CREATED)
        assert text == (
            "Exported by qsology 2026.01\n"
            "<ADIF_VER:5>3.1.5\n"
            "<PROGRAMID:7>qsology\n"
            "<PROGRAMVERSION:7>2026.01\n"
            "<CREATED_TIMESTAMP:15>20240701 093000\n"
            "<USERDEF1:3:S>EPC\n"
            "<EOH>\n"
            "\n"
            "<CALL:5>K1ABC <QSO_DATE:8>20240601 <TIME_ON:6>120003 "
            "<QSO_DATE_OFF:8>20240601 <TIME_OFF:6>120100 <BAND:3>20m "
            "<FREQ:6>14.074 <MODE:3>FT8 <RST_SENT:3>-10 <NAME:5>Józef "
            "<LAT:11>S033 51.407 <LON:11>E151 12.918 <DXCC:3>291 "
            "<STATION_CALLSIGN:7>M0WME/P <MY_POTA_REF:15>US-0001,US-0002 "
            "<COMMENT:18>line one\r\nline two <APP_N1MM_EXCHANGE1:2>14 "
            "<EPC:4>1234 <QSLMSG:3>TNX "
            f"<APP_QSOLOGY_UUID:36>{UUID} <EOR>\n"
        )

    def test_header_doesnt_start_with_a_tag(self, sample):
        # A file starting with "<" has no header, as far as ADIF goes
        assert not exports.export_text([sample], "adi").startswith("<")

    def test_intl_fields_left_out(self, sample):
        assert "NAME_INTL" not in exports.export_text([sample], "adi")

    def test_empty_log(self):
        text = exports.export_text([], "adi", CREATED)
        assert text.endswith("<EOH>\n\n")
        assert adi.parse(text).records == []

    def test_unwritable_characters_are_replaced(self, sample):
        sample.comment = "bad\x00char"
        assert "<COMMENT:8>bad�char" in exports.export_text([sample], "adi")

    def test_unknown_format(self):
        with pytest.raises(ValueError, match="Unknown export format"):
            exports.export_text([], "csv")


class TestADIParser:
    def test_reads_records(self):
        parsed = adi.parse(
            "header\n<ADIF_VER:5>3.1.5<EOH>"
            "<CALL:5>K1ABC<BAND:3>20m<EOR>\n<CALL:5>K2ABC<EOR>"
        )
        assert parsed.header == {"ADIF_VER": "3.1.5"}
        assert [record.fields for record in parsed.records] == [
            {"CALL": "K1ABC", "BAND": "20m"},
            {"CALL": "K2ABC"},
        ]
        assert parsed.records[0].raw == "<CALL:5>K1ABC<BAND:3>20m<EOR>"
        assert parsed.records[1].index == 1

    def test_tags_are_case_insensitive(self):
        parsed = adi.parse("<eoh><call:5>K1ABC<Band:3>20m<eor>")
        assert parsed.records[0].fields == {"CALL": "K1ABC", "BAND": "20m"}

    def test_no_header(self):
        parsed = adi.parse("<CALL:5>K1ABC<EOR>")
        assert parsed.header == {}
        assert parsed.records[0].fields == {"CALL": "K1ABC"}
        assert parsed.problems == []

    def test_header_without_eoh(self):
        parsed = adi.parse("A header\n<CALL:5>K1ABC<EOR>")
        assert parsed.records[0].fields == {"CALL": "K1ABC"}
        assert "No <EOH>" in parsed.problems[0]

    def test_type_indicators(self):
        parsed = adi.parse("<EOH><FREQ:6:N>14.074<CALL:5:S>K1ABC<EOR>")
        assert parsed.records[0].fields == {"FREQ": "14.074", "CALL": "K1ABC"}

    def test_data_can_hold_anything(self):
        parsed = adi.parse("<EOH><COMMENT:13>a <EOR> < b >\n<CALL:1>X<EOR>")
        assert parsed.records[0].fields["COMMENT"] == "a <EOR> < b >"

    def test_text_between_fields_is_ignored(self):
        parsed = adi.parse("<EOH>junk <CALL:1>X 5 < 3 more junk\n<EOR>")
        assert parsed.records[0].fields == {"CALL": "X"}

    def test_tag_without_length(self):
        record = adi.parse("<EOH><CALL:1>X<COMMENT>hi<EOR>").records[0]
        assert record.fields == {"CALL": "X"}
        assert "<COMMENT> has no length" in record.problems[0]

    def test_repeated_field(self):
        record = adi.parse("<EOH><CALL:1>X<CALL:1>Y<EOR>").records[0]
        assert record.fields == {"CALL": "X"}
        assert "'Y'" in record.problems[0]

    def test_truncated_file(self):
        parsed = adi.parse("<EOH><CALL:5>K1ABC<EOR><CALL:5>K2A")
        assert len(parsed.records) == 2
        last = parsed.records[1]
        assert last.fields == {"CALL": "K2A"}
        assert last.raw == "<CALL:5>K2A"
        assert any("past the end" in problem for problem in last.problems)
        assert any("no <EOR>" in problem for problem in last.problems)

    def test_empty_and_header_only(self):
        assert adi.parse("").records == []
        assert adi.parse("just a header<EOH>\n").records == []

    def test_byte_order_mark_and_crlf(self):
        parsed = adi.parse("﻿h<EOH>\r\n<CALL:1>X\r\n<EOR>\r\n")
        assert parsed.records[0].fields == {"CALL": "X"}

    def test_lengths_counted_in_characters(self):
        record = adi.parse("<EOH><NAME:5>Józef<QTH:3>ABC<EOR>").records[0]
        assert record.fields == {"NAME": "Józef", "QTH": "ABC"}

    @pytest.mark.parametrize("gap", ["", " ", "\n"])
    def test_lengths_counted_in_bytes(self, gap):
        # Józef is 6 bytes in UTF-8, as some loggers count it
        record = adi.parse(f"<EOH><NAME:6>Józef{gap}<QTH:3>ABC<EOR>").records[0]
        assert record.fields == {"NAME": "Józef", "QTH": "ABC"}

    def test_character_count_with_space_inside(self):
        record = adi.parse("<EOH><NAME:6>Jó zef <EOR>").records[0]
        assert record.fields == {"NAME": "Jó zef"}


class TestReadingValues:
    def read(self, **fields):
        base = {"CALL": "K1ABC", "QSO_DATE": "20240601", "TIME_ON": "1200"}
        return adif.adif_to_qso(base | fields)

    def test_date_and_time(self):
        converted = self.read(TIME_ON="120003")
        assert converted.values["time_on"] == datetime(
            2024, 6, 1, 12, 0, 3, tzinfo=UTC
        )
        assert converted.problems == []

    def test_time_off_on_the_same_day(self):
        converted = self.read(TIME_OFF="1215")
        assert converted.values["time_off"] == datetime(
            2024, 6, 1, 12, 15, tzinfo=UTC
        )

    def test_time_off_past_midnight(self):
        converted = self.read(TIME_ON="2359", TIME_OFF="0001")
        assert converted.values["time_off"] == datetime(
            2024, 6, 2, 0, 1, tzinfo=UTC
        )

    def test_time_off_with_its_own_date(self):
        converted = self.read(QSO_DATE_OFF="20240603", TIME_OFF="0001")
        assert converted.values["time_off"] == datetime(
            2024, 6, 3, 0, 1, tzinfo=UTC
        )

    @pytest.mark.parametrize(
        "fields",
        [
            {"QSO_DATE": "2024-06-01"},
            {"QSO_DATE": "20241301"},
            {"TIME_ON": "12"},
            {"TIME_ON": "2500"},
            {"TIME_ON": ""},
        ],
    )
    def test_unreadable_time_on_is_kept(self, fields):
        converted = self.read(**fields)
        assert "time_on" not in converted.values
        assert converted.problems
        for key, value in fields.items():
            if value:
                assert converted.extra[key] == value

    def test_unreadable_time_off_is_kept(self):
        converted = self.read(TIME_OFF="99")
        assert "time_off" not in converted.values
        assert converted.extra["TIME_OFF"] == "99"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("N051 30.000", "51.5"),
            ("s033 51.407", "-33.856783"),
            ("N051 30", "51.5"),
            ("51.5", "51.5"),
            ("-33.856785", "-33.856785"),
        ],
    )
    def test_locations(self, value, expected):
        assert self.read(LAT=value).values["lat"] == Decimal(expected)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("LAT", "E051 30.000"),
            ("LON", "N051 30.000"),
            ("LAT", "N051 60.000"),
            ("LAT", "N091 00.000"),
            ("LAT", "somewhere"),
            ("LAT", "NaN"),
        ],
    )
    def test_bad_locations_are_kept(self, field, value):
        converted = self.read(**{field: value})
        assert field.lower() not in converted.values
        assert converted.extra[field] == value

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("FREQ", "14.074.1"),
            ("FREQ", "Infinity"),
            ("DXCC", "two hundred"),
            ("DXCC", "291.5"),
            ("BAND", "x" * 11),
        ],
    )
    def test_unreadable_values_are_kept(self, field, value):
        converted = self.read(**{field: value})
        assert converted.extra[field] == value
        assert converted.problems

    def test_numbers(self):
        converted = self.read(FREQ="14.0740", DXCC="0291", CQZ="5.0")
        assert converted.values["freq"] == Decimal("14.074")
        assert converted.values["dxcc"] == 291
        assert converted.values["cqz"] == 5

    def test_other_fields_go_in_extra(self):
        converted = self.read(QSLMSG="TNX", my_field="1", **{"9X": "2"})
        assert converted.extra == {
            "QSLMSG": "TNX",
            "MY_FIELD": "1",
            "X_9X": "2",
        }

    def test_empty_fields_are_dropped(self):
        converted = self.read(NAME="", QSLMSG="")
        assert "name" not in converted.values
        assert converted.extra == {}

    def test_uuid(self):
        assert self.read(APP_QSOLOGY_UUID=str(UUID)).uuid == str(UUID)

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("qslmsg", "QSLMSG"),
            ("my field", "MY_FIELD"),
            ("2nd", "X_2ND"),
            ("", "X"),
            ("a" * 80, "A" * 64),
            ("trailing_", "TRAILING"),
        ],
    )
    def test_clean_field_name(self, name, expected):
        assert adif.clean_field_name(name) == expected


class TestADX:
    def test_golden(self, sample):
        text = exports.export_text([sample], "adx", CREATED)
        assert text.startswith(
            '<?xml version="1.0" encoding="UTF-8"?>\n<ADX>\n<HEADER>'
            "<ADIF_VER>3.1.5</ADIF_VER><PROGRAMID>qsology</PROGRAMID>"
            "<PROGRAMVERSION>2026.01</PROGRAMVERSION>"
            "<CREATED_TIMESTAMP>20240701 093000</CREATED_TIMESTAMP>"
            '<USERDEF FIELDID="1" TYPE="S">EPC</USERDEF>'
            "</HEADER>\n<RECORDS>\n<RECORD><CALL>K1ABC</CALL>"
        )
        assert "<COMMENT>line one&#13;\nline two</COMMENT>" in text
        assert (
            '<APP PROGRAMID="N1MM" FIELDNAME="EXCHANGE1" TYPE="S">14</APP>'
            in text
        )
        assert '<USERDEF FIELDNAME="EPC">1234</USERDEF>' in text
        assert "<NAME_INTL>Józef</NAME_INTL>" in text
        assert text.endswith("</RECORD>\n</RECORDS>\n</ADX>\n")

    def test_reads_back(self, sample):
        parsed = adx.parse(exports.export_text([sample], "adx", CREATED))
        assert parsed.header["ADIF_VER"] == "3.1.5"
        fields = parsed.records[0].fields
        assert fields["COMMENT"] == "line one\r\nline two"
        assert fields["NAME_INTL"] == "Józef"
        assert fields["EPC"] == "1234"
        assert fields["APP_N1MM_EXCHANGE1"] == "14"
        assert fields["APP_QSOLOGY_UUID"] == str(UUID)

    def test_app_field_without_a_name(self, sample):
        sample.extra = {"APP_X": "1", "APP__Y": "2"}
        parsed = adx.parse(exports.export_text([sample], "adx"))
        assert parsed.records[0].fields["APP_X"] == "1"
        assert parsed.records[0].fields["APP__Y"] == "2"

    def test_escaping(self, sample):
        sample.comment = "<tag> & \"quotes\" 'too'"
        parsed = adx.parse(exports.export_text([sample], "adx"))
        assert parsed.records[0].fields["COMMENT"] == sample.comment

    def test_unwritable_characters_are_replaced(self, sample):
        sample.comment = "bad\x01char"
        parsed = adx.parse(exports.export_text([sample], "adx"))
        assert parsed.records[0].fields["COMMENT"] == "bad�char"

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("<ADX><RECORDS>", "Not valid XML"),
            ("not xml", "Not valid XML"),
            ("<OTHER/>", "Expected <ADX>"),
        ],
    )
    def test_invalid(self, text, message):
        with pytest.raises(ValueError, match=message):
            adx.parse(text)

    def test_no_records(self):
        assert adx.parse("<ADX><HEADER/></ADX>").records == []

    def test_repeated_field(self):
        record = adx.parse(
            "<ADX><RECORDS><RECORD><CALL>X</CALL><CALL>Y</CALL></RECORD>"
            "</RECORDS></ADX>"
        ).records[0]
        assert record.fields == {"CALL": "X"}
        assert record.problems


class TestJSON:
    def test_golden(self, sample):
        text = exports.export_text([sample], "json", CREATED)
        document = json.loads(text)
        assert document["format"] == "qsology"
        assert document["version"] == 1
        assert document["created"] == "2024-07-01T09:30:00Z"
        assert document["qsos"] == [
            {
                "uuid": str(UUID),
                "call": "K1ABC",
                "time_on": "2024-06-01T12:00:03Z",
                "time_off": "2024-06-01T12:01:00Z",
                "band": "20m",
                "freq": "14.074",
                "mode": "FT8",
                "rst_sent": "-10",
                "name": "Józef",
                "lat": "-33.856785",
                "lon": "151.215296",
                "dxcc": 291,
                "station_callsign": "M0WME/P",
                "my_pota_ref": "US-0001,US-0002",
                "comment": "line one\r\nline two",
                "extra": {
                    "APP_N1MM_EXCHANGE1": "14",
                    "EPC": "1234",
                    "NAME_INTL": "Józef",
                    "QSLMSG": "TNX",
                },
            }
        ]

    def test_one_qso_per_line(self, sample):
        lines = exports.export_text([sample, sample], "json").splitlines()
        assert len(lines) == 4

    def test_empty_log(self):
        assert json.loads(exports.export_text([], "json"))["qsos"] == []

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("{", "Not valid JSON"),
            ("[]", "Not a qsology"),
            ('{"format": "other"}', "Not a qsology"),
            ('{"format": "qsology", "version": 2}', "version 2"),
            ('{"format": "qsology", "version": 1}', "no list"),
        ],
    )
    def test_invalid(self, text, message):
        with pytest.raises(ValueError, match=message):
            jsonfmt.parse(text)

    def test_records_that_arent_objects(self):
        parsed = jsonfmt.parse(
            '{"format": "qsology", "version": 1, "qsos": [1, {"call": "X"}]}'
        )
        assert parsed.records[0].problems
        assert parsed.records[0].raw == "1"
        assert parsed.records[1].data == {"call": "X"}


@pytest.mark.django_db
class TestStreaming:
    def test_queryset_is_streamed_in_time_order(self, log, user):
        later = log(time_on=datetime(2024, 6, 2, tzinfo=UTC))
        earlier = log(time_on=datetime(2024, 6, 1, tzinfo=UTC))
        chunks = list(exports.export(QSO.objects.all(), "json"))
        assert len(chunks) == 4
        assert str(earlier.uuid) in chunks[1]
        assert str(later.uuid) in chunks[2]

    def test_export_to_stream(self, log, tmp_path):
        log()
        path = tmp_path / "log.adi"
        with path.open("w", encoding="utf-8", newline="") as stream:
            exports.export_to(stream, QSO.objects.all(), "adi")
        assert len(adi.parse(path.read_text("utf-8")).records) == 1
