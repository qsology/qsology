import contextlib
import logging
from decimal import Decimal

from apps.locations import maidenhead, zones
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import Case, Q, When
from django.db.models.functions import Upper

logger = logging.getLogger(__name__)


def validate_gridsquare(value: str) -> None:
    if value and not maidenhead.is_valid_grid(value):
        raise ValidationError(f"{value!r} is not a valid Maidenhead locator")


latitude_validators = [MinValueValidator(-90), MaxValueValidator(90)]
longitude_validators = [MinValueValidator(-180), MaxValueValidator(180)]


def coordinate_field(validators, **kwargs):
    return models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        validators=validators,
        **kwargs,
    )


SEARCH_LIMIT = 20


class DxccEntityQuerySet(models.QuerySet):
    def current(self):
        """Entities that still count for DXCC"""
        return self.filter(is_deleted=False)

    def search(self, text: str, limit: int = SEARCH_LIMIT):
        """Entities to pick from, by number or name, e.g. 223 or england

        An exact number comes first, then current entities before deleted
        ones"""
        text = text.strip()
        if not text:
            return self.none()
        matches = Q(name__icontains=text)
        if text.isdigit():
            matches |= Q(code=int(text))
        return (
            self.filter(matches)
            .annotate(
                rank=Case(
                    When(code=int(text) if text.isdigit() else -1, then=0),
                    When(name__istartswith=text, then=1),
                    default=2,
                )
            )
            .order_by("rank", "is_deleted", "name")[:limit]
        )


class DxccEntity(models.Model):
    """A DXCC entity, as used by LoTW and ADIF, e.g. 223 ENGLAND

    Loaded from the TQSL configuration published by the ARRL. Entities are
    never removed, deleted entities stay so old contacts keep their entity"""

    code = models.PositiveIntegerField(
        unique=True, help_text="ARRL entity number, the ADIF DXCC value"
    )
    name = models.CharField(max_length=100)
    is_deleted = models.BooleanField(default=False)
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    cq_zones = models.JSONField(default=list, blank=True)
    itu_zones = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = DxccEntityQuerySet.as_manager()

    class Meta:
        ordering = ("code",)
        verbose_name = "DXCC entity"
        verbose_name_plural = "DXCC entities"

    def __str__(self) -> str:
        return f"{self.code} {self.name}"


class ReferenceProgram(models.TextChoices):
    """Award programmes with references to operate from"""

    POTA = "pota", "Parks on the Air"
    SOTA = "sota", "Summits on the Air"
    IOTA = "iota", "Islands on the Air"


# The ADIF fields each programme is recorded in, for both ends of a contact
PROGRAM_ADIF_FIELDS = {
    ReferenceProgram.POTA: ("pota_ref", "my_pota_ref"),
    ReferenceProgram.SOTA: ("sota_ref", "my_sota_ref"),
    ReferenceProgram.IOTA: ("iota", "my_iota"),
}


class ReferenceQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def program(self, program: str):
        return self.filter(program=program)

    def lookup(self, program: str, code: str):
        return self.filter(program=program, code__iexact=code.strip()).first()

    def search(
        self,
        text: str,
        program: str | None = None,
        limit: int = SEARCH_LIMIT,
    ):
        """References to pick from when making a location, by code or name,
        e.g. US-0001, G/LD, EU-005 or acadia

        An exact code comes first, then codes starting with the text, then
        names containing it, with active references before inactive ones"""
        text = text.strip()
        if not text:
            return self.none()
        references = self if program is None else self.filter(program=program)
        return (
            references.filter(
                Q(code__istartswith=text) | Q(name__icontains=text)
            )
            .annotate(
                rank=Case(
                    When(code__iexact=text, then=0),
                    When(code__istartswith=text, then=1),
                    default=2,
                )
            )
            .order_by("rank", "-is_active", "program", "code")[:limit]
        )


