from decimal import Decimal

import pytest
from apps.common import http
from apps.qrz.client import (
    QRZAuthError,
    QRZClient,
    QRZError,
    QRZNotFound,
    QRZResult,
)

from .conftest import (
    SESSION_KEY,
    expired,
    found,
    login_failed,
    login_ok,
    not_found,
    qrz_xml,
)


class FakeFetch:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, params=None, timeout=30):
        self.calls.append(dict(params or {}, url=url))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def client(*responses, session_key=""):
    fetch = FakeFetch(*responses)
    return QRZClient("m0wme", "hunter2", session_key, fetch=fetch), fetch


class TestQRZResult:
    def test_qso_fields(self):
        raw = {
            "call": "AA7BQ",
            "name_fmt": "FRED LLOYD",
            "addr2": "SCOTTSDALE",
            "state": "AZ",
            "county": "Maricopa",
            "country": "United States",
            "grid": "DM32af",
            "lat": "34.23456",
            "lon": "-112.34356",
            "dxcc": "291",
            "cqzone": "3",
            "ituzone": "2",
            "iota": "NA-001",
            "email": "flloyd@qrz.com",
            "qslmgr": "W1AW",
        }
        assert QRZResult("AA7BQ", raw).qso_fields() == {
            "name": "FRED LLOYD",
            "qth": "SCOTTSDALE",
            "state": "AZ",
            "cnty": "Maricopa",
            "country": "United States",
            "gridsquare": "DM32af",
            "lat": Decimal("34.23456"),
            "lon": Decimal("-112.34356"),
            "dxcc": 291,
            "cqz": 3,
            "ituz": 2,
            "iota": "NA-001",
            "email": "flloyd@qrz.com",
            "qsl_via": "W1AW",
        }

    @pytest.mark.parametrize(
        ("raw", "name"),
        [
            (
                {"name_fmt": "Fred Lloyd", "fname": "X", "name": "Y"},
                "Fred Lloyd",
            ),
            ({"fname": "FRED", "name": "LLOYD"}, "FRED LLOYD"),
            (
                {"nickname": "Bud", "fname": "FRED", "name": "LLOYD"},
                "Bud LLOYD",
            ),
            ({"name": "LLOYD"}, "LLOYD"),
            ({"fname": "FRED"}, "FRED"),
            ({}, ""),
        ],
    )
    def test_name(self, raw, name):
        assert QRZResult("AA7BQ", raw).name == name

    def test_empty_and_unreadable_values_left_out(self):
        raw = {"addr2": "", "lat": "north", "dxcc": "", "cqzone": "three"}
        assert QRZResult("AA7BQ", raw).qso_fields() == {}


class TestLogin:
    def test_success(self, settings):
        qrz, fetch = client(login_ok())
        assert qrz.login() == SESSION_KEY
        assert qrz.session_key == SESSION_KEY
        assert fetch.calls == [
            {
                "url": settings.QRZ_XML_URL,
                "username": "m0wme",
                "password": "hunter2",
                "agent": settings.QRZ_AGENT,
            }
        ]

    def test_refused(self):
        qrz, _ = client(login_failed())
        with pytest.raises(QRZAuthError, match="incorrect"):
            qrz.login()
        assert qrz.session_key == ""

    def test_no_key_or_error(self):
        qrz, _ = client(qrz_xml())
        with pytest.raises(QRZAuthError, match="login failed"):
            qrz.login()


class TestLookup:
    def test_logs_in_first(self):
        qrz, fetch = client(login_ok(), found())
        result = qrz.lookup(" aa7bq ")

        assert result.callsign == "AA7BQ"
        assert result.raw["grid"] == "DM32af"
        assert result.qso_fields()["name"] == 'FRED "The Boss" LLOYD'
        assert len(fetch.calls) == 2
        assert fetch.calls[1]["s"] == SESSION_KEY
        assert fetch.calls[1]["callsign"] == "AA7BQ"

    def test_reuses_session(self):
        qrz, fetch = client(found(), session_key="existing")
        qrz.lookup("AA7BQ")
        assert len(fetch.calls) == 1
        assert fetch.calls[0]["s"] == "existing"

    def test_subscription_message(self):
        message = "A subscription is required to access the complete record."
        qrz, _ = client(found(message=message), session_key="k")
        assert qrz.lookup("AA7BQ").message == message

    def test_not_found(self):
        qrz, _ = client(not_found("XX9XXX"), session_key="k")
        with pytest.raises(QRZNotFound, match="XX9XXX"):
            qrz.lookup("XX9XXX")

    def test_no_callsign(self):
        qrz, fetch = client()
        with pytest.raises(QRZNotFound):
            qrz.lookup("  ")
        assert fetch.calls == []

    @pytest.mark.parametrize(
        "error", ["Session Timeout", "Invalid session key"]
    )
    def test_expired_session_logs_in_once_and_retries(self, error):
        qrz, fetch = client(
            qrz_xml(f"<Error>{error}</Error>"),
            login_ok("fresh"),
            found(),
            session_key="stale",
        )
        assert qrz.lookup("AA7BQ").callsign == "AA7BQ"
        assert qrz.session_key == "fresh"
        assert [call.get("s") for call in fetch.calls] == [
            "stale",
            None,
            "fresh",
        ]

    def test_session_that_keeps_expiring(self):
        qrz, fetch = client(
            expired(), login_ok(), expired(), session_key="stale"
        )
        with pytest.raises(QRZError, match="Session Timeout"):
            qrz.lookup("AA7BQ")
        # One login and one retry, never a loop
        assert len(fetch.calls) == 3

    def test_relogin_refused(self):
        qrz, _ = client(expired(), login_failed(), session_key="stale")
        with pytest.raises(QRZAuthError):
            qrz.lookup("AA7BQ")

    def test_other_error(self):
        qrz, _ = client(
            qrz_xml("<Error>Something odd</Error>"), session_key="k"
        )
        with pytest.raises(QRZError, match="Something odd"):
            qrz.lookup("AA7BQ")

    def test_no_record_and_no_error(self):
        qrz, _ = client(qrz_xml("<Key>k</Key>"), session_key="k")
        with pytest.raises(QRZError, match="no record"):
            qrz.lookup("AA7BQ")

    def test_invalid_xml(self):
        qrz, _ = client(b"<html>Bad gateway", session_key="k")
        with pytest.raises(QRZError, match="invalid XML"):
            qrz.lookup("AA7BQ")

    def test_http_error(self):
        qrz, _ = client(http.HTTPError("timed out"), session_key="k")
        with pytest.raises(QRZError, match="timed out"):
            qrz.lookup("AA7BQ")

    def test_uses_http_by_default(self, monkeypatch):
        fetch = FakeFetch(found())
        monkeypatch.setattr(http, "get", fetch)
        QRZClient("m0wme", "hunter2", "k").lookup("AA7BQ")
        assert len(fetch.calls) == 1
