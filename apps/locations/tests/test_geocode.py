import json

import pytest
from apps.common import http
from apps.locations import geocode
from apps.locations.geocode import GeocodeError, GeocodeResult

W1AW = {
    "lat": "41.714775",
    "lon": "-72.727260",
    "display_name": "225 Main Street, Newington, Connecticut",
    "address": {
        "country": "United States",
        "country_code": "us",
        "state": "Connecticut",
        "county": "Hartford County",
    },
}


def respond(value) -> bytes:
    return json.dumps(value).encode()


class TestGeocode:
    def test_found(self, fake_fetch, settings):
        fetch = fake_fetch(respond([W1AW]))
        result = geocode.geocode(" 225 Main St, Newington ", fetch=fetch)

        assert result == GeocodeResult(
            latitude=41.714775,
            longitude=-72.72726,
            display_name="225 Main Street, Newington, Connecticut",
            country="United States",
            country_code="US",
            state="Connecticut",
            county="Hartford County",
        )
        assert result.gridsquare == "FN31pr"
        (call,) = fetch.calls
        assert call["url"] == settings.GEOCODER_URL
        assert call["params"] == {
            "q": "225 Main St, Newington",
            "format": "jsonv2",
            "limit": "1",
            "addressdetails": "1",
        }

    def test_without_address_details(self, fake_fetch):
        fetch = fake_fetch(respond([{"lat": "51.5074", "lon": "-0.1278"}]))
        result = geocode.geocode("London", fetch=fetch)
        assert result.gridsquare == "IO91wm"
        assert result.country == ""
        assert result.display_name == ""

    def test_odd_address_details_ignored(self, fake_fetch):
        fetch = fake_fetch(
            respond([{"lat": "1", "lon": "1", "address": "somewhere"}])
        )
        assert geocode.geocode("x", fetch=fetch).country == ""

    def test_not_found(self, fake_fetch):
        assert geocode.geocode("Nowhere", fetch=fake_fetch(b"[]")) is None

    @pytest.mark.parametrize("address", ["", "   "])
    def test_empty_address_makes_no_request(self, fake_fetch, address):
        fetch = fake_fetch()
        assert geocode.geocode(address, fetch=fetch) is None
        assert fetch.calls == []

    def test_http_error(self, fake_fetch):
        fetch = fake_fetch(http.HTTPError("connection refused"))
        with pytest.raises(GeocodeError, match="connection refused"):
            geocode.geocode("London", fetch=fetch)

    def test_invalid_json(self, fake_fetch):
        with pytest.raises(GeocodeError, match="invalid JSON"):
            geocode.geocode("London", fetch=fake_fetch(b"<html>"))

    def test_not_a_list(self, fake_fetch):
        fetch = fake_fetch(respond({"error": "Rate limited"}))
        with pytest.raises(GeocodeError, match="unexpected"):
            geocode.geocode("London", fetch=fetch)

    @pytest.mark.parametrize(
        "result",
        [{"display_name": "x"}, {"lat": "north", "lon": "1"}, "nope"],
    )
    def test_no_coordinates(self, fake_fetch, result):
        with pytest.raises(GeocodeError, match="no coordinates"):
            geocode.geocode("London", fetch=fake_fetch(respond([result])))

    def test_uses_http_by_default(self, fake_fetch, monkeypatch):
        fetch = fake_fetch(respond([W1AW]))
        monkeypatch.setattr(http, "get", fetch)
        assert geocode.geocode("W1AW") is not None
        assert len(fetch.calls) == 1


class TestAddressToGrid:
    def test_found(self, fake_fetch):
        fetch = fake_fetch(respond([W1AW]))
        assert geocode.address_to_grid("W1AW", fetch=fetch) == "FN31pr"

    def test_not_found(self, fake_fetch):
        assert geocode.address_to_grid("x", fetch=fake_fetch(b"[]")) is None


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class TestThrottle:
    @pytest.fixture
    def clock(self, monkeypatch, settings):
        settings.GEOCODER_MIN_INTERVAL = 1.0
        clock = FakeClock()
        monkeypatch.setattr(geocode.time, "monotonic", clock.monotonic)
        monkeypatch.setattr(geocode.time, "sleep", clock.sleep)
        monkeypatch.setattr(geocode, "_last_request", 0.0)
        return clock

    def test_first_request_doesnt_wait(self, clock):
        geocode._throttle()
        assert clock.slept == []

    def test_waits_between_requests(self, clock):
        geocode._throttle()
        clock.now += 0.25
        geocode._throttle()
        assert clock.slept == [pytest.approx(0.75)]

    def test_no_wait_once_interval_passed(self, clock):
        geocode._throttle()
        clock.now += 1.5
        geocode._throttle()
        assert clock.slept == []

    def test_geocode_is_throttled(self, clock, fake_fetch):
        fetch = fake_fetch(respond([]), respond([]))
        geocode.geocode("a", fetch=fetch)
        geocode.geocode("b", fetch=fetch)
        assert clock.slept == [pytest.approx(1.0)]

    def test_disabled_in_tests(self, settings):
        # The test settings turn the wait off
        from config.settings import test

        assert test.GEOCODER_MIN_INTERVAL == 0
