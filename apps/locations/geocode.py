"""Turn an address into coordinates and a grid square

Uses Nominatim, the OpenStreetMap geocoder, which is free to use. The public
server asks for no more than one request a second and an identifying user
agent, see https://operations.osmfoundation.org/policies/nominatim/. A self
hosted Nominatim can be used instead by changing GEOCODER_URL"""

import json
import threading
import time
from dataclasses import dataclass

from apps.common import http
from apps.locations import maidenhead
from django.conf import settings


class GeocodeError(Exception):
    """The geocoder couldn't be reached or gave a bad response"""


@dataclass(frozen=True)
class GeocodeResult:
    latitude: float
    longitude: float
    display_name: str
    country: str = ""
    country_code: str = ""
    state: str = ""
    county: str = ""

    @property
    def gridsquare(self) -> str:
        return maidenhead.latlon_to_grid(self.latitude, self.longitude)


_lock = threading.Lock()
_last_request = 0.0


def _throttle() -> None:
    """Keep to the geocoder's request rate, per process"""
    global _last_request
    with _lock:
        wait = settings.GEOCODER_MIN_INTERVAL - (
            time.monotonic() - _last_request
        )
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()


def geocode(
    address: str, fetch: http.Fetch | None = None
) -> GeocodeResult | None:
    """Find the best match for an address, or None if there isn't one"""
    address = address.strip()
    if not address:
        return None
    fetch = fetch or http.get
    _throttle()
    try:
        body = fetch(
            settings.GEOCODER_URL,
            params={
                "q": address,
                "format": "jsonv2",
                "limit": "1",
                "addressdetails": "1",
            },
        )
        results = json.loads(body)
    except http.HTTPError as exc:
        raise GeocodeError(str(exc)) from exc
    except ValueError as exc:
        raise GeocodeError("The geocoder sent back invalid JSON") from exc
    if not isinstance(results, list):
        raise GeocodeError("The geocoder sent back an unexpected response")
    if not results:
        return None
    best = results[0]
    if not isinstance(best, dict):
        raise GeocodeError("The geocoder result had no coordinates")
    details = best.get("address")
    if not isinstance(details, dict):
        details = {}
    try:
        return GeocodeResult(
            latitude=float(best["lat"]),
            longitude=float(best["lon"]),
            display_name=best.get("display_name", ""),
            country=details.get("country", ""),
            country_code=details.get("country_code", "").upper(),
            state=details.get("state", ""),
            county=details.get("county", ""),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise GeocodeError("The geocoder result had no coordinates") from exc


def address_to_grid(address: str, **kwargs) -> str | None:
    """The 6 character grid square for an address, or None if not found"""
    result = geocode(address, **kwargs)
    return result.gridsquare if result else None
