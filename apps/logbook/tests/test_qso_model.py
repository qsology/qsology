"""The QSO model: what it holds, and that it never loses anything"""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from apps.callsigns.models import Callsign, CallsignVariant
from apps.locations.models import Location
from apps.logbook.models import (
    QSO,
    ImportBatch,
    LogSession,
    ProtectedOperation,
    QSOAction,
    QSOConflict,
    QSOEvent,
    QSORevision,
    ReviewStatus,
)
from apps.logbook.signals import qso_changed
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models.deletion import ProtectedError

from .conftest import T0

pytestmark = pytest.mark.django_db


def make(user, **values):
    """Save a QSO straight through the model"""
    defaults = {"call": "K1ABC", "time_on": T0, "station_callsign": "M0WME"}
    qso = QSO(owner=user, **(defaults | values))
    qso.save()
    return qso


class TestNormalize:
    def test_callsigns_are_upper_case_without_spaces(self, user):
        qso = make(user, call=" k1 abc ", station_callsign="m0wme/p")
        assert qso.call == "K1ABC"
        assert qso.station_callsign == "M0WME/P"

    def test_codes_are_tidied(self, user):
        qso = make(user, mode=" ft8 ", band=" 20M ", pota_ref="us-0001")
        assert qso.mode == "FT8"
        assert qso.band == "20m"
        assert qso.pota_ref == "US-0001"

    def test_band_comes_from_freq(self, user):
        qso = make(user, freq=Decimal("7.074"), freq_rx=Decimal("14.2"))
        assert qso.band == "40m"
        assert qso.band_rx == "20m"

    def test_band_given_is_kept_even_if_it_disagrees(self, user):
        # It is flagged for review, but never changed behind the user's back
        qso = make(user, freq=Decimal("7.074"), band="20m")
        assert qso.band == "20m"
        assert qso.review_status == ReviewStatus.NEEDS_REVIEW

    def test_grid_case_is_normalized(self, user):
        qso = make(user, gridsquare="fn31PR", my_gridsquare="io91WM")
        assert qso.gridsquare == "FN31pr"
        assert qso.my_gridsquare == "IO91wm"

    def test_invalid_grid_is_kept_as_given(self, user):
        qso = make(user, gridsquare="ZZ99")
        assert qso.gridsquare == "ZZ99"

    def test_times_are_utc_to_the_second(self, user):
        eastern = timezone(timedelta(hours=-5))
        qso = make(
            user, time_on=datetime(2024, 6, 1, 7, 0, 30, 999, tzinfo=eastern)
        )
        assert qso.time_on == datetime(2024, 6, 1, 12, 0, 30, tzinfo=UTC)
        assert qso.time_on.tzinfo == UTC

    def test_free_text_keeps_its_spacing(self, user):
        qso = make(user, comment="  hello\n  world  ", name=" Fred ")
        assert qso.comment == "  hello\n  world  "
        assert qso.name == " Fred "

    def test_none_text_is_empty(self, user):
        qso = make(user, mode=None, comment=None)
        assert qso.mode == ""
        assert qso.comment == ""


class TestValidation:
    """Things that can never be logged, nothing is saved"""

    @pytest.mark.parametrize(
        ("values", "field"),
        [
            ({"call": ""}, "call"),
            ({"call": "   "}, "call"),
            ({"time_on": None}, "time_on"),
            ({"time_on": datetime(2024, 6, 1, 12, 0)}, "time_on"),
            ({"time_off": datetime(2024, 6, 1, 12, 0)}, "time_off"),
            ({"comment": "bell\x07"}, "comment"),
            ({"name": "nul\x00"}, "name"),
            ({"call": "K1\x1bABC"}, "call"),
            ({"lat": Decimal("91")}, "lat"),
            ({"lon": Decimal("-180.5")}, "lon"),
            ({"cqz": 41}, "cqz"),
            ({"ituz": 0}, "ituz"),
            ({"band": "x" * 11}, "band"),
            ({"extra": []}, "extra"),
            ({"extra": {"lower": "x"}}, "extra"),
            ({"extra": {"1ABC": "x"}}, "extra"),
            ({"extra": {"QSLMSG": 5}}, "extra"),
            ({"extra": {"QSLMSG": "\x01"}}, "extra"),
        ],
    )
    def test_rejected(self, user, values, field):
        with pytest.raises(ValidationError) as excinfo:
            make(user, **values)
        assert field in excinfo.value.message_dict
        assert not QSO.all_objects.exists()
        assert not QSORevision.objects.exists()
        assert not QSOEvent.objects.exists()

    def test_another_users_location(self, user, other_user):
        location = Location.objects.create(owner=other_user, name="Theirs")
        with pytest.raises(ValidationError, match="someone else"):
            make(user, my_location=location)

    def test_another_users_session(self, user, other_user):
        session = LogSession.objects.create(owner=other_user, name="Theirs")
        with pytest.raises(ValidationError, match="someone else"):
            make(user, session=session)

    def test_database_refuses_a_blank_call(self, user):
        qso = QSO(owner=user, call="", time_on=T0)
        with pytest.raises(IntegrityError), transaction.atomic():
            # Skip our save, as if something wrote to the table directly
            models.Model.save(qso)

    def test_unicode_is_fine(self, user):
        qso = make(user, name="Józef Ślusarczyk", comment="73 de 日本 🙂")
        qso.refresh_from_db()
        assert qso.name == "Józef Ślusarczyk"
        assert qso.comment == "73 de 日本 🙂"


