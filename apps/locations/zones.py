"""CQ and ITU zones from a position or grid square

The zone boundaries are polygons (see data/README.md), so a zone is found by
which polygon a point is in. Points on a border can be in two zones and the
poles aren't in any, there's no zone for those rather than a guess, unless
the DXCC entity settles it.

Most points are well inside a zone, so each small cell of the map that no
border passes through is looked up once and remembered"""

import functools
import json
import math
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

from apps.locations import maidenhead

DATA_DIR = Path(__file__).parent / "data"

# The size of the cells of the map, in degrees
CELL_SIZE = 0.25
# How far into a grid square's corners to look, so the corner isn't on the
# edge of the square next to it
_INSET = 1e-6
# How many points along each side of a grid square to look at
_GRID_SAMPLES = 9


class Zones(NamedTuple):
    cq: int | None
    itu: int | None


class _Polygon(NamedTuple):
    zone: int
    west: float
    south: float
    east: float
    north: float
    ring: list[tuple[float, float]]

    def contains(self, longitude: float, latitude: float) -> bool:
        if not (
            self.west <= longitude <= self.east
            and self.south <= latitude <= self.north
        ):
            return False
        # Ray casting, counting the edges crossed heading east
        inside = False
        ring = self.ring
        x2, y2 = ring[-1]
        for x1, y1 in ring:
            if (y1 > latitude) != (y2 > latitude) and longitude < (x2 - x1) * (
                latitude - y1
            ) / (y2 - y1) + x1:
                inside = not inside
            x2, y2 = x1, y1
        return inside


_CELLS_AROUND = round(360 / CELL_SIZE)


def _cell(latitude: float, longitude: float) -> tuple[int, int]:
    """The cell a point is in, with longitude wrapped around"""
    return (
        math.floor(latitude / CELL_SIZE),
        math.floor(longitude / CELL_SIZE) % _CELLS_AROUND,
    )


def _cell_middle(cell: tuple[int, int]) -> tuple[float, float]:
    latitude = (cell[0] + 0.5) * CELL_SIZE
    longitude = (cell[1] + 0.5) * CELL_SIZE
    return latitude, longitude - 360 if longitude > 180 else longitude


def _cells_between(low: float, high: float) -> range:
    """The cells a span of degrees touches, with a little to spare for a
    border along the edge of a cell"""
    return range(
        math.floor((low - _INSET) / CELL_SIZE),
        math.floor((high + _INSET) / CELL_SIZE) + 1,
    )


class ZoneMap:
    """The zones of one kind, CQ or ITU"""

    def __init__(self, polygons: Iterable[_Polygon]):
        self.polygons = list(polygons)
        self._cache: dict[tuple[int, int], frozenset[int]] = {}
        # Every cell a border might pass through, generously, as anything
        # missed here gives a wrong zone
        self._border_cells = set()
        for polygon in self.polygons:
            ring = polygon.ring
            for (x1, y1), (x2, y2) in zip(
                ring, ring[1:] + ring[:1], strict=True
            ):
                for lat in _cells_between(min(y1, y2), max(y1, y2)):
                    for lon in _cells_between(min(x1, x2), max(x1, x2)):
                        self._border_cells.add((lat, lon % _CELLS_AROUND))

    @classmethod
    def load(cls, path: Path, key: str) -> ZoneMap:
        with path.open() as file:
            features = json.load(file)["features"]
        polygons = []
        for feature in features:
            # The files only have simple polygons, the first ring is the
            # outside and there are no holes
            ring = [(x, y) for x, y in feature["geometry"]["coordinates"][0]]
            polygons.append(
                _Polygon(
                    zone=feature["properties"][key],
                    west=min(x for x, _ in ring),
                    south=min(y for _, y in ring),
                    east=max(x for x, _ in ring),
                    north=max(y for _, y in ring),
                    ring=ring,
                )
            )
        return cls(polygons)

    def _search(self, latitude: float, longitude: float) -> frozenset[int]:
        # Polygons crossing the antimeridian run past ±180, so look a turn
        # either way too
        return frozenset(
            polygon.zone
            for polygon in self.polygons
            for lon in (longitude, longitude - 360, longitude + 360)
            if polygon.contains(lon, latitude)
        )

    def candidates(
        self, latitude: float | Decimal, longitude: float | Decimal
    ) -> frozenset[int]:
        """Every zone the point is in, usually one"""
        latitude, longitude = float(latitude), float(longitude)
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError(f"{latitude}, {longitude} is out of range")
        cell = _cell(latitude, longitude)
        if cell in self._border_cells:
            return self._search(latitude, longitude)
        if cell not in self._cache:
            self._cache[cell] = self._search(*_cell_middle(cell))
        return self._cache[cell]

    def at(
        self,
        latitude: float | Decimal,
        longitude: float | Decimal,
        allowed: Iterable[int] = (),
    ) -> int | None:
        """The zone the point is in, or None when it's not in one or is on a
        border. allowed, e.g. the zones of the DXCC entity, settles a point
        on a border"""
        return _only(self.candidates(latitude, longitude), allowed)

    def for_grid(self, grid: str, allowed: Iterable[int] = ()) -> int | None:
        """The zone a grid square is in

        Squares bigger than a subsquare (6 characters) must be all in one
        zone, or in only one of allowed, smaller ones are taken to be where
        their middle is"""
        (south, west), (north, east) = maidenhead.grid_bounds(grid)
        middle = ((south + north) / 2, (west + east) / 2)
        if len(grid.strip()) >= 6:
            return self.at(*middle, allowed=allowed)
        # Corners, edges and across the middle, a zone could still slip
        # between them but only a sliver of one
        points = [
            (latitude, longitude)
            for latitude in _spread(south, north)
            for longitude in _spread(west, east)
        ]
        found = frozenset().union(
            *(self.candidates(*point) for point in points)
        )
        return _only(found, allowed)


def _spread(low: float, high: float) -> list[float]:
    """Evenly spaced points from just inside low to just inside high"""
    low, high = low + _INSET, high - _INSET
    step = (high - low) / (_GRID_SAMPLES - 1)
    return [low + step * i for i in range(_GRID_SAMPLES)]


def _only(candidates: frozenset[int], allowed: Iterable[int]) -> int | None:
    if len(candidates) > 1:
        candidates = candidates & frozenset(allowed)
    return next(iter(candidates)) if len(candidates) == 1 else None


@functools.cache
def cq_zones() -> ZoneMap:
    return ZoneMap.load(DATA_DIR / "cqzones.geojson", "cq_zone_number")


@functools.cache
def itu_zones() -> ZoneMap:
    return ZoneMap.load(DATA_DIR / "ituzones.geojson", "itu_zone_number")


def zones_at(latitude, longitude, dxcc=None) -> Zones:
    """The CQ and ITU zones at a point, either can be None. dxcc is the
    DxccEntity, used to settle points on a border"""
    return Zones(
        cq_zones().at(latitude, longitude, _allowed(dxcc, "cq_zones")),
        itu_zones().at(latitude, longitude, _allowed(dxcc, "itu_zones")),
    )


def zones_for_grid(grid: str, dxcc=None) -> Zones:
    """The CQ and ITU zones of a grid square, either can be None

    Raises ValueError for anything that isn't a grid square"""
    return Zones(
        cq_zones().for_grid(grid, _allowed(dxcc, "cq_zones")),
        itu_zones().for_grid(grid, _allowed(dxcc, "itu_zones")),
    )


def _allowed(dxcc, attribute: str) -> list[int]:
    return list(getattr(dxcc, attribute, None) or [])
