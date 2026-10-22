"""Public locations, owned by the system, and private ones owned by users"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from apps.callsigns.models import Callsign, CallsignAllocation
from apps.locations import importers
from apps.locations.models import DxccEntity, Location, Reference
from apps.logbook.models import QSO, Logbook, LogSession
from django.core.exceptions import ValidationError
from django.utils import timezone

pytestmark = pytest.mark.django_db

SINCE = datetime(2020, 1, 1, tzinfo=UTC)


def allocate(user, call, is_primary=False, valid_from=SINCE, valid_until=None):
    callsign, _ = Callsign.objects.get_or_create(call=call)
    return CallsignAllocation.objects.create(
        callsign=callsign,
        user=user,
        is_primary=is_primary,
        valid_from=valid_from,
        valid_until=valid_until,
    )


@pytest.fixture
def public(park):
    importers.sync_public_locations("pota")
    return park.public_location


class TestPrivateNames:
    """A private name is left alone unless it is shared or clashes with a
    public location, then the owner's callsign is added"""

    def test_plain_name(self, user):
        allocate(user, "M0WME")
        location = Location.objects.create(owner=user, name="Home")
        assert location.full_name == "Home"
        assert location.qualifier == ""
        assert str(location) == "Home"

    def test_same_name_for_different_users(self, user, other_user):
        mine = Location.objects.create(owner=user, name="Home")
        theirs = Location.objects.create(owner=other_user, name="Home")
        assert mine.full_name == theirs.full_name == "Home"

    def test_names_unique_per_owner(self, user):
        Location.objects.create(owner=user, name="Home")
        with pytest.raises(ValidationError, match="already have a location"):
            Location.objects.create(owner=user, name="Home")

    def test_spaces_round_the_name(self, user):
        location = Location.objects.create(owner=user, name="  Home ")
        assert location.full_name == "Home"


class TestQualifier:
    def test_primary_callsign(self, user):
        allocate(user, "2E0AAA")
        allocate(user, "M0WME", is_primary=True)
        location = Location.objects.create(
            owner=user, name="Home", is_shared=True
        )
        assert location.full_name == "Home (M0WME)"
        assert location.qualifier == "M0WME"

    def test_any_held_callsign_without_a_primary(self, user):
        allocate(user, "M0WME")
        location = Location.objects.create(
            owner=user, name="Home", is_shared=True
        )
        assert location.full_name == "Home (M0WME)"

    def test_callsigns_no_longer_held_dont_count(self, user):
        allocate(user, "M0OLD", valid_until=SINCE + timedelta(days=1))
        location = Location.objects.create(
            owner=user, name="Home", is_shared=True
        )
        assert location.full_name == "Home (m0wme)"

    def test_kept_when_the_callsign_changes(self, user):
        allocation = allocate(user, "M0WME")
        location = Location.objects.create(
            owner=user, name="Home", is_shared=True
        )
        allocation.end(SINCE + timedelta(days=1))
        allocate(user, "G4NEW")
        location.name = "Shack"
        location.save()
        assert location.full_name == "Shack (M0WME)"

    def test_sharing_and_unsharing(self, user):
        allocate(user, "M0WME")
        location = Location.objects.create(owner=user, name="Home")
        location.is_shared = True
        location.save()
        assert location.full_name == "Home (M0WME)"
        location.is_shared = False
        location.save()
        assert location.full_name == "Home"


class TestSharedNames:
    def test_shared_names_are_unique(self, user, other_user):
        allocation = allocate(user, "M0WME")
        Location.objects.create(owner=user, name="Home", is_shared=True)
        # M0WME passes to someone else, who shares a location called Home
        now = timezone.now()
        allocation.end(now)
        allocate(other_user, "M0WME", valid_from=now)
        with pytest.raises(ValidationError, match="already a location"):
            Location.objects.create(
                owner=other_user, name="Home", is_shared=True
            )
        # Kept to themselves it's fine
        Location.objects.create(owner=other_user, name="Home")

    def test_shared_cant_take_a_public_name(self, user):
        Location.objects.create(owner=None, name="Home (m0wme)")
        with pytest.raises(ValidationError, match="already a location"):
            Location.objects.create(owner=user, name="Home", is_shared=True)

    def test_owner_cant_have_the_same_shown_name_twice(self, user):
        allocate(user, "M0WME")
        Location.objects.create(owner=user, name="Home", is_shared=True)
        with pytest.raises(ValidationError, match="already have a location"):
            Location.objects.create(owner=user, name="Home (M0WME)")

    def test_public_locations_arent_shared(self, db):
        with pytest.raises(ValidationError):
            Location.objects.create(owner=None, name="X", is_shared=True)