class TestVersioning:
    def test_new_qso_is_version_one(self, user):
        assert make(user).version == 1

    def test_each_save_is_a_new_version(self, user):
        qso = make(user)
        qso.comment = "one"
        qso.save()
        qso.comment = "two"
        qso.save()
        assert qso.version == 3
        qso.refresh_from_db()
        assert qso.version == 3

    def test_stale_copy_cant_overwrite(self, user):
        qso = make(user)
        stale = QSO.objects.get(pk=qso.pk)
        qso.comment = "newer"
        qso.save()

        stale.comment = "older"
        with pytest.raises(QSOConflict):
            stale.save()

        qso.refresh_from_db()
        assert qso.comment == "newer"
        assert qso.version == 2
        assert stale.version == 1
        assert qso.revisions.count() == 2

    def test_reloading_after_a_conflict_works(self, user):
        qso = make(user)
        stale = QSO.objects.get(pk=qso.pk)
        qso.save()
        with pytest.raises(QSOConflict):
            stale.save()
        stale.refresh_from_db()
        stale.comment = "retried"
        stale.save()
        assert stale.version == 3

    def test_failed_save_leaves_version_alone(self, user, monkeypatch):
        qso = make(user)

        def broken(*args, **kwargs):
            raise RuntimeError("database fell over")

        monkeypatch.setattr(models.Model, "save", broken)
        with pytest.raises(RuntimeError):
            qso.save()
        monkeypatch.undo()
        assert qso.version == 1
        qso.save()
        assert qso.version == 2


class TestRevisions:
    def test_every_save_is_recorded(self, user, other_user):
        qso = make(user)
        qso.comment = "changed"
        qso.save(actor=other_user, reason="typo")

        first, second = qso.revisions.all()
        assert (first.version, first.action) == (1, QSOAction.CREATED)
        assert (second.version, second.action) == (2, QSOAction.UPDATED)
        assert second.actor == other_user
        assert second.reason == "typo"
        assert first.data["comment"] == ""
        assert second.data["comment"] == "changed"

    def test_revision_holds_the_whole_qso(self, user):
        qso = make(user, extra={"QSLMSG": "TNX"}, freq=Decimal("14.2"))
        assert qso.revisions.get().data == qso.to_data(include_empty=True)

    def test_revisions_cant_be_changed(self, user):
        revision = make(user).revisions.get()
        revision.reason = "rewriting history"
        with pytest.raises(ProtectedOperation):
            revision.save()
        with pytest.raises(ProtectedOperation):
            QSORevision.objects.update(reason="rewriting history")

    def test_revisions_cant_be_deleted(self, user):
        revision = make(user).revisions.get()
        with pytest.raises(ProtectedOperation):
            revision.delete()
        with pytest.raises(ProtectedOperation):
            QSORevision.objects.all().delete()
        assert QSORevision.objects.count() == 1

    def test_revision_versions_are_unique(self, user):
        qso = make(user)
        with pytest.raises(IntegrityError), transaction.atomic():
            QSORevision.objects.create(
                qso=qso, version=1, action=QSOAction.UPDATED, data={}
            )


