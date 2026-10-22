"""QSOs, the history of every change to them and the events they raise

A QSO is never removed by anything a user does. Deleting one marks it deleted
and it can be restored. Every save, including a delete, is checked against
the version the caller loaded so a stale copy can't overwrite a newer one,
and leaves a revision behind holding the whole QSO as it was saved, so any
earlier state can be brought back"""

import string
import uuid
from datetime import UTC

from apps.callsigns.models import (
    Callsign,
    CallsignVariant,
    normalize_callsign,
    parse_callsign,
)
from apps.locations import maidenhead, zones
from apps.logbook import bands, fields, sanity
from apps.logbook.signals import qso_changed
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone


class QSOConflict(Exception):
    """The QSO was changed by someone else since it was loaded"""


class ProtectedOperation(Exception):
    """A bulk operation that would skip the QSO history was attempted"""


class ReviewStatus(models.TextChoices):
    OK = "ok", "OK"
    NEEDS_REVIEW = "needs_review", "Needs review"
    REVIEWED = "reviewed", "Reviewed"


class QSOAction(models.TextChoices):
    CREATED = "created", "Created"
    UPDATED = "updated", "Updated"
    DELETED = "deleted", "Deleted"
    RESTORED = "restored", "Restored"
    REVIEWED = "reviewed", "Reviewed"
    REVERTED = "reverted", "Reverted"


_ASCII_UPPER = str.maketrans(string.ascii_lowercase, string.ascii_uppercase)


def ascii_upper(value: str) -> str:
    """Upper case only A-Z, so a value never changes length

    str.upper() can, it turns ß into SS"""
    return value.translate(_ASCII_UPPER)


def validate_safe_text(value: str) -> None:
    if fields.has_unsafe_chars(value):
        raise ValidationError("Control characters aren't allowed")


# Fields that are codes rather than free text, surrounding space is dropped
STRIPPED_FIELDS = frozenset(
    name
    for name, field in fields.BY_NAME.items()
    if field.is_text
    and name not in {"name", "qth", "comment", "notes", "my_rig", "my_antenna"}
)
# ADIF markers rather than fields, and our own fields, never kept in extra
RESERVED_EXTRA_KEYS = frozenset({"EOR", "EOH", fields.APP_UUID})
CALLSIGN_FIELDS = ("call", "station_callsign", "operator")
UPPER_FIELDS = frozenset(
    {
        "mode",
        "submode",
        "cont",
        "iota",
        "my_iota",
        "sota_ref",
        "my_sota_ref",
        "pota_ref",
        "my_pota_ref",
        "wwff_ref",
        "my_wwff_ref",
        "contest_id",
        "arrl_sect",
        "prop_mode",
        "qsl_sent",
        "qsl_rcvd",
        "lotw_qsl_sent",
        "lotw_qsl_rcvd",
        "eqsl_qsl_sent",
        "eqsl_qsl_rcvd",
    }
)


def _text(max_length: int = 255, **kwargs):
    return models.CharField(
        max_length=max_length,
        blank=True,
        validators=[validate_safe_text],
        **kwargs,
    )


def _decimal(max_digits=15, decimal_places=6, **kwargs):
    return models.DecimalField(
        max_digits=max_digits,
        decimal_places=decimal_places,
        null=True,
        blank=True,
        **kwargs,
    )


def _latitude():
    return _decimal(
        9, 6, validators=[MinValueValidator(-90), MaxValueValidator(90)]
    )


def _longitude():
    return _decimal(
        9, 6, validators=[MinValueValidator(-180), MaxValueValidator(180)]
    )


def _int(**kwargs):
    return models.IntegerField(null=True, blank=True, **kwargs)


def _zone(maximum: int):
    return models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(maximum)],
    )


