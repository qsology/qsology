import pytest
from apps.locations import maidenhead
from apps.locations.maidenhead import (
    grid_bounds,
    grid_to_latlon,
    is_valid_grid,
    latlon_to_grid,
    normalize_grid,
)
from hypothesis import given
from hypothesis import strategies as st

# Floating point slack when checking a point is inside a cell
EPSILON = 1e-9


class TestLatLonToGrid:
    @pytest.mark.parametrize(
        ("latitude", "longitude", "grid"),
        [
            (41.714775, -72.727260, "FN31pr"),  # W1AW, north west
            (51.5074, -0.1278, "IO91wm"),  # London, just west of Greenwich
            (-33.8688, 151.2093, "QF56od"),  # Sydney, south east
            (-22.9068, -43.1729, "GG87jc"),  # Rio de Janeiro, south west
            (0, 0, "JJ00aa"),
        ],
    )
    def test_known_locations(self, latitude, longitude, grid):
        assert latlon_to_grid(latitude, longitude) == grid

    @pytest.mark.parametrize(
        ("precision", "grid"),
        [
            (2, "FN"),
            (4, "FN31"),
            (6, "FN31pr"),
            (8, "FN31pr21"),
            (10, "FN31pr21rn"),
        ],
    )
    def test_precisions(self, precision, grid):
        assert latlon_to_grid(41.714775, -72.727260, precision) == grid

    def test_default_is_six_characters(self):
        assert maidenhead.DEFAULT_PRECISION == 6
        assert len(latlon_to_grid(10, 10)) == 6

    def test_accepts_decimals(self):
        from decimal import Decimal

        assert latlon_to_grid(Decimal("51.5074"), Decimal("-0.1278")) == (
            "IO91wm"
        )

    @pytest.mark.parametrize("precision", [0, 1, 3, 5, 12])
    def test_invalid_precision(self, precision):
        with pytest.raises(ValueError, match="Precision"):
            latlon_to_grid(0, 0, precision)

    @pytest.mark.parametrize(
        ("latitude", "longitude"),
        [(90.01, 0), (-90.01, 0), (0, 180.01), (0, -180.01)],
    )
    def test_out_of_range(self, latitude, longitude):
        with pytest.raises(ValueError, match="out of range"):
            latlon_to_grid(latitude, longitude)

    def test_cell_edges_are_exact(self):
        # -1.9 is on a cell edge, as a float it is a hair under, which once
        # put it in different cells at different precisions
        assert latlon_to_grid(-1.9, 0, 8) == "JI08ac04"
        assert latlon_to_grid(-1.9, 0, 10).startswith("JI08ac04")

    @pytest.mark.parametrize("value", [float("nan"), float("inf")])
    def test_not_a_number(self, value):
        with pytest.raises(ValueError, match="out of range"):
            latlon_to_grid(value, 0)
        with pytest.raises(ValueError, match="out of range"):
            latlon_to_grid(0, value)

    def test_south_pole_and_antimeridian_west(self):
        assert latlon_to_grid(-90, -180) == "AA00aa"

    def test_north_pole_and_antimeridian_east_stay_on_grid(self):
        # 90N and 180E are treated as the last cell before them
        assert latlon_to_grid(90, 180) == "RR99xx"
        assert latlon_to_grid(90, 180, 10) == "RR99xx99xx"


class TestValidation:
    @pytest.mark.parametrize(
        "value",
        ["FN", "fn31", "FN31pr", "fn31PR", "FN31pr21", "FN31pr21rn", "RR99xx"],
    )
    def test_valid(self, value):
        assert is_valid_grid(value)

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "F",
            "FN3",
            "SN31",  # Fields only go up to R
            "FN31py",  # Subsquares only go up to X
            "FN31pr2",
            "FN31pr21r",
            "FN31pr21rn0",
            "31FN",
            "FN 31",
        ],
    )
    def test_invalid(self, value):
        assert not is_valid_grid(value)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("io91wm", "IO91wm"),
            ("IO91WM", "IO91wm"),
            (" io91WM ", "IO91wm"),
            ("fn31pr21RN", "FN31pr21rn"),
            ("fn", "FN"),
        ],
    )
    def test_normalize(self, value, expected):
        assert normalize_grid(value) == expected

    def test_normalize_rejects_invalid(self):
        with pytest.raises(ValueError, match="not a valid"):
            normalize_grid("ZZ99")


class TestGridToLatLon:
    def test_field_bounds(self):
        south_west, north_east = grid_bounds("JJ")
        assert south_west == (0, 0)
        assert north_east == (10, 20)

    def test_square_bounds(self):
        south_west, north_east = grid_bounds("IO91")
        assert south_west == pytest.approx((51, -2))
        assert north_east == pytest.approx((52, 0))

    def test_centre(self):
        assert grid_to_latlon("IO91wm") == pytest.approx(
            (51.520833, -0.125), abs=1e-5
        )

    def test_case_doesnt_matter(self):
        assert grid_to_latlon("io91WM") == grid_to_latlon("IO91wm")

    def test_invalid(self):
        with pytest.raises(ValueError):
            grid_to_latlon("nope")


latitudes = st.floats(min_value=-90, max_value=90, allow_nan=False)
longitudes = st.floats(min_value=-180, max_value=180, allow_nan=False)
precisions = st.sampled_from(maidenhead.VALID_PRECISIONS)


class TestProperties:
    @given(latitudes, longitudes, precisions)
    def test_point_is_inside_its_grid(self, latitude, longitude, precision):
        grid = latlon_to_grid(latitude, longitude, precision)
        assert is_valid_grid(grid)
        assert len(grid) == precision
        south_west, north_east = grid_bounds(grid)
        assert south_west.latitude - EPSILON <= latitude
        assert latitude <= north_east.latitude + EPSILON
        assert south_west.longitude - EPSILON <= longitude
        assert longitude <= north_east.longitude + EPSILON

    @given(latitudes, longitudes, precisions)
    def test_centre_maps_back_to_the_same_grid(
        self, latitude, longitude, precision
    ):
        grid = latlon_to_grid(latitude, longitude, precision)
        assert latlon_to_grid(*grid_to_latlon(grid), precision) == grid

    @given(latitudes, longitudes)
    def test_longer_grids_extend_shorter_ones(self, latitude, longitude):
        full = latlon_to_grid(latitude, longitude, 10)
        for precision in (2, 4, 6, 8):
            assert (
                latlon_to_grid(latitude, longitude, precision)
                == (full[:precision])
            )

    @given(latitudes, longitudes, precisions)
    def test_normalize_is_idempotent(self, latitude, longitude, precision):
        grid = latlon_to_grid(latitude, longitude, precision)
        assert normalize_grid(grid) == grid
        assert normalize_grid(grid.swapcase()) == grid
