from apps.logbook import fields, query
from apps.logbook.models.qso import QSO, ProtectedOperation
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone


def match_all() -> dict:
    return {"and": []}


class LogbookQuerySet(models.QuerySet):
    def master_for(self, user) -> Logbook:
        """The user's master logbook, every QSO they have logged"""
        logbook, _ = self.get_or_create(
            owner=user,
            is_master=True,
            defaults={"name": "Master log"},
        )
        return logbook


class Logbook(models.Model):
    """A view onto the user's QSOs, picked out by a filter

    QSOs belong to the user, not to logbooks, so the same QSO can appear in
    any number of logbooks and deleting a logbook never touches a QSO. Every
    user has a master logbook with no filter, holding everything"""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="logbooks",
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_master = models.BooleanField(default=False, editable=False)
    filter = models.JSONField(
        default=match_all,
        blank=True,
        help_text="Which QSOs are in the logbook, see apps.logbook.query",
    )
    # The filter in the text syntax, kept in step with filter on save
    query = models.TextField(blank=True, editable=False)
    template = models.ForeignKey(
        "logbook.LogTemplate",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="logbooks",
    )
    default_location = models.ForeignKey(
        "locations.Location",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = LogbookQuerySet.as_manager()

    class Meta:
        ordering = ("owner", "-is_master", "name")
        constraints = (
            models.UniqueConstraint(
                fields=["owner", "name"], name="logbook_unique_name"
            ),
            models.UniqueConstraint(
                fields=["owner"],
                condition=Q(is_master=True),
                name="logbook_one_master",
            ),
        )

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        try:
            self.filter = query.validate(self.filter)
            self.query = query.format_filter(self.filter)
        except query.QueryError as exc:
            raise ValidationError({"filter": str(exc)}) from exc
        if self.is_master and self.filter != query.MATCH_ALL:
            raise ValidationError(
                {"filter": "The master logbook always holds every QSO"}
            )
        if (
            self.template is not None
            and self.template.owner_id != self.owner_id
        ):
            raise ValidationError({"template": "This belongs to someone else"})
        location = self.default_location
        if location is not None and not location.usable_by(self.owner):
            raise ValidationError(
                {"default_location": "This belongs to someone else"}
            )

    def delete(self, *args, **kwargs):
        """Delete the logbook, its QSOs stay in the master log"""
        if self.is_master:
            raise ProtectedOperation("The master logbook can't be deleted")
        return super().delete(*args, **kwargs)

    def set_query(self, text: str) -> None:
        """Set the filter from the text syntax

        Raises ValidationError if the text can't be understood"""
        try:
            self.filter = query.parse(text)
        except query.QueryError as exc:
            raise ValidationError({"query": str(exc)}) from exc
        self.query = query.format_filter(self.filter)

    def qsos(self, include_deleted: bool = False):
        manager = QSO.all_objects if include_deleted else QSO.objects
        return manager.filter(owner_id=self.owner_id).filter(
            query.compile_filter(self.filter)
        )

    def matches(self, qso: QSO) -> bool:
        """Would the QSO be shown in this logbook?"""
        return self.qsos(include_deleted=True).filter(pk=qso.pk).exists()


# Fields a template can lay out, and what QRZ lookups can fill in
TEMPLATE_FIELDS = frozenset(fields.BY_NAME)
QRZ_FIELDS = frozenset(
    {
        "name",
        "qth",
        "state",
        "cnty",
        "country",
        "gridsquare",
        "lat",
        "lon",
        "dxcc",
        "cqz",
        "ituz",
        "iota",
        "email",
        "qsl_via",
    }
)
# Fields that don't make sense to default, they're different every time
NO_DEFAULT_FIELDS = frozenset({"call", "time_on", "time_off", "stx", "srx"})
MAX_WIDTH = 100


class LogTemplate(models.Model):
    """How the QSO entry form is laid out, and what it starts filled with

    layout is a list in display order of {"field": name, "width": n} and
    optionally "label". defaults are field values to start each QSO with.
    With qrz_lookup on, the qrz_fields are filled in from QRZ.com once the
    callsign is known"""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="log_templates",
    )
    name = models.CharField(max_length=100)
    layout = models.JSONField(default=list)
    defaults = models.JSONField(default=dict, blank=True)
    qrz_lookup = models.BooleanField(default=False)
    qrz_fields = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("owner", "name")
        constraints = (
            models.UniqueConstraint(
                fields=["owner", "name"], name="log_template_unique_name"
            ),
        )

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        errors = {}
        try:
            self.layout = self._clean_layout(self.layout)
        except ValidationError as exc:
            errors["layout"] = exc.messages
        try:
            self.defaults = self._clean_defaults(self.defaults)
        except ValidationError as exc:
            errors["defaults"] = exc.messages
        if not isinstance(self.qrz_fields, list) or not all(
            field in QRZ_FIELDS for field in self.qrz_fields
        ):
            errors["qrz_fields"] = (
                f"QRZ can fill in {', '.join(sorted(QRZ_FIELDS))}"
            )
        elif len(set(self.qrz_fields)) != len(self.qrz_fields):
            errors["qrz_fields"] = "A field is listed more than once"
        if errors:
            raise ValidationError(errors)

    @staticmethod
    def _clean_layout(layout) -> list[dict]:
        if not isinstance(layout, list):
            raise ValidationError("Layout must be a list of fields")
        cleaned, seen = [], set()
        for item in layout:
            if not isinstance(item, dict) or not set(item) <= {
                "field",
                "width",
                "label",
            }:
                raise ValidationError(
                    "Each layout entry is a field, width and optional label"
                )
            name = item.get("field")
            if name not in TEMPLATE_FIELDS:
                raise ValidationError(f"Unknown field {name!r}")
            if name in seen:
                raise ValidationError(f"{name} is in the layout twice")
            seen.add(name)
            width = item.get("width")
            if (
                not isinstance(width, int)
                or isinstance(width, bool)
                or not 1 <= width <= MAX_WIDTH
            ):
                raise ValidationError(
                    f"{name} width must be between 1 and {MAX_WIDTH}"
                )
            entry = {"field": name, "width": width}
            if "label" in item:
                if (
                    not isinstance(item["label"], str)
                    or len(item["label"]) > 64
                ):
                    raise ValidationError(f"{name} label must be short text")
                entry["label"] = item["label"]
            cleaned.append(entry)
        if "call" not in seen:
            raise ValidationError("The layout must include the call")
        return cleaned

    @staticmethod
    def _clean_defaults(defaults) -> dict:
        if not isinstance(defaults, dict):
            raise ValidationError("Defaults must be a mapping of field values")
        for name, value in defaults.items():
            if name not in TEMPLATE_FIELDS or name in NO_DEFAULT_FIELDS:
                raise ValidationError(f"{name!r} can't have a default")
            try:
                fields.from_json_value(fields.BY_NAME[name], value)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
        return defaults

    @property
    def field_names(self) -> list[str]:
        return [item["field"] for item in self.layout]