class QSOQuerySet(models.QuerySet):
    def owned_by(self, user):
        return self.filter(owner=user)

    def live(self):
        return self.filter(deleted_at__isnull=True)

    def deleted(self):
        return self.filter(deleted_at__isnull=False)

    def needs_review(self):
        return self.filter(review_status=ReviewStatus.NEEDS_REVIEW)

    def delete(self, actor=None, reason: str = ""):
        """Mark each QSO deleted, they can be restored later

        Nothing is removed, so the deleted count is always 0"""
        with transaction.atomic():
            for qso in self.live().select_for_update():
                qso.delete(actor=actor, reason=reason)
        return 0, {}

    delete.alters_data = True
    delete.queryset_only = True

    def update(self, **kwargs):
        raise ProtectedOperation(
            "QSOs must be saved one at a time so their history is kept"
        )

    update.alters_data = True

    def bulk_update(self, *args, **kwargs):
        raise ProtectedOperation(
            "QSOs must be saved one at a time so their history is kept"
        )

    def bulk_create(self, *args, **kwargs):
        raise ProtectedOperation(
            "QSOs must be saved one at a time so their history is kept"
        )


class LiveQSOManager(models.Manager.from_queryset(QSOQuerySet)):
    """QSOs that haven't been deleted"""

    def get_queryset(self):
        return super().get_queryset().live()


class AllQSOManager(models.Manager.from_queryset(QSOQuerySet)):
    """Every QSO, deleted or not"""