class TestClashesWithPublicNames:
    def test_name_of_a_public_location_is_qualified(self, user, public):
        allocate(user, "M0WME")
        location = Location.objects.create(owner=user, name=public.full_name)
        assert location.full_name == (
            "GB-0001 Lake District National Park (M0WME)"
        )

    def test_ignores_case(self, user, public):
        location = Location.objects.create(
            owner=user, name=public.full_name.upper()
        )
        assert location.qualifier == "m0wme"

    def test_new_public_location_renames_clashing_ones(
        self, user, other_user, park
    ):
        allocate(user, "M0WME")
        clash = Location.objects.create(
            owner=user, name="GB-0001 Lake District National Park"
        )
        other = Location.objects.create(owner=other_user, name="Elsewhere")
        assert clash.full_name == "GB-0001 Lake District National Park"

        importers.sync_public_locations("pota")
        renamed = Location.qualify_clashing_names()

        assert renamed == 1
        clash.refresh_from_db()
        other.refresh_from_db()
        assert clash.full_name == "GB-0001 Lake District National Park (M0WME)"
        assert other.full_name == "Elsewhere"
        assert park.public_location.full_name == (
            "GB-0001 Lake District National Park"
        )

    def test_import_renames(self, user, pota_csv):
        clash = Location.objects.create(
            owner=user, name="US-0004 Arches National Park"
        )
        result = importers.import_data("pota", pota_csv)
        assert result.renamed_locations == 1
        clash.refresh_from_db()
        assert clash.full_name == "US-0004 Arches National Park (m0wme)"

    def test_public_location_saved_by_hand_renames(self, user):
        clash = Location.objects.create(owner=user, name="The Beach")
        Location.objects.create(owner=None, name="The Beach")
        clash.refresh_from_db()
        assert clash.full_name == "The Beach (m0wme)"

    def test_renamed_public_location_frees_the_name(self, user):
        public = Location.objects.create(owner=None, name="The Beach")
        mine = Location.objects.create(owner=user, name="The Beach")
        assert mine.full_name == "The Beach (m0wme)"
        public.name = "The Dunes"
        public.save()
        mine.save()
        assert mine.full_name == "The Beach"

    def test_rename_that_clashes_is_skipped(self, user):
        allocate(user, "M0WME")
        Location.objects.create(owner=user, name="The Beach (M0WME)")
        clash = Location.objects.create(owner=user, name="The Beach")
        Location.objects.create(owner=None, name="The Beach")
        clash.refresh_from_db()
        # Only the owner can see it, so it is left as it was
        assert clash.full_name == "The Beach"


