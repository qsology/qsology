from decimal import Decimal

import pytest
from apps.locations import zones
from apps.locations.models import DxccEntity, Location
from hypothesis import given
from hypothesis import strategies as st

# Somewhere on the border of CQ zones 14 and 40, off the Faroes
BORDER = (62.5, -12.5)

PLACES = {
    "London": ((51.5074, -0.1278), (14, 27)),
    "Reykjavik": ((64.15, -21.94), (40, 17)),
    "Moscow": ((55.75, 37.62), (16, 29)),
    "Cape Town": ((-33.92, 18.42), (38, 57)),
    "Buenos Aires": ((-34.6, -58.38), (13, 14)),
    "New York": ((40.71, -74.0), (5, 8)),
    "Los Angeles": ((34.05, -118.24), (3, 6)),
    "Acadia": ((44.31, -68.2034), (5, 8)),
    "Nome": ((64.5, -165.4), (1, 1)),
    "Honolulu": ((21.31, -157.86), (31, 61)),
    "Tokyo": ((35.68, 139.69), (25, 45)),
    "Sydney": ((-33.87, 151.21), (30, 59)),
    # Either side of the antimeridian, where the zones run past ±180
    "Fiji": ((-18.0, 178.0), (32, 56)),
    "Samoa": ((-13.83, -171.76), (32, 62)),
}

latitudes = st.floats(-90, 90, allow_nan=False)
longitudes = st.floats(-180, 180, allow_nan=False)
# Points on the edges of the cells the lookups are remembered for
cell_edges = st.integers(-360, 360).map(lambda n: n * zones.CELL_SIZE)


class TestZonesAt:
    @pytest.mark.parametrize(
        ("point", "expected"), PLACES.values(), ids=PLACES.keys()
    )
    def test_places(self, point, expected):
        assert zones.zones_at(*point) == expected

    def test_decimals(self):
        assert zones.zones_at(Decimal("51.5074"), Decimal("-0.1278")) == (
            14,
            27,
        )

    @pytest.mark.parametrize("latitude", [90, 89, -89, -90])
    def test_poles_arent_in_a_zone(self, latitude):
        assert zones.zones_at(latitude, 0) == (None, None)

    def test_antimeridian_is_the_same_either_way(self):
        for latitude in range(-80, 81, 5):
            assert zones.zones_at(latitude, 180) == zones.zones_at(
                latitude, -180
            )

    def test_border_isnt_guessed(self):
        assert zones.cq_zones().candidates(*BORDER) == {14, 40}
        assert zones.zones_at(*BORDER).cq is None

    def test_dxcc_settles_a_border(self):
        faroes = DxccEntity(code=222, name="FAROE IS.", cq_zones=[14])
        iceland = DxccEntity(code=242, name="ICELAND", cq_zones=[40])
        assert zones.zones_at(*BORDER, dxcc=faroes).cq == 14
        assert zones.zones_at(*BORDER, dxcc=iceland).cq == 40

    def test_dxcc_doesnt_override_the_map(self):
        elsewhere = DxccEntity(code=1, name="CANADA", cq_zones=[1])
        assert zones.zones_at(51.5074, -0.1278, dxcc=elsewhere).cq == 14

    @pytest.mark.parametrize(
        ("latitude", "longitude"), [(91, 0), (-91, 0), (0, 181), (0, -181)]
    )
    def test_out_of_range(self, latitude, longitude):
        with pytest.raises(ValueError):
            zones.zones_at(latitude, longitude)

    def test_every_zone_is_somewhere(self):
        assert {p.zone for p in zones.cq_zones().polygons} == set(range(1, 41))
        assert {p.zone for p in zones.itu_zones().polygons} == set(range(1, 91))


class TestRememberedCells:
    """Remembering a cell's zones must give the same answer as looking"""

    @given(latitudes, longitudes)
    def test_anywhere(self, latitude, longitude):
        for zone_map in (zones.cq_zones(), zones.itu_zones()):
            assert zone_map.candidates(latitude, longitude) == zone_map._search(
                latitude, longitude
            )

    @given(cell_edges.filter(lambda x: -90 <= x <= 90), cell_edges)
    def test_cell_edges(self, latitude, longitude):
        for zone_map in (zones.cq_zones(), zones.itu_zones()):
            assert zone_map.candidates(latitude, longitude) == zone_map._search(
                latitude, longitude
            )

    def test_places_in_one_cell(self):
        cq = zones.cq_zones()
        first = cq.candidates(51.51, -0.12)
        assert cq.candidates(51.52, -0.13) is first