class Reference(models.Model):
    """A park, summit or island from one of the award programmes

    Loaded from the programme's published list. References that drop off the
    list are made inactive rather than deleted, as locations and contacts can
    still point at them"""

    program = models.CharField(max_length=8, choices=ReferenceProgram.choices)
    code = models.CharField(
        max_length=32, help_text="e.g. US-0001, W7W/KG-001, EU-005"
    )
    name = models.CharField(max_length=255)
    # A plain code rather than a link so references can load before entities
    dxcc_code = models.PositiveIntegerField(
        null=True, blank=True, help_text="ADIF DXCC entity number"
    )
    region = models.CharField(
        max_length=100,
        blank=True,
        help_text="e.g. US-ME for POTA, the association for SOTA",
    )
    latitude = coordinate_field(latitude_validators)
    longitude = coordinate_field(longitude_validators)
    gridsquare = models.CharField(
        max_length=10, blank=True, validators=[validate_gridsquare]
    )
    altitude_m = models.IntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    # Anything else the programme publishes, e.g. SOTA points or IOTA islands
    data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ReferenceQuerySet.as_manager()

    class Meta:
        ordering = ("program", "code")
        constraints = (
            models.UniqueConstraint(
                fields=["program", "code"], name="reference_unique_code"
            ),
        )

    def __str__(self) -> str:
        return f"{self.code} {self.name}"

    @property
    def dxcc(self) -> DxccEntity | None:
        if self.dxcc_code is None:
            return None
        return DxccEntity.objects.filter(code=self.dxcc_code).first()


def public_name(reference: Reference) -> str:
    """The name of the public location for a reference, e.g.
    US-0001 Acadia National Park"""
    return f"{reference.code} {reference.name}"[:FULL_NAME_LENGTH]


def _only_zone(entity, attribute: str) -> int | None:
    """The entity's zone, when it's all in one"""
    found = getattr(entity, attribute, None) or []
    return found[0] if len(found) == 1 else None


def owner_qualifier(owner) -> str:
    """What a private location's name is qualified with, the owner's
    primary callsign, any callsign they hold, or failing that their
    username"""
    from apps.callsigns.models import CallsignAllocation

    held = (
        CallsignAllocation.objects.active()
        .filter(user=owner)
        .select_related("callsign")
        .order_by("-is_primary", "valid_from")
        .first()
    )
    return held.callsign.call if held else owner.get_username()


FULL_NAME_LENGTH = 160


class LocationQuerySet(models.QuerySet):
    def public(self):
        return self.filter(owner__isnull=True)

    def private(self):
        return self.filter(owner__isnull=False)

    def shared(self):
        """Private locations their owners have shared with everyone"""
        return self.filter(owner__isnull=False, is_shared=True)

    def visible_to(self, user):
        """Public and shared locations, and the user's own"""
        return self.filter(
            Q(owner__isnull=True) | Q(is_shared=True) | Q(owner=user)
        )