class TestPublicLocations:
    def test_made_from_the_reference(self, public, park):
        assert public.is_public
        assert public.owner is None
        assert public.full_name == "GB-0001 Lake District National Park"
        assert public.name == "Lake District National Park"
        assert public.latitude == park.latitude
        assert public.gridsquare == "IO84lm"
        assert list(public.references.all()) == [park]

    def test_adif_fields(self, public):
        assert public.adif_fields()["my_pota_ref"] == "GB-0001"

    def test_only_public_locations_have_a_source(self, user, park):
        location = Location(owner=user, name="Mine", source_reference=park)
        with pytest.raises(ValidationError):
            location.save()

    def test_reference_with_a_public_location_cant_go(self, public, park):
        from django.db.models import ProtectedError

        with pytest.raises(ProtectedError):
            park.delete()

    def test_querysets(self, public, user, other_user, make_user):
        mine = Location.objects.create(owner=user, name="Home")
        theirs = Location.objects.create(owner=other_user, name="Home")
        shared = Location.objects.create(
            owner=other_user, name="Club", is_shared=True
        )
        assert list(Location.objects.public()) == [public]
        assert set(Location.objects.private()) == {mine, theirs, shared}
        assert list(Location.objects.shared()) == [shared]
        assert set(Location.objects.visible_to(user)) == {
            public,
            mine,
            shared,
        }
        assert set(Location.objects.visible_to(make_user())) == {
            public,
            shared,
        }

    def test_usable_and_editable(self, public, user, other_user, make_user):
        mine = Location.objects.create(owner=user, name="Home")
        staff = make_user(is_staff=True)
        assert public.usable_by(user)
        assert public.usable_by(other_user)
        assert not public.editable_by(user)
        assert public.editable_by(staff)
        assert mine.usable_by(user)
        assert mine.editable_by(user)
        assert not mine.usable_by(other_user)
        assert not mine.editable_by(other_user)
        assert not mine.editable_by(staff)

    def test_shared_is_usable_by_all_but_editable_by_the_owner(
        self, user, other_user
    ):
        shared = Location.objects.create(
            owner=user, name="Club", is_shared=True
        )
        assert shared.usable_by(other_user)
        assert not shared.editable_by(other_user)
        assert shared.editable_by(user)


class TestCloning:
    def test_clone(self, public, park, user):
        allocate(user, "M0WME")
        clone = public.clone_for(user)
        assert not clone.is_public
        assert clone.owner == user
        assert clone.cloned_from == public
        assert clone.source_reference is None
        assert clone.name == "Lake District National Park"
        assert clone.full_name == "Lake District National Park"
        assert clone.latitude == public.latitude
        assert clone.gridsquare == public.gridsquare
        assert list(clone.references.all()) == [park]
        assert list(public.clones.all()) == [clone]

    def test_changes_dont_touch_the_original(self, public, user, summit):
        clone = public.clone_for(user, name="My spot")
        clone.set_coordinates(54.6, -3.0)
        clone.references.add(summit)
        clone.save()
        public.refresh_from_db()
        assert public.latitude == Decimal("54.5")
        assert public.references.count() == 1

    def test_cloning_twice_needs_another_name(self, public, user):
        public.clone_for(user)
        with pytest.raises(ValidationError):
            public.clone_for(user)
        assert public.clone_for(user, name="Second visit").name == (
            "Second visit"
        )

    def test_other_users_can_clone_too(self, public, user, other_user):
        public.clone_for(user)
        public.clone_for(other_user)
        assert public.clones.count() == 2

    def test_private_locations_can_be_cloned(self, user, other_user):
        home = Location.objects.create(owner=user, name="Home")
        assert home.clone_for(other_user).cloned_from == home

    def test_clone_survives_the_original_going(self, user):
        home = Location.objects.create(owner=user, name="Home")
        copy = home.clone_for(user, name="Copy")
        home.delete()
        copy.refresh_from_db()
        assert copy.cloned_from is None