class TestZonesForGrid:
    @pytest.mark.parametrize(
        ("grid", "expected"),
        [
            ("IO91wm", (14, 27)),
            ("io91WM", (14, 27)),
            ("FN54vh", (5, 8)),
            ("FN54vh12", (5, 8)),
            # Squares all in one zone
            ("IO91", (14, 27)),
            ("RR73", (19, 75)),
            # Squares and fields across a border
            ("JN30", (None, None)),
            ("JN46", (None, 28)),
            ("IO", (14, None)),
            ("JO", (None, None)),
        ],
    )
    def test_grids(self, grid, expected):
        assert zones.zones_for_grid(grid) == expected

    def test_subsquare_on_a_border_is_its_middle(self):
        middle = zones.zones_for_grid("JN46mm")
        assert middle == (14, 28)

    def test_dxcc_settles_a_square_across_a_border(self):
        italy = DxccEntity(code=248, name="ITALY", cq_zones=[15, 33])
        switzerland = DxccEntity(code=287, name="SWITZERLAND", cq_zones=[14])
        assert zones.zones_for_grid("JN46", dxcc=italy).cq == 15
        assert zones.zones_for_grid("JN46", dxcc=switzerland).cq == 14

    @pytest.mark.parametrize("grid", ["", "ZZ00", "IO9", "nonsense"])
    def test_not_a_grid(self, grid):
        with pytest.raises(ValueError):
            zones.zones_for_grid(grid)


@pytest.mark.django_db
class TestLocationZones:
    def make(self, user, **kwargs):
        return Location.objects.create(owner=user, name="Home", **kwargs)

    def test_from_coordinates(self, user):
        location = self.make(
            user, latitude=Decimal("51.5074"), longitude=Decimal("-0.1278")
        )
        assert (location.cq_zone, location.itu_zone) == (14, 27)

    def test_from_grid(self, user):
        location = self.make(user, gridsquare="FN54vh")
        assert (location.cq_zone, location.itu_zone) == (5, 8)

    def test_coordinates_win_over_grid(self, user):
        location = self.make(
            user,
            latitude=Decimal("51.5074"),
            longitude=Decimal("-0.1278"),
            gridsquare="FN54vh",
        )
        assert (location.cq_zone, location.itu_zone) == (14, 27)

    def test_given_zones_are_kept(self, user):
        location = self.make(user, gridsquare="IO91wm", cq_zone=40)
        assert (location.cq_zone, location.itu_zone) == (40, 27)

    def test_from_dxcc_all_in_one_zone(self, user, england):
        location = self.make(user, dxcc=england)
        assert (location.cq_zone, location.itu_zone) == (14, 27)

    def test_not_from_dxcc_in_many_zones(self, user, usa):
        location = self.make(user, dxcc=usa)
        assert (location.cq_zone, location.itu_zone) == (None, None)

    def test_dxcc_when_the_map_cant_say(self, user, england):
        location = self.make(user, gridsquare="IO", dxcc=england)
        assert (location.cq_zone, location.itu_zone) == (14, 27)

    def test_nowhere(self, user):
        location = self.make(user)
        assert (location.cq_zone, location.itu_zone) == (None, None)

    def test_moving_moves_the_zones(self, user):
        location = self.make(user, gridsquare="IO91wm")
        location.set_coordinates(44.31, -68.2034)
        location.save()
        location.refresh_from_db()
        assert (location.cq_zone, location.itu_zone) == (5, 8)

    def test_moving_to_the_pole(self, user):
        location = self.make(user, gridsquare="IO91wm")
        location.set_coordinates(90, 0)
        location.save()
        assert (location.cq_zone, location.itu_zone) == (None, None)

    def test_bad_coordinates_are_still_reported(self, user):
        with pytest.raises(Exception, match="latitude"):
            self.make(user, latitude=Decimal("95"), longitude=Decimal("0"))
