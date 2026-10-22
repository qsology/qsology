"""Maidenhead locator (grid square) conversion

A locator is built from pairs of characters, each pair splitting the cell
above it into smaller cells:

    field      AA-RR    18 x 18, 20 x 10 degrees
    square     00-99    10 x 10,  2 x 1 degrees
    subsquare  aa-xx    24 x 24,  5 x 2.5 minutes
    extended   00-99    10 x 10, 30 x 15 seconds
    (and so on, alternating between 24 and 10 divisions)

Locators are written with the field upper case and the subsquare lower case,
e.g. IO91wm"""

import re
from decimal import Decimal
from typing import NamedTuple

VALID_PRECISIONS = (2, 4, 6, 8, 10)
DEFAULT_PRECISION = 6

_GRID_RE = re.compile(
    r"^[A-R]{2}(?:[0-9]{2}(?:[A-X]{2}(?:[0-9]{2}(?:[A-X]{2})?)?)?)?$",
    re.IGNORECASE,
)


class LatLon(NamedTuple):
    latitude: float
    longitude: float


def _divisions(precision: int) -> list[int]:
    """How many cells each pair splits the one above it into"""
    pairs = precision // 2
    return [18] + [10 if i % 2 else 24 for i in range(1, pairs)]


def is_valid_grid(value: str) -> bool:
    return bool(value) and bool(_GRID_RE.match(value))


def normalize_grid(value: str) -> str:
    """Normalize the case of a locator, e.g. io91WM -> IO91wm

    Raises ValueError for anything that isn't a locator"""
    value = value.strip()
    if not is_valid_grid(value):
        raise ValueError(f"{value!r} is not a valid Maidenhead locator")
    return (
        value[:4].upper() + value[4:6].lower() + value[6:8] + value[8:].lower()
    )


def _exact(value: float | Decimal) -> Decimal:
    """A coordinate as an exact decimal, floats by their shortest repr"""
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError(f"{value} is out of range")
    return number


def latlon_to_grid(
    latitude: float | Decimal,
    longitude: float | Decimal,
    precision: int = DEFAULT_PRECISION,
) -> str:
    """Convert a latitude and longitude to a locator

    The north pole and the antimeridian are treated as the last cell before
    them, so 90N and 180E are both still on the grid"""
    if precision not in VALID_PRECISIONS:
        raise ValueError(f"Precision must be one of {VALID_PRECISIONS}")
    # Exact decimals, so a point on a cell edge lands in the same cell at
    # every precision, e.g. -1.9 as a float is a hair under -1.9
    latitude = _exact(latitude)
    longitude = _exact(longitude)
    if not -90 <= latitude <= 90:
        raise ValueError(f"Latitude {latitude} is out of range")
    if not -180 <= longitude <= 180:
        raise ValueError(f"Longitude {longitude} is out of range")

    # Work in whole multiples of the smallest cell to avoid rounding drift
    divisions = _divisions(precision)
    cells = 1
    for division in divisions:
        cells *= division
    lon_cell = min(int((longitude + 180) * cells / 360), cells - 1)
    lat_cell = min(int((latitude + 90) * cells / 180), cells - 1)

    result = []
    for index, division in enumerate(divisions):
        cells //= division
        lon_digit, lon_cell = divmod(lon_cell, cells)
        lat_digit, lat_cell = divmod(lat_cell, cells)
        if division == 10:
            result.append(f"{lon_digit}{lat_digit}")
        else:
            letters = chr(ord("A") + lon_digit) + chr(ord("A") + lat_digit)
            result.append(letters if index == 0 else letters.lower())
    return normalize_grid("".join(result))


def grid_bounds(value: str) -> tuple[LatLon, LatLon]:
    """The south west and north east corners of a locator's cell"""
    grid = normalize_grid(value).upper()
    divisions = _divisions(len(grid))
    lon = lat = 0.0
    lon_size, lat_size = 360.0, 180.0
    for index, division in enumerate(divisions):
        pair = grid[index * 2 : index * 2 + 2]
        lon_size /= division
        lat_size /= division
        if division == 10:
            lon += int(pair[0]) * lon_size
            lat += int(pair[1]) * lat_size
        else:
            lon += (ord(pair[0]) - ord("A")) * lon_size
            lat += (ord(pair[1]) - ord("A")) * lat_size
    south_west = LatLon(lat - 90, lon - 180)
    return south_west, LatLon(
        south_west.latitude + lat_size, south_west.longitude + lon_size
    )


def grid_to_latlon(value: str) -> LatLon:
    """The center of a locator's cell"""
    south_west, north_east = grid_bounds(value)
    return LatLon(
        (south_west.latitude + north_east.latitude) / 2,
        (south_west.longitude + north_east.longitude) / 2,
    )