class TestImporting:
    def test_every_park_gets_one(self, pota_csv):
        result = importers.import_data("pota", pota_csv)
        assert result.public_locations == 5
        assert Location.objects.public().count() == 5
        acadia = Location.objects.get(full_name="US-0001 Acadia National Park")
        assert acadia.gridsquare == "FN54vh"
        assert acadia.references.get().code == "US-0001"

    def test_every_summit_gets_one(self, sota_csv):
        importers.import_data("sota", sota_csv)
        summit = Reference.objects.lookup("sota", "3B8/MU-002")
        location = summit.public_location
        assert location.full_name == "3B8/MU-002 Pieter Both"
        assert location.altitude_m == 821

    def test_not_for_iota(self, iota_json):
        result = importers.import_data("iota", iota_json)
        assert result.public_locations == 0
        assert not Location.objects.exists()

    def test_reimport_updates_in_place(self, pota_csv):
        importers.import_data("pota", pota_csv)
        ids = set(Location.objects.values_list("pk", flat=True))
        renamed = pota_csv.replace(b"Acadia National Park", b"Acadia NP")
        importers.import_data("pota", renamed)
        assert set(Location.objects.values_list("pk", flat=True)) == ids
        assert Location.objects.filter(full_name="US-0001 Acadia NP").exists()
        assert Location.references.through.objects.count() == 5

    def test_dxcc_and_zones(self, pota_csv, usa):
        DxccEntity.objects.create(
            code=6, name="ALASKA", cq_zones=[1], itu_zones=[1, 2]
        )
        importers.import_data("pota", pota_csv)
        acadia = Reference.objects.lookup("pota", "US-0001").public_location
        alagnak = Reference.objects.lookup("pota", "US-0002").public_location
        assert acadia.dxcc == usa
        assert alagnak.dxcc_id == 6
        # From where the parks are, as the entities are in many zones
        assert (acadia.cq_zone, acadia.itu_zone) == (5, 8)
        assert (alagnak.cq_zone, alagnak.itu_zone) == (1, 1)

    def test_zones_from_dxcc_without_a_position(self, england):
        reference = Reference.objects.create(
            program="pota", code="GB-9999", name="Nowhere", dxcc_code=223
        )
        importers.sync_public_locations("pota")
        location = reference.public_location
        assert (location.cq_zone, location.itu_zone) == (14, 27)

    def test_zones_follow_a_moved_reference(self, pota_csv):
        importers.import_data("pota", pota_csv)
        Reference.objects.filter(code="US-0001").update(
            latitude=Decimal("51.5074"), longitude=Decimal("-0.1278")
        )
        importers.sync_public_locations("pota")
        acadia = Reference.objects.lookup("pota", "US-0001").public_location
        assert (acadia.cq_zone, acadia.itu_zone) == (14, 27)

    def test_dxcc_not_loaded_yet(self, pota_csv):
        importers.import_data("pota", pota_csv)
        assert not Location.objects.filter(dxcc__isnull=False).exists()

    def test_dropped_reference_keeps_its_location(self, pota_csv):
        importers.import_data("pota", pota_csv)
        shorter = b"\n".join(pota_csv.splitlines()[:-1])
        importers.import_data("pota", shorter)
        assert Location.objects.public().count() == 5

    def test_clones_are_left_alone(self, pota_csv, user):
        importers.import_data("pota", pota_csv)
        public = Location.objects.get(full_name="US-0001 Acadia National Park")
        clone = public.clone_for(user)
        clone.set_coordinates(44.0, -68.0)
        clone.save()
        importers.import_data("pota", pota_csv)
        clone.refresh_from_db()
        assert clone.latitude == Decimal("44")


class TestLoggingFromPublicLocations:
    def test_any_user_can_log_from_one(self, public, user, other_user):
        for owner in (user, other_user):
            qso = QSO(
                owner=owner,
                call="K1ABC",
                time_on=SINCE,
                my_location=public,
            )
            qso.save()
            assert qso.my_location == public

    def test_logbook_default(self, public, user):
        logbook = Logbook.objects.create(
            owner=user, name="Lakes", default_location=public
        )
        assert logbook.default_location == public

    def test_session(self, public, user):
        session = LogSession.objects.create(
            owner=user, name="Activation", my_location=public
        )
        assert session.my_location == public

    def test_someone_elses_private_location_still_refused(
        self, user, other_user
    ):
        theirs = Location.objects.create(owner=other_user, name="Theirs")
        with pytest.raises(ValidationError, match="someone else"):
            QSO(
                owner=user, call="K1ABC", time_on=SINCE, my_location=theirs
            ).save()
        with pytest.raises(ValidationError, match="someone else"):
            Logbook.objects.create(
                owner=user, name="X", default_location=theirs
            )
        with pytest.raises(ValidationError, match="someone else"):
            LogSession.objects.create(owner=user, name="X", my_location=theirs)

    def test_shared_location(self, user, other_user):
        shared = Location.objects.create(
            owner=other_user, name="Club", is_shared=True
        )
        qso = QSO(owner=user, call="K1ABC", time_on=SINCE, my_location=shared)
        qso.save()
        assert qso.my_location == shared
