import pytest
from apps.callsigns.models import (
    ParsedCallsign,
    format_callsign,
    normalize_callsign,
    normalize_callsign_part,
    parse_callsign,
)


class TestNormalizeCallsign:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("M0WME", "M0WME"),
            ("m0wme", "M0WME"),
            ("M0wMe", "M0WME"),
            ("  m0wme  ", "M0WME"),
            ("m0 wme", "M0WME"),
            ("zl/m0wme/p", "ZL/M0WME/P"),
        ],
    )
    def test_normalize_callsign(self, value, expected):
        assert normalize_callsign(value) == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("p", "P"), ("/p", "P"), ("zl/", "ZL"), (" /mm ", "MM"), ("", "")],
    )
    def test_normalize_callsign_part(self, value, expected):
        assert normalize_callsign_part(value) == expected


class TestParseCallsign:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("M0WME", ("", "M0WME", "")),
            ("m0wme", ("", "M0WME", "")),
            ("M0WME/P", ("", "M0WME", "P")),
            ("M0WME/MM", ("", "M0WME", "MM")),
            ("ZL/M0WME", ("ZL", "M0WME", "")),
            ("zl/m0wme/p", ("ZL", "M0WME", "P")),
            ("VK9/M0WME/AM", ("VK9", "M0WME", "AM")),
            # Some countries use the country as a suffix
            ("W1AW/VE3", ("", "W1AW", "VE3")),
            ("F/K4GBI", ("F", "K4GBI", "")),
        ],
    )
    def test_parse(self, value, expected):
        assert parse_callsign(value) == ParsedCallsign(*expected)

    def test_tie_is_read_as_suffix(self):
        assert parse_callsign("K1AB/VE3A") == ParsedCallsign("", "K1AB", "VE3A")

    @pytest.mark.parametrize(
        "value", ["", "/", "M0WME/", "/M0WME", "ZL//M0WME", "A/B/C/D"]
    )
    def test_invalid(self, value):
        with pytest.raises(ValueError, match="not a valid callsign"):
            parse_callsign(value)


class TestFormatCallsign:
    @pytest.mark.parametrize(
        ("parts", "expected"),
        [
            (("", "M0WME", ""), "M0WME"),
            (("", "M0WME", "P"), "M0WME/P"),
            (("ZL", "M0WME", ""), "ZL/M0WME"),
            (("ZL", "M0WME", "P"), "ZL/M0WME/P"),
        ],
    )
    def test_format(self, parts, expected):
        assert format_callsign(*parts) == expected

    @pytest.mark.parametrize(
        "value", ["M0WME", "M0WME/P", "ZL/M0WME", "ZL/M0WME/P"]
    )
    def test_round_trip(self, value):
        assert format_callsign(*parse_callsign(value)) == value