class TestEvents:
    def test_each_save_raises_an_event(self, user):
        qso = make(user)
        qso.delete()
        qso.restore()
        events = list(QSOEvent.objects.values_list("event_type", "version"))
        assert events == [
            ("qso.created", 1),
            ("qso.deleted", 2),
            ("qso.restored", 3),
        ]
        event = QSOEvent.objects.first()
        assert event.qso_uuid == qso.uuid
        assert event.owner == user
        assert event.payload["call"] == "K1ABC"

    def test_signal_sent_once_committed(
        self, user, django_capture_on_commit_callbacks
    ):
        received = []

        def receiver(sender, qso, event, **kwargs):
            received.append((qso.call, event.event_type))

        qso_changed.connect(receiver)
        try:
            with django_capture_on_commit_callbacks(execute=True):
                make(user)
                assert received == []
        finally:
            qso_changed.disconnect(receiver)
        assert received == [("K1ABC", "qso.created")]

    def test_nothing_happens_when_rolled_back(
        self, user, django_capture_on_commit_callbacks
    ):
        received = []

        def receiver(sender, **kwargs):
            received.append(kwargs)

        qso_changed.connect(receiver)
        try:
            with (
                django_capture_on_commit_callbacks(execute=True),
                pytest.raises(RuntimeError),
                transaction.atomic(),
            ):
                make(user)
                raise RuntimeError("rolled back")
        finally:
            qso_changed.disconnect(receiver)
        assert received == []
        assert not QSO.all_objects.exists()
        assert not QSOEvent.objects.exists()

    def test_pending_and_dispatched(self, user):
        make(user)
        event = QSOEvent.objects.pending().get()
        event.mark_dispatched()
        assert not QSOEvent.objects.pending().exists()
        assert event.dispatched_at is not None

    def test_events_go_with_the_account(self, user):
        make(user).delete()
        user.schedule_deletion(grace_period=timedelta(0))
        assert QSOEvent.objects.count() == 2
        user._hard_delete()
        assert not QSOEvent.objects.exists()


class TestSoftDelete:
    def test_delete_hides_but_keeps(self, user):
        qso = make(user)
        assert qso.delete() == (0, {})

        assert not QSO.objects.exists()
        kept = QSO.all_objects.get(pk=qso.pk)
        assert kept.is_deleted
        assert kept.revisions.last().action == QSOAction.DELETED

    def test_restore(self, user):
        qso = make(user)
        qso.delete(reason="oops")
        qso.restore(reason="not oops")
        assert QSO.objects.get(pk=qso.pk).deleted_at is None
        actions = list(qso.revisions.values_list("action", flat=True))
        assert actions == ["created", "deleted", "restored"]

    def test_deleting_twice_does_nothing(self, user):
        qso = make(user)
        qso.delete()
        qso.delete()
        assert qso.version == 2

    def test_queryset_delete_is_soft(self, user):
        for minutes in range(3):
            make(user, time_on=T0 + timedelta(minutes=minutes * 30))
        assert QSO.objects.all().delete() == (0, {})
        assert QSO.all_objects.count() == 3
        assert QSO.all_objects.deleted().count() == 3
        assert QSORevision.objects.filter(action="deleted").count() == 3

    def test_related_manager_delete_is_soft(self, user):
        make(user)
        user.qsos.all().delete()
        assert QSO.all_objects.count() == 1

    def test_default_manager_includes_deleted(self, user):
        # Used by dumpdata, so backups always have every QSO
        make(user).delete()
        assert QSO._default_manager.count() == 1

    def test_only_removed_with_the_account(self, user):
        qso = make(user)
        user.schedule_deletion(grace_period=timedelta(0))
        assert QSO.all_objects.filter(pk=qso.pk).exists()
        user._hard_delete()
        assert not QSO.all_objects.filter(pk=qso.pk).exists()
        assert not QSORevision.objects.exists()

    def test_deleting_a_user_keeps_qsos_until_purged(self, user):
        make(user)
        user.delete()
        assert QSO.all_objects.count() == 1


class TestProtectedOperations:
    @pytest.mark.parametrize("manager", ["objects", "all_objects"])
    def test_bulk_update(self, user, manager):
        make(user)
        with pytest.raises(ProtectedOperation):
            getattr(QSO, manager).update(call="X1X")
        with pytest.raises(ProtectedOperation):
            getattr(QSO, manager).bulk_update([], ["call"])
        assert QSO.objects.get().call == "K1ABC"

    def test_bulk_create(self, user):
        with pytest.raises(ProtectedOperation):
            QSO.objects.bulk_create([QSO(owner=user, call="X", time_on=T0)])


