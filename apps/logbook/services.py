"""Logging, changing and pre-filling QSOs

This is how QSOs should be logged and changed. Everything here runs in a
transaction, so a QSO is either saved along with its revision, event and
serial number, or nothing is"""

import uuid as uuid_module
from dataclasses import dataclass, field

from apps.logbook import fields, sanity
from apps.logbook.models import (
    QSO,
    LogSession,
    QSOAction,
    QSOConflict,
    QSORevision,
)
from apps.logbook.models.logbook import QRZ_FIELDS
from django.core.exceptions import ValidationError
from django.db import transaction


class SanityCheckError(Exception):
    """The QSO has warnings that haven't been accepted, nothing was saved

    Log it again with accept_warnings=True to keep it, flagged for review"""

    def __init__(self, issues: list[sanity.Issue]):
        self.issues = issues
        super().__init__("; ".join(issue.message for issue in issues))


ALLOWED_KEYS = frozenset(fields.BY_NAME) | {"extra"}


def _apply(qso: QSO, values: dict) -> None:
    unknown = set(values) - ALLOWED_KEYS
    if unknown:
        raise ValidationError(
            f"Unknown QSO fields: {', '.join(sorted(unknown))}"
        )
    for key, value in values.items():
        setattr(qso, key, dict(value) if key == "extra" else value)


def _check(
    qso: QSO, accept_warnings: bool, fill_zones: bool = False
) -> list[sanity.Issue]:
    """Validate the QSO, raising if it can't or shouldn't be saved"""
    qso.normalize()
    if fill_zones:
        qso.fill_zones()
    qso.full_clean()
    qso.resolve_callsign_variant()
    issues = qso.sanity_check()
    new_issues = [
        issue for issue in issues if issue.code not in qso.reviewed_codes
    ]
    if new_issues and not accept_warnings:
        raise SanityCheckError(new_issues)
    return issues


def log_qso(
    owner,
    values: dict,
    *,
    accept_warnings: bool = False,
    session: LogSession | None = None,
    location=None,
    logbook=None,
    qso_uuid: str | uuid_module.UUID | None = None,
    actor=None,
    reason: str = "",
    import_batch=None,
    source_record: str = "",
) -> QSO:
    """Log a QSO

    values are QSO field values, plus extra for other ADIF fields. Details
    from the session and location fill in anything not given, and zones not
    given come from the grid squares. Raises
    ValidationError if the QSO can't be logged and SanityCheckError if it has
    warnings that haven't been accepted.

    Giving a uuid makes logging safe to retry: if a QSO with that uuid has
    already been logged it is returned unchanged"""
    if qso_uuid:
        qso_uuid = uuid_module.UUID(str(qso_uuid))
        existing = QSO.all_objects.filter(uuid=qso_uuid).first()
        if existing is not None:
            if existing.owner_id != owner.pk:
                raise ValidationError({"uuid": "This uuid is already in use"})
            return existing
    if location is None and session is not None:
        location = session.my_location
    if location is None and logbook is not None:
        location = logbook.default_location

    with transaction.atomic():
        qso = QSO(owner=owner)
        if qso_uuid:
            qso.uuid = qso_uuid
        if location is not None:
            _apply(qso, location.adif_fields())
            qso.my_location = location
        if session is not None:
            _apply(qso, session.qso_defaults())
            qso.session = session
        _apply(qso, values)
        if session is not None and session.uses_serials and qso.stx is None:
            # Taken in this transaction, so handed back if the QSO isn't
            # saved
            qso.stx = session.allocate_serial()
        qso.import_batch = import_batch
        qso.source_record = source_record
        # Imported QSOs are kept as they were in the file
        _check(qso, accept_warnings, fill_zones=import_batch is None)
        qso.save(actor=actor or owner, reason=reason)
    return qso


def update_qso(
    qso: QSO,
    changes: dict,
    *,
    accept_warnings: bool = False,
    expected_version: int | None = None,
    actor=None,
    reason: str = "",
) -> QSO:
    """Change a QSO

    Raises QSOConflict if the QSO has changed since expected_version, or
    since it was loaded, so a change is never made to stale data"""
    if qso.is_deleted:
        raise ValidationError("Restore the QSO before changing it")
    if expected_version is not None and expected_version != qso.version:
        raise QSOConflict(f"QSO {qso.uuid} is at version {qso.version}")
    with transaction.atomic():
        _apply(qso, changes)
        _check(qso, accept_warnings, fill_zones=True)
        qso.save(actor=actor, reason=reason)
    return qso


def revert_qso(
    qso: QSO, revision: QSORevision, *, actor=None, reason: str = ""
) -> QSO:
    """Put a QSO's fields back to how they were at an earlier revision

    The revert is itself a new revision, so it can be undone too"""
    if revision.qso_id != qso.pk:
        raise ValidationError("That revision belongs to a different QSO")
    data = {
        key: value
        for key, value in revision.data.items()
        if key in fields.BY_NAME or key == "extra"
    }
    with transaction.atomic():
        qso.apply_data(data)
        qso.save(
            actor=actor,
            action=QSOAction.REVERTED,
            reason=reason or f"Reverted to version {revision.version}",
        )
    return qso


@dataclass
class Prefill:
    """Starting values for a new QSO, as JSON values

    sources says where each value came from: template, location, session or
    qrz. messages explains anything that couldn't be filled in"""

    values: dict = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    layout: list[dict] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    def add(self, source: str, values: dict) -> None:
        for key, value in values.items():
            if value is None or value == "":
                continue
            self.values[key] = fields.to_json_value(fields.BY_NAME[key], value)
            self.sources[key] = source


def prefill(
    owner,
    *,
    call: str = "",
    template=None,
    logbook=None,
    session: LogSession | None = None,
    location=None,
    fetch=None,
) -> Prefill:
    """Work out what a new QSO should start filled in with

    Later sources win over earlier ones: the template's defaults, then the
    location, then the session, then a QRZ lookup of the call"""
    from apps.qrz import services as qrz

    if template is None and logbook is not None:
        template = logbook.template
    if location is None and session is not None:
        location = session.my_location
    if location is None and logbook is not None:
        location = logbook.default_location

    result = Prefill()
    if template is not None:
        result.layout = list(template.layout)
        result.add(
            "template",
            {
                key: fields.from_json_value(fields.BY_NAME[key], value)
                for key, value in template.defaults.items()
            },
        )
    if location is not None:
        result.add("location", location.adif_fields())
    if session is not None:
        result.add("session", session.qso_defaults())
        if session.uses_serials:
            result.add("session", {"stx": session.peek_serial()})
    if call and template is not None and template.qrz_lookup:
        wanted = set(template.qrz_fields) or QRZ_FIELDS
        try:
            found = qrz.lookup(owner, call, fetch=fetch)
        except qrz.QRZError as exc:
            result.messages.append(f"QRZ lookup failed: {exc}")
        else:
            result.add(
                "qrz",
                {
                    key: value
                    for key, value in found.qso_fields().items()
                    if key in wanted
                },
            )
    return result