class LogSession(models.Model):
    """A stretch of operating, e.g. a contest entry or a park activation

    QSOs logged in a session take its details, and in a contest are given
    the next serial number. Serials are handed out under a row lock, so they
    are never given out twice even with several people logging at once, and
    a serial taken by a QSO that then fails to save is handed back. Serials
    of deleted QSOs are never reused

    TODO: Club logging, with several operators sharing a callsign and a
    session, will build on this"""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="log_sessions",
    )
    name = models.CharField(max_length=100)
    logbook = models.ForeignKey(
        Logbook,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sessions",
    )
    contest_id = models.CharField(
        max_length=64, blank=True, help_text="ADIF CONTEST_ID, e.g. CQ-WW-CW"
    )
    station_callsign = models.CharField(max_length=40, blank=True)
    operator = models.CharField(max_length=40, blank=True)
    my_location = models.ForeignKey(
        "locations.Location",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    uses_serials = models.BooleanField(
        default=False, help_text="Give each QSO the next serial number"
    )
    serial_start = models.PositiveIntegerField(default=1)
    next_serial = models.PositiveIntegerField(default=1, editable=False)
    stx_string = models.CharField(
        max_length=255, blank=True, help_text="Exchange sent with each QSO"
    )
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("owner", "-started_at")
        constraints = (
            models.CheckConstraint(
                condition=Q(next_serial__gte=F("serial_start")),
                name="log_session_next_serial_after_start",
            ),
            models.CheckConstraint(
                condition=Q(ended_at__isnull=True)
                | Q(ended_at__gte=F("started_at")),
                name="log_session_ends_after_start",
            ),
        )

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if self._state.adding:
            self.next_serial = max(self.next_serial, self.serial_start)
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.logbook is not None and self.logbook.owner_id != self.owner_id:
            raise ValidationError({"logbook": "This belongs to someone else"})
        location = self.my_location
        if location is not None and not location.usable_by(self.owner):
            raise ValidationError(
                {"my_location": "This belongs to someone else"}
            )

    @property
    def is_active(self) -> bool:
        return self.ended_at is None or self.ended_at > timezone.now()

    def end(self, at=None) -> None:
        self.ended_at = at or timezone.now()
        self.save()

    def peek_serial(self) -> int:
        """The serial the next QSO will get, without taking it"""
        return (
            LogSession.objects.filter(pk=self.pk)
            .values_list("next_serial", flat=True)
            .get()
        )

    def allocate_serial(self) -> int:
        """Take the next serial

        Call inside the transaction saving the QSO, so the serial is handed
        back if the QSO isn't saved"""
        with transaction.atomic():
            serial = (
                LogSession.objects.select_for_update()
                .filter(pk=self.pk)
                .values_list("next_serial", flat=True)
                .get()
            )
            LogSession.objects.filter(pk=self.pk).update(
                next_serial=F("next_serial") + 1
            )
        self.next_serial = serial + 1
        return serial

    def qso_defaults(self) -> dict:
        """Field values a QSO logged in this session starts with"""
        values = {
            "contest_id": self.contest_id,
            "station_callsign": self.station_callsign,
            "operator": self.operator,
            "stx_string": self.stx_string,
        }
        return {key: value for key, value in values.items() if value}