class Location(models.Model):
    """Somewhere a user operates from, e.g. home, a park or a summit

    Public locations have no owner, they belong to the system and are made
    from every POTA park and SOTA summit, kept up to date as the lists are
    reloaded. Anyone can log from one, and anyone can clone one to make a
    private copy of their own to change. Private locations belong to a user,
    who can share one with everyone else.

    full_name is what a location is shown as. Public locations keep their
    own name, e.g. "US-0001 Acadia National Park". A private location is
    shown by its name, e.g. "Home", unless it is shared or its name is the
    same as a public location's, when its owner's callsign is added, e.g.
    "Home (M0WME)". A private location is renamed like this when a public
    location with the same name appears, the public one keeps its name.

    The location can be at any number of references at once, e.g. a park on
    an island, and any POTA, SOTA or IOTA reference can be added. Contacts
    copy the location's details when logged, so changing or deleting a
    location never changes what was logged"""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="locations",
        help_text="Empty for public locations, which the system owns",
    )
    name = models.CharField(max_length=100)
    full_name = models.CharField(max_length=FULL_NAME_LENGTH, editable=False)
    is_shared = models.BooleanField(
        default=False,
        help_text="Let everyone see and log from this private location",
    )
    # The callsign added to the name, kept once given so the name doesn't
    # change when the owner's callsign does
    qualifier = models.CharField(max_length=40, blank=True, editable=False)
    source_reference = models.OneToOneField(
        Reference,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="public_location",
        editable=False,
        help_text="The reference a public location is made from",
    )
    cloned_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="clones",
        editable=False,
    )
    address = models.TextField(blank=True)
    latitude = coordinate_field(latitude_validators)
    longitude = coordinate_field(longitude_validators)
    gridsquare = models.CharField(
        max_length=10, blank=True, validators=[validate_gridsquare]
    )
    altitude_m = models.IntegerField(null=True, blank=True)
    dxcc = models.ForeignKey(
        DxccEntity,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        to_field="code",
    )
    cq_zone = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1)]
    )
    itu_zone = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1)]
    )
    country = models.CharField(max_length=100, blank=True)
    state = models.CharField(
        max_length=50, blank=True, help_text="State, province or similar"
    )
    county = models.CharField(max_length=100, blank=True)
    references = models.ManyToManyField(
        Reference, blank=True, related_name="locations"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = LocationQuerySet.as_manager()

    class Meta:
        ordering = ("full_name",)
        constraints = (
            models.UniqueConstraint(
                fields=["owner", "name"],
                name="location_unique_name",
                violation_error_message=(
                    "You already have a location with this name"
                ),
            ),
            models.UniqueConstraint(
                fields=["owner", "full_name"],
                name="location_unique_full_name_per_owner",
                violation_error_message=(
                    "You already have a location with this name"
                ),
            ),
            # Everyone sees public and shared locations, so they can't share
            # a name
            models.UniqueConstraint(
                fields=["full_name"],
                condition=Q(owner__isnull=True) | Q(is_shared=True),
                name="location_unique_shared_name",
                violation_error_message=(
                    "There is already a location with this name, "
                    "please choose another"
                ),
            ),
            models.CheckConstraint(
                condition=Q(owner__isnull=False) | Q(is_shared=False),
                name="location_public_isnt_shared",
            ),
            models.CheckConstraint(
                condition=Q(source_reference__isnull=True)
                | Q(owner__isnull=True),
                name="location_reference_only_when_public",
            ),
            models.CheckConstraint(
                condition=(
                    Q(latitude__isnull=True, longitude__isnull=True)
                    | Q(latitude__isnull=False, longitude__isnull=False)
                ),
                name="location_both_coordinates",
            ),
        )

    def __str__(self) -> str:
        return self.full_name or self.name

    def save(self, *args, **kwargs):
        self.name = self.name.strip()
        self.full_name = self.make_full_name()
        if self.gridsquare:
            self.gridsquare = self.gridsquare.strip()
        if self.gridsquare and maidenhead.is_valid_grid(self.gridsquare):
            self.gridsquare = maidenhead.normalize_grid(self.gridsquare)
        if not self.gridsquare and self.has_coordinates:
            # Out of range coordinates are left for full_clean to report
            with contextlib.suppress(ValueError):
                self.gridsquare = maidenhead.latlon_to_grid(
                    self.latitude, self.longitude
                )
        self.fill_zones()
        self.full_clean()
        with transaction.atomic():
            super().save(*args, **kwargs)
            if self.is_public:
                Location.qualify_clashing_names()

    def clean(self):
        super().clean()
        if (self.latitude is None) != (self.longitude is None):
            raise ValidationError(
                "Latitude and longitude must be given together"
            )

    def make_full_name(self) -> str:
        if self.is_public:
            if self.source_reference is not None:
                return public_name(self.source_reference)
            return self.name[:FULL_NAME_LENGTH]
        if not self.needs_qualifier():
            return self.name
        if not self.qualifier:
            self.qualifier = owner_qualifier(self.owner)
        return f"{self.name} ({self.qualifier})"

    def needs_qualifier(self) -> bool:
        """Does the owner's callsign need adding to the name?"""
        if self.is_shared:
            return True
        return (
            Location.objects.public()
            .filter(full_name__iexact=self.name)
            .exists()
        )

    @classmethod
    def qualify_clashing_names(cls) -> int:
        """Add the owner's callsign to private locations named the same as
        a public location, returning how many were renamed"""
        public_names = (
            cls.objects.public()
            .annotate(upper_name=Upper("full_name"))
            .values("upper_name")
        )
        clashing = (
            cls.objects.private()
            .annotate(upper_name=Upper("full_name"))
            .filter(upper_name__in=public_names)
        )
        renamed = 0
        for location in clashing.select_related("owner"):
            try:
                with transaction.atomic():
                    location.save()
            except ValidationError:
                # e.g. the owner already has a location with the new name,
                # it keeps its name, which only they can see
                logger.warning(
                    "Couldn't rename location %s clashing with a public one",
                    location.pk,
                )
                continue
            renamed += 1
        return renamed

    @property
    def is_public(self) -> bool:
        return self.owner_id is None

    def usable_by(self, user) -> bool:
        """Can the user log from here?"""
        return self.is_public or self.is_shared or self.owner_id == user.pk

    def editable_by(self, user) -> bool:
        """Only the owner changes a private location, public locations are
        kept up to date by the system and are left to staff"""
        if self.is_public:
            return user.is_staff
        return self.owner_id == user.pk

    def clone_for(self, user, name: str | None = None) -> Location:
        """A private copy of the location for the user to change

        Raises ValidationError if the user already has a location with the
        name, give another one"""
        clone = Location(
            owner=user,
            name=(name or self.name)[:100],
            cloned_from=self,
            **{
                field: getattr(self, field)
                for field in (
                    "address",
                    "latitude",
                    "longitude",
                    "gridsquare",
                    "altitude_m",
                    "dxcc_id",
                    "cq_zone",
                    "itu_zone",
                    "country",
                    "state",
                    "county",
                )
            },
        )
        with transaction.atomic():
            clone.save()
            clone.references.set(self.references.all())
        return clone

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    def set_coordinates(self, latitude, longitude) -> None:
        """Move the location, the grid square and zones follow"""
        self.latitude = Decimal(str(latitude)).quantize(Decimal("0.000001"))
        self.longitude = Decimal(str(longitude)).quantize(Decimal("0.000001"))
        self.gridsquare = maidenhead.latlon_to_grid(latitude, longitude)
        self.cq_zone = self.itu_zone = None
        self.fill_zones()

    def _entity(self) -> DxccEntity | None:
        """The DXCC entity, or None when there's none by that code, which is
        left for full_clean to report"""
        try:
            return self.dxcc
        except DxccEntity.DoesNotExist:
            return None

    def zones_here(self) -> zones.Zones:
        """The zones the location is in, from its coordinates or else its
        grid square"""
        entity = self._entity()
        try:
            if self.has_coordinates:
                return zones.zones_at(
                    self.latitude, self.longitude, dxcc=entity
                )
            if self.gridsquare:
                return zones.zones_for_grid(self.gridsquare, dxcc=entity)
        except ValueError:
            # Left for full_clean to report
            pass
        return zones.Zones(None, None)

    def fill_zones(self) -> None:
        """Fill in the zones that haven't been given, from where the
        location is, or else the DXCC entity when it's all in one zone"""
        if self.cq_zone is not None and self.itu_zone is not None:
            return
        here = self.zones_here()
        entity = self._entity()
        if self.cq_zone is None:
            self.cq_zone = here.cq or _only_zone(entity, "cq_zones")
        if self.itu_zone is None:
            self.itu_zone = here.itu or _only_zone(entity, "itu_zones")

    def geocode(self, address: str | None = None, **kwargs):
        """Look up the address and move the location to it

        Returns the geocoding result, or None if nothing was found, in which
        case the location is left as it was"""
        from apps.locations import geocode

        if address is not None:
            self.address = address
        result = geocode.geocode(self.address, **kwargs)
        if result is not None:
            self.set_coordinates(result.latitude, result.longitude)
            if result.country and not self.country:
                self.country = result.country
            if result.state and not self.state:
                self.state = result.state
            if result.county and not self.county:
                self.county = result.county
        return result

    def references_for(self, program: str) -> list[str]:
        return sorted(
            reference.code
            for reference in self.references.all()
            if reference.program == program
        )

    def adif_fields(self) -> dict:
        """The MY_ fields a contact made from here should be logged with

        Keys are QSO field names, empty values are left out"""
        fields = {
            "my_gridsquare": self.gridsquare,
            "my_lat": self.latitude,
            "my_lon": self.longitude,
            "my_altitude": self.altitude_m,
            "my_dxcc": self.dxcc_id,
            "my_cq_zone": self.cq_zone,
            "my_itu_zone": self.itu_zone,
            "my_country": self.country,
            "my_state": self.state,
            "my_cnty": self.county,
        }
        for program, (_, my_field) in PROGRAM_ADIF_FIELDS.items():
            # POTA allows several parks at once, e.g. a two-fer
            fields[my_field] = ",".join(self.references_for(program))
        return {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