class TestRelatedDeletes:
    """Removing anything a QSO points at never removes the QSO"""

    def test_location(self, user, location):
        qso = make(user, my_location=location, my_gridsquare="FN54vh")
        location.delete()
        qso = QSO.objects.get(pk=qso.pk)
        assert qso.my_location is None
        assert qso.my_gridsquare == "FN54vh"

    def test_session_with_qsos_cant_go(self, user, contest):
        make(user, session=contest)
        with pytest.raises(ProtectedError):
            contest.delete()

    def test_callsign(self, user, station):
        qso = make(user)
        assert qso.callsign_variant is not None
        Callsign.objects.filter(pk=station.pk).delete()
        qso = QSO.objects.get(pk=qso.pk)
        assert qso.callsign_variant is None
        assert qso.station_callsign == "M0WME"

    def test_import_batch(self, user):
        batch = ImportBatch.objects.create(owner=user, format="adi")
        qso = make(user)
        qso.import_batch = batch
        qso.save()
        batch.delete()
        assert QSO.objects.get(pk=qso.pk).import_batch is None


class TestCallsignVariant:
    def test_held_callsign_is_linked(self, user, station):
        qso = make(user)
        assert qso.callsign_variant.full_call == "M0WME"

    def test_variant_is_made_for_portable(self, user, station):
        qso = make(user, station_callsign="M0WME/P")
        assert qso.callsign_variant.full_call == "M0WME/P"
        assert CallsignVariant.objects.lookup("M0WME/P").exists()

    def test_not_linked_to_someone_elses(self, other_user, station):
        qso = make(other_user)
        assert qso.callsign_variant is None
        assert qso.station_callsign == "M0WME"

    def test_not_linked_before_allocation(self, user, station):
        qso = make(user, time_on=datetime(2019, 1, 1, tzinfo=UTC))
        assert qso.callsign_variant is None

    def test_unknown_callsign(self, user):
        qso = make(user, station_callsign="G4XYZ")
        assert qso.callsign_variant is None

    def test_unparseable_callsign(self, user, station):
        qso = make(user, station_callsign="M0WME//P")
        assert qso.callsign_variant is None


class TestReview:
    def test_clean_qso_is_ok(self, user, station):
        qso = make(user, freq=Decimal("14.074"), mode="FT8")
        assert qso.review_status == ReviewStatus.OK
        assert qso.issues == []

    def test_issues_flag_for_review(self, user, station):
        qso = make(user, mode="FT8")
        assert qso.review_status == ReviewStatus.NEEDS_REVIEW
        assert [issue.code for issue in qso.issue_list] == ["band_freq_missing"]

    def test_reviewing_accepts_the_issues(self, user, station):
        qso = make(user, mode="FT8")
        qso.mark_reviewed(reason="logged from memory")
        assert qso.review_status == ReviewStatus.REVIEWED
        assert qso.reviewed_codes == ["band_freq_missing"]
        assert qso.revisions.last().action == QSOAction.REVIEWED

    def test_a_new_issue_needs_review_again(self, user, station):
        qso = make(user, mode="FT8")
        qso.mark_reviewed()
        qso.gridsquare = "ZZ99"
        qso.save()
        assert qso.review_status == ReviewStatus.NEEDS_REVIEW

    def test_fixing_the_issue_makes_it_ok(self, user, station):
        qso = make(user, mode="FT8")
        qso.freq = Decimal("14.074")
        qso.save()
        assert qso.review_status == ReviewStatus.OK
        assert qso.issues == []

    def test_needs_review_queryset(self, user, station):
        make(user, mode="FT8")
        make(
            user,
            freq=Decimal("14.074"),
            mode="FT8",
            time_on=T0 + timedelta(hours=1),
        )
        assert QSO.objects.needs_review().count() == 1


class TestData:
    def test_to_data_leaves_out_empty_fields(self, user):
        qso = make(user, freq=Decimal("14.07400"))
        data = qso.to_data()
        assert data == {
            "uuid": str(qso.uuid),
            "call": "K1ABC",
            "time_on": "2024-06-01T12:00:00Z",
            "band": "20m",
            "freq": "14.074",
            "station_callsign": "M0WME",
        }

    def test_apply_data_round_trip(self, user):
        qso = make(
            user,
            freq=Decimal("14.074"),
            lat=Decimal("-33.5"),
            dxcc=291,
            time_off=T0 + timedelta(minutes=5),
            extra={"QSLMSG": "TNX"},
        )
        copy = QSO(owner=user)
        copy.apply_data(qso.to_data(include_empty=True))
        assert copy.to_data() | {"uuid": str(qso.uuid)} == qso.to_data()

    def test_apply_data_rejects_bad_values(self):
        with pytest.raises(ValueError, match="freq"):
            QSO().apply_data({"freq": "fourteen"})
        with pytest.raises(ValueError, match="timezone"):
            QSO().apply_data({"time_on": "2024-06-01T12:00:00"})