class QSO(models.Model):
    """A contact, as logged by a user

    Field names follow ADIF, see apps.logbook.fields for how they map. ADIF
    fields without a column of their own are kept in extra by ADIF name"""

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="qsos",
    )
    version = models.PositiveIntegerField(default=0, editable=False)

    # The contact
    call = models.CharField(max_length=40, validators=[validate_safe_text])
    time_on = models.DateTimeField(db_index=True)
    time_off = models.DateTimeField(null=True, blank=True)
    band = _text(10)
    band_rx = _text(10)
    freq = _decimal()
    freq_rx = _decimal()
    mode = _text(32)
    submode = _text(32)
    rst_sent = _text(16)
    rst_rcvd = _text(16)
    tx_pwr = _decimal(12, 3)
    rx_pwr = _decimal(12, 3)
    prop_mode = _text(16)
    sat_name = _text(64)
    sat_mode = _text(32)

    # The station worked
    name = _text()
    qth = _text()
    gridsquare = _text(10)
    lat = _latitude()
    lon = _longitude()
    country = _text(100)
    dxcc = _int(validators=[MinValueValidator(0)])
    cqz = _zone(40)
    ituz = _zone(90)
    cont = _text(2)
    state = _text(50)
    cnty = _text(100)
    iota = _text(16)
    sota_ref = _text(32)
    pota_ref = _text(255)
    wwff_ref = _text(32)
    sig = _text(64)
    sig_info = _text(255)
    email = _text()
    qsl_via = _text()

    # Our station
    station_callsign = _text(40)
    operator = _text(40)
    callsign_variant = models.ForeignKey(
        CallsignVariant,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="qsos",
        editable=False,
    )
    my_location = models.ForeignKey(
        "locations.Location",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="qsos",
        help_text="Its details are copied into the my_ fields when logged",
    )
    my_gridsquare = _text(10)
    my_lat = _latitude()
    my_lon = _longitude()
    my_altitude = _decimal(10, 2)
    my_country = _text(100)
    my_dxcc = _int(validators=[MinValueValidator(0)])
    my_cq_zone = _zone(40)
    my_itu_zone = _zone(90)
    my_state = _text(50)
    my_cnty = _text(100)
    my_iota = _text(16)
    my_sota_ref = _text(32)
    my_pota_ref = _text(255)
    my_wwff_ref = _text(32)
    my_sig = _text(64)
    my_sig_info = _text(255)
    my_rig = _text()
    my_antenna = _text()

    # Contests
    session = models.ForeignKey(
        "logbook.LogSession",
        null=True,
        blank=True,
        # A session with contacts in it can't go, it holds their serials
        on_delete=models.PROTECT,
        related_name="qsos",
    )
    contest_id = _text(64)
    srx = _int(validators=[MinValueValidator(0)])
    stx = _int(validators=[MinValueValidator(0)])
    srx_string = _text()
    stx_string = _text()
    contest_check = _text(16)
    contest_class = _text(16)
    arrl_sect = _text(16)
    precedence = _text(16)

    # QSLs
    qsl_sent = _text(1)
    qsl_rcvd = _text(1)
    lotw_qsl_sent = _text(1)
    lotw_qsl_rcvd = _text(1)
    eqsl_qsl_sent = _text(1)
    eqsl_qsl_rcvd = _text(1)

    # Free text
    comment = models.TextField(blank=True, validators=[validate_safe_text])
    notes = models.TextField(blank=True, validators=[validate_safe_text])

    # Every other ADIF field, by ADIF name, e.g. {"QSLMSG": "TNX"}
    extra = models.JSONField(default=dict, blank=True)

    # Review of the sanity checks
    review_status = models.CharField(
        max_length=16,
        choices=ReviewStatus.choices,
        default=ReviewStatus.OK,
        db_index=True,
        editable=False,
    )
    issues = models.JSONField(default=list, blank=True, editable=False)
    reviewed_codes = models.JSONField(default=list, blank=True, editable=False)

    # Where the QSO came from
    import_batch = models.ForeignKey(
        "logbook.ImportBatch",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="qsos",
        editable=False,
    )
    source_record = models.TextField(
        blank=True,
        editable=False,
        help_text="The record exactly as it was imported",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)

    # The first manager is the default, which Django uses for related
    # lookups, validation and dumpdata, so keep deleted QSOs visible there
    all_objects = AllQSOManager()
    objects = LiveQSOManager()

    class Meta:
        verbose_name = "QSO"
        ordering = ("-time_on", "-id")
        default_manager_name = "all_objects"
        indexes = (
            models.Index(fields=["owner", "time_on"]),
            models.Index(fields=["owner", "call"]),
        )
        constraints = (
            # Serials are never given out twice in a session
            models.UniqueConstraint(
                fields=["session", "stx"],
                condition=Q(session__isnull=False, stx__isnull=False),
                name="qso_unique_session_serial",
                violation_error_message="Serial already used in this session",
            ),
            models.CheckConstraint(
                condition=~Q(call=""), name="qso_call_required"
            ),
        )

    def __str__(self) -> str:
        when = self.time_on.strftime("%Y-%m-%d %H:%M") if self.time_on else "?"
        return f"{self.call} {when} {self.band} {self.mode}".strip()

    # Saving

    def save(self, *args, actor=None, action=None, reason="", **kwargs):
        """Save the QSO, recording a revision and an event alongside it

        Raises ValidationError if the QSO can't be stored at all, and
        QSOConflict if it has been changed since it was loaded"""
        self.normalize()
        self.full_clean()
        self.resolve_callsign_variant()
        self._refresh_review()
        adding = self._state.adding
        with transaction.atomic():
            if adding:
                self.version = 1
            else:
                bumped = models.QuerySet.update(
                    QSO.all_objects.filter(pk=self.pk, version=self.version),
                    version=F("version") + 1,
                )
                if not bumped:
                    raise QSOConflict(
                        f"QSO {self.uuid} has changed since it was loaded"
                    )
                self.version += 1
            try:
                super().save(*args, **kwargs)
            except BaseException:
                if not adding:
                    self.version -= 1
                raise
            action = action or (
                QSOAction.CREATED if adding else QSOAction.UPDATED
            )
            snapshot = self.to_data(include_empty=True)
            QSORevision.objects.create(
                qso=self,
                version=self.version,
                action=action,
                actor=actor,
                reason=reason,
                data=snapshot,
            )
            event = QSOEvent.objects.create(
                owner_id=self.owner_id,
                qso=self,
                qso_uuid=self.uuid,
                version=self.version,
                event_type=f"qso.{action}",
                payload=snapshot,
            )
            transaction.on_commit(
                lambda: qso_changed.send(sender=QSO, qso=self, event=event)
            )

    save.alters_data = True

    def delete(self, using=None, keep_parents=False, actor=None, reason=""):
        """Mark the QSO deleted rather than removing it

        It is hidden from logbooks and exports until restored. Nothing is
        removed, so the deleted count is always 0"""
        if not self.is_deleted:
            self.deleted_at = timezone.now()
            self.save(actor=actor, action=QSOAction.DELETED, reason=reason)
        return 0, {}

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def issue_list(self) -> list[sanity.Issue]:
        return [sanity.Issue.from_dict(issue) for issue in self.issues]

    # Validation

    def normalize(self) -> None:
        """Tidy up the values without changing what they mean"""
        for field in fields.FIELDS:
            if field.is_text and getattr(self, field.name) is None:
                setattr(self, field.name, "")
        for name in STRIPPED_FIELDS:
            value = getattr(self, name)
            if isinstance(value, str):
                setattr(self, name, value.strip())
        for name in CALLSIGN_FIELDS:
            setattr(self, name, normalize_callsign(getattr(self, name) or ""))
        for name in UPPER_FIELDS:
            setattr(self, name, ascii_upper(getattr(self, name)))
        self.band = bands.normalize_band(self.band)
        self.band_rx = bands.normalize_band(self.band_rx)
        if not self.band:
            self.band = bands.band_for_freq(self.freq) or ""
        if not self.band_rx:
            self.band_rx = bands.band_for_freq(self.freq_rx) or ""
        for name in ("gridsquare", "my_gridsquare"):
            value = getattr(self, name)
            if maidenhead.is_valid_grid(value):
                setattr(self, name, maidenhead.normalize_grid(value))
        # ADIF times only go down to the second
        for name in ("time_on", "time_off"):
            value = getattr(self, name)
            if value is not None and timezone.is_aware(value):
                setattr(
                    self, name, value.astimezone(UTC).replace(microsecond=0)
                )
        if self.extra is None:
            self.extra = {}

    def fill_zones(self) -> None:
        """Fill in the zones that haven't been given from the grid squares,
        each station's from its own"""
        for grid, cq, itu in (
            ("gridsquare", "cqz", "ituz"),
            ("my_gridsquare", "my_cq_zone", "my_itu_zone"),
        ):
            value = getattr(self, grid)
            if not maidenhead.is_valid_grid(value or ""):
                continue
            if getattr(self, cq) is not None and getattr(self, itu) is not None:
                continue
            found = zones.zones_for_grid(value)
            if getattr(self, cq) is None:
                setattr(self, cq, found.cq)
            if getattr(self, itu) is None:
                setattr(self, itu, found.itu)

    def clean(self):
        super().clean()
        errors = {}
        for name in ("time_on", "time_off"):
            value = getattr(self, name)
            if value is not None and timezone.is_naive(value):
                errors[name] = "Times must include a timezone"
        if not isinstance(self.extra, dict):
            errors["extra"] = "Extra fields must be a mapping"
        else:
            for key, value in self.extra.items():
                if not isinstance(key, str) or not fields.EXTRA_KEY_RE.match(
                    key
                ):
                    errors["extra"] = f"{key!r} isn't a valid ADIF field name"
                elif key in RESERVED_EXTRA_KEYS:
                    errors["extra"] = f"{key} can't be used as a field name"
                elif not isinstance(value, str):
                    errors["extra"] = f"{key} must be text"
                elif fields.has_unsafe_chars(value):
                    errors["extra"] = f"{key} has control characters in it"
        if self.my_location is not None and not self.my_location.usable_by(
            self.owner
        ):
            errors["my_location"] = "This belongs to someone else"
        if self.session is not None and self.session.owner_id != self.owner_id:
            errors["session"] = "This belongs to someone else"
        if errors:
            raise ValidationError(errors)

    def resolve_callsign_variant(self) -> None:
        """Link the station callsign to the user's callsign, if it is theirs"""
        self.callsign_variant = None
        if not self.station_callsign or self.time_on is None:
            return
        try:
            parsed = parse_callsign(self.station_callsign)
        except ValueError:
            return
        callsign = Callsign.objects.filter(call=parsed.call).first()
        if (
            callsign is None
            or callsign.role_for(self.owner, self.time_on) is None
        ):
            return
        try:
            self.callsign_variant = callsign.get_variant(
                prefix=parsed.prefix, suffix=parsed.suffix
            )
        except ValidationError:
            # An odd prefix or suffix, the callsign text is still logged
            self.callsign_variant = None

    def sanity_check(self) -> list[sanity.Issue]:
        """Run the sanity checks, see apps.logbook.sanity"""
        return sanity.check(self)

    def _refresh_review(self) -> None:
        issues = self.sanity_check()
        self.issues = [issue.as_dict() for issue in issues]
        codes = {issue.code for issue in issues}
        if not codes:
            self.review_status = ReviewStatus.OK
        elif codes <= set(self.reviewed_codes):
            self.review_status = ReviewStatus.REVIEWED
        else:
            self.review_status = ReviewStatus.NEEDS_REVIEW

    def restore(self, actor=None, reason="") -> None:
        if self.is_deleted:
            self.deleted_at = None
            self.save(actor=actor, action=QSOAction.RESTORED, reason=reason)

    def mark_reviewed(self, actor=None, reason="") -> None:
        """Accept the current issues, they won't flag the QSO again

        A new kind of issue appearing later will"""
        self.reviewed_codes = sorted(
            {issue["code"] for issue in self.issues} | set(self.reviewed_codes)
        )
        self.save(actor=actor, action=QSOAction.REVIEWED, reason=reason)

    # Serialising

    def to_data(self, include_empty: bool = False) -> dict:
        """The QSO as plain JSON values, keyed by field name

        Used for revisions, events and the JSON export"""
        data = {"uuid": str(self.uuid)}
        for field in fields.FIELDS:
            value = fields.to_json_value(field, getattr(self, field.name))
            if include_empty or value not in (None, ""):
                data[field.name] = value
        if include_empty or self.extra:
            data["extra"] = dict(sorted((self.extra or {}).items()))
        if include_empty:
            data.update(
                my_location=self.my_location_id,
                session=self.session_id,
                review_status=self.review_status,
                issues=self.issues,
                reviewed_codes=self.reviewed_codes,
                deleted_at=(
                    fields.format_datetime(self.deleted_at)
                    if self.deleted_at
                    else None
                ),
            )
        return data

    def apply_data(self, data: dict) -> None:
        """Set field values from to_data style JSON values

        Raises ValueError if a value can't be read"""
        for key, value in data.items():
            if key == "extra":
                self.extra = dict(value or {})
            elif key in fields.BY_NAME:
                field = fields.BY_NAME[key]
                value = fields.from_json_value(field, value)
                if value is None and field.is_text:
                    value = ""
                setattr(self, key, value)


class QSORevisionQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ProtectedOperation("Revisions can't be changed")

    def delete(self):
        raise ProtectedOperation("Revisions can't be deleted")


class QSORevision(models.Model):
    """The whole QSO as it was after each save, never changed or removed

    Removed only along with the QSO itself, which only happens when the
    owner's account is deleted"""

    qso = models.ForeignKey(
        QSO, on_delete=models.CASCADE, related_name="revisions"
    )
    version = models.PositiveIntegerField()
    action = models.CharField(max_length=16, choices=QSOAction.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    reason = models.CharField(max_length=255, blank=True)
    data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    objects = QSORevisionQuerySet.as_manager()

    class Meta:
        ordering = ("qso", "version")
        constraints = (
            models.UniqueConstraint(
                fields=["qso", "version"], name="qso_revision_unique_version"
            ),
        )

    def __str__(self) -> str:
        return f"{self.qso_id} v{self.version} {self.action}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ProtectedOperation("Revisions can't be changed")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedOperation("Revisions can't be deleted")


class QSOEventQuerySet(models.QuerySet):
    def pending(self):
        return self.filter(dispatched_at__isnull=True)


class QSOEvent(models.Model):
    """Something that happened to a QSO, for other systems to act on

    Written in the same transaction as the change, so an event exists if and
    only if the change was saved. Dispatching them elsewhere comes later,
    pending() are those not yet sent anywhere"""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="qso_events",
    )
    qso = models.ForeignKey(
        QSO,
        null=True,
        on_delete=models.SET_NULL,
        related_name="events",
    )
    qso_uuid = models.UUIDField(db_index=True)
    version = models.PositiveIntegerField()
    event_type = models.CharField(max_length=32)
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)

    objects = QSOEventQuerySet.as_manager()

    class Meta:
        ordering = ("id",)

    def __str__(self) -> str:
        return f"{self.event_type} {self.qso_uuid} v{self.version}"

    def mark_dispatched(self) -> None:
        self.dispatched_at = timezone.now()
        self.save(update_fields=["dispatched_at"])
