import json
from decimal import Decimal

import pytest
from apps.locations.models import (
    DxccEntity,
    Location,
    Reference,
    ReferenceProgram,
)
from django.core.exceptions import ValidationError
from django.db import IntegrityError

pytestmark = pytest.mark.django_db


@pytest.fixture
def make_location(user):
    def _make_location(**kwargs):
        kwargs.setdefault("owner", user)
        kwargs.setdefault("name", "Home")
        return Location.objects.create(**kwargs)

    return _make_location


class TestDxccEntity:
    def test_str(self, england):
        assert str(england) == "223 ENGLAND"

    def test_current_leaves_out_deleted(self, england):
        DxccEntity.objects.create(code=2, name="ABU AIL", is_deleted=True)
        assert list(DxccEntity.objects.current()) == [england]


class TestReference:
    def test_str(self, park):
        assert str(park) == "GB-0001 Lake District National Park"

    def test_dxcc(self, park, england):
        assert park.dxcc == england

    def test_dxcc_not_loaded_yet(self, park):
        assert park.dxcc_code == 223
        assert park.dxcc is None

    def test_no_dxcc(self, summit):
        assert summit.dxcc is None

    def test_code_unique_within_programme(self, park):
        with pytest.raises(IntegrityError):
            Reference.objects.create(
                program=ReferenceProgram.POTA, code="GB-0001", name="Again"
            )

    def test_same_code_in_another_programme(self, park):
        Reference.objects.create(
            program=ReferenceProgram.SOTA, code="GB-0001", name="Fine"
        )

    def test_lookup_ignores_case_and_space(self, park):
        assert Reference.objects.lookup("pota", " gb-0001 ") == park
        assert Reference.objects.lookup("sota", "GB-0001") is None

    def test_active_and_program(self, park, summit):
        summit.is_active = False
        summit.save()
        assert list(Reference.objects.active()) == [park]
        assert list(Reference.objects.program("sota")) == [summit]


class TestLocationSave:
    def test_grid_from_coordinates(self, make_location):
        location = make_location(
            latitude=Decimal("41.714775"), longitude=Decimal("-72.727260")
        )
        assert location.gridsquare == "FN31pr"

    def test_given_grid_is_kept_and_normalised(self, make_location):
        location = make_location(
            latitude=Decimal("41.714775"),
            longitude=Decimal("-72.727260"),
            gridsquare=" fn31PR21 ",
        )
        assert location.gridsquare == "FN31pr21"

    def test_grid_without_coordinates(self, make_location):
        assert make_location(gridsquare="io91wm").gridsquare == "IO91wm"

    def test_nothing_given(self, make_location):
        location = make_location()
        assert location.gridsquare == ""
        assert not location.has_coordinates

    def test_bad_grid(self, make_location):
        with pytest.raises(ValidationError, match="Maidenhead"):
            make_location(gridsquare="ZZ99")

    @pytest.mark.parametrize(
        ("latitude", "longitude"),
        [(Decimal(1), None), (None, Decimal(1))],
    )
    def test_coordinates_go_together(self, make_location, latitude, longitude):
        with pytest.raises(ValidationError, match="together"):
            make_location(latitude=latitude, longitude=longitude)

    @pytest.mark.parametrize(
        ("latitude", "longitude"),
        [
            (Decimal("90.1"), Decimal(0)),
            (Decimal("-90.1"), Decimal(0)),
            (Decimal(0), Decimal("180.1")),
            (Decimal(0), Decimal("-180.1")),
        ],
    )
    def test_coordinate_ranges(self, make_location, latitude, longitude):
        with pytest.raises(ValidationError):
            make_location(latitude=latitude, longitude=longitude)

    def test_name_unique_per_owner(self, make_location, other_user):
        make_location()
        make_location(owner=other_user)
        with pytest.raises(ValidationError):
            make_location()

    def test_zone_must_be_positive(self, make_location):
        with pytest.raises(ValidationError):
            make_location(cq_zone=0)

    def test_dxcc_must_exist(self, make_location):
        with pytest.raises(ValidationError):
            make_location(dxcc_id=999)

    def test_dxcc_by_code(self, make_location, england):
        assert make_location(dxcc=england).dxcc_id == 223


class TestSetCoordinates:
    def test_moves_grid_too(self, make_location):
        location = make_location(gridsquare="IO91wm")
        location.set_coordinates(41.714775, -72.727260)
        assert location.latitude == Decimal("41.714775")
        assert location.longitude == Decimal("-72.727260")
        assert location.gridsquare == "FN31pr"

    def test_rounds_to_six_places(self, make_location):
        location = make_location()
        location.set_coordinates(51.50740123456, -0.12780987654)
        location.save()
        assert location.latitude == Decimal("51.507401")
        assert location.longitude == Decimal("-0.127810")


class TestAdifFields:
    def test_everything(self, user, park, summit, england):
        location = Location.objects.create(
            owner=user,
            name="Lakes",
            latitude=park.latitude,
            longitude=park.longitude,
            gridsquare=park.gridsquare,
            dxcc=england,
            cq_zone=14,
            itu_zone=27,
            country="England",
            state="CMA",
            county="Cumbria",
        )
        location.references.add(park, summit)

        assert location.adif_fields() == {
            "my_gridsquare": "IO84lm",
            "my_lat": Decimal("54.5"),
            "my_lon": Decimal("-3.1"),
            "my_dxcc": 223,
            "my_cq_zone": 14,
            "my_itu_zone": 27,
            "my_country": "England",
            "my_state": "CMA",
            "my_cnty": "Cumbria",
            "my_pota_ref": "GB-0001",
            "my_sota_ref": "G/LD-001",
        }

    def test_pota_two_fer(self, make_location, park):
        location = make_location()
        second = Reference.objects.create(
            program="pota", code="GB-0002", name="Peak District"
        )
        location.references.add(second, park)
        assert location.adif_fields()["my_pota_ref"] == "GB-0001,GB-0002"

    def test_empty_values_left_out(self, make_location):
        assert make_location().adif_fields() == {}

    def test_iota(self, make_location):
        location = make_location()
        location.references.add(
            Reference.objects.create(
                program="iota", code="EU-005", name="Great Britain"
            )
        )
        assert location.adif_fields() == {"my_iota": "EU-005"}


class TestGeocode:
    def test_moves_the_location(self, make_location, fake_fetch):
        fetch = fake_fetch(
            json.dumps(
                [
                    {
                        "lat": "41.714775",
                        "lon": "-72.727260",
                        "display_name": "225 Main St, Newington",
                        "address": {
                            "country": "United States",
                            "country_code": "us",
                            "state": "Connecticut",
                            "county": "Hartford County",
                        },
                    }
                ]
            ).encode()
        )
        location = make_location()
        result = location.geocode("225 Main St, Newington, CT", fetch=fetch)

        assert result.display_name == "225 Main St, Newington"
        assert location.address == "225 Main St, Newington, CT"
        assert location.gridsquare == "FN31pr"
        assert location.country == "United States"
        assert location.state == "Connecticut"
        assert location.county == "Hartford County"
        assert fetch.calls[0]["params"]["q"] == "225 Main St, Newington, CT"

    def test_keeps_details_already_set(self, make_location, fake_fetch):
        fetch = fake_fetch(
            b'[{"lat": "1", "lon": "1", "address": {"country": "X"}}]'
        )
        location = make_location(address="Here", country="Mine")
        location.geocode(fetch=fetch)
        assert location.country == "Mine"
        assert fetch.calls[0]["params"]["q"] == "Here"

    def test_not_found_leaves_location(self, make_location, fake_fetch):
        location = make_location(gridsquare="IO91wm")
        assert location.geocode("Nowhere", fetch=fake_fetch(b"[]")) is None
        assert location.gridsquare == "IO91wm"
        assert not location.has_coordinates


class TestReferenceSearch:
    @pytest.fixture
    def references(self, park, summit):
        island = Reference.objects.create(
            program="iota", code="EU-005", name="Great Britain"
        )
        lakes = Reference.objects.create(
            program="pota", code="GB-0011", name="Lake Vyrnwy", is_active=False
        )
        lake_island = Reference.objects.create(
            program="iota", code="EU-120", name="Lake Islands"
        )
        return park, summit, island, lakes, lake_island

    def search(self, text, **kwargs):
        return [ref.code for ref in Reference.objects.search(text, **kwargs)]

    def test_by_code(self, references):
        assert self.search("eu-005") == ["EU-005"]
        assert self.search("G/LD") == ["G/LD-001"]

    def test_exact_code_first(self, references):
        Reference.objects.create(program="pota", code="GB-00011", name="X")
        assert self.search("GB-0001") == ["GB-0001", "GB-00011"]

    def test_by_name_active_first(self, references):
        assert self.search("lake") == ["EU-120", "GB-0001", "GB-0011"]

    def test_iota_summits_and_parks_together(self, references):
        assert self.search("Great Britain") == ["EU-005"]
        assert self.search("scafell") == ["G/LD-001"]

    def test_one_programme(self, references):
        assert self.search("lake", program="iota") == ["EU-120"]

    def test_nothing_for_blank(self, references):
        assert self.search("  ") == []

    def test_limit(self, references):
        assert len(self.search("lake", limit=2)) == 2

    def test_iota_can_be_added_to_a_location(self, references, user):
        island = Reference.objects.get(code="EU-005")
        location = Location.objects.create(owner=user, name="Home")
        location.references.add(island)
        assert location.adif_fields() == {"my_iota": "EU-005"}
        assert not Location.objects.public().exists()


class TestDxccSearch:
    @pytest.fixture
    def entities(self, england, usa):
        DxccEntity.objects.create(
            code=2, name="ABU AIL ISLANDS (DELETED)", is_deleted=True
        )
        DxccEntity.objects.create(code=22, name="PALAU")
        DxccEntity.objects.create(code=279, name="SCOTLAND")
        DxccEntity.objects.create(code=106, name="GUERNSEY")

    def search(self, text):
        return [e.code for e in DxccEntity.objects.search(text)]

    def test_by_name(self, entities):
        assert self.search("england") == [223]
        # Deleted entities come last
        assert self.search("land") == [223, 279, 2]

    def test_by_number(self, entities):
        assert self.search("223") == [223]
        assert self.search(" 2 ") == [2]

    def test_names_starting_with_first(self, entities):
        assert self.search("s") == [279, 106, 291, 2]
        assert self.search("u") == [291, 106, 22, 2]

    def test_nothing_for_blank(self, entities):
        assert self.search("") == []
