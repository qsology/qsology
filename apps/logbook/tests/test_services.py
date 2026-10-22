"""Logging and changing QSOs through the service layer"""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from apps.logbook import services
from apps.logbook.models import (
    QSO,
    Logbook,
    LogSession,
    LogTemplate,
    QSOAction,
    QSOConflict,
    QSOEvent,
    QSORevision,
    ReviewStatus,
)
from apps.logbook.sanity import Code
from apps.qrz.models import QRZCredential
from django.core.exceptions import ValidationError
from django.db import transaction

from .conftest import T0

pytestmark = pytest.mark.django_db


class TestLogQSO:
    def test_logs_a_clean_qso(self, user, station, qso_values):
        qso = services.log_qso(user, qso_values())
        assert qso.pk is not None
        assert qso.review_status == ReviewStatus.OK
        assert qso.revisions.get().actor == user
        assert qso.callsign_variant.full_call == "M0WME"

    def test_warnings_stop_it_being_logged(self, user, station, qso_values):
        with pytest.raises(services.SanityCheckError) as excinfo:
            services.log_qso(
                user, qso_values(freq=Decimal("7.074"), band="20m")
            )
        assert [issue.code for issue in excinfo.value.issues] == [
            Code.BAND_FREQ_MISMATCH
        ]
        assert "7.074 MHz is on 40m" in str(excinfo.value)
        assert not QSO.all_objects.exists()
        assert not QSORevision.objects.exists()
        assert not QSOEvent.objects.exists()

    def test_accepted_warnings_are_flagged(self, user, station, qso_values):
        qso = services.log_qso(
            user,
            qso_values(freq=Decimal("7.074"), band="20m"),
            accept_warnings=True,
        )
        assert qso.review_status == ReviewStatus.NEEDS_REVIEW
        assert qso.issue_list[0].code == Code.BAND_FREQ_MISMATCH
        assert QSO.objects.needs_review().get() == qso

    def test_errors_cant_be_accepted(self, user, station, qso_values):
        with pytest.raises(ValidationError):
            services.log_qso(user, qso_values(call=""), accept_warnings=True)
        assert not QSO.all_objects.exists()

    def test_unknown_fields_are_refused(self, user, qso_values):
        with pytest.raises(ValidationError, match="owner, uuid"):
            services.log_qso(user, qso_values(uuid="x", owner=None))

    def test_extra_fields(self, user, station, qso_values):
        qso = services.log_qso(
            user, qso_values(extra={"QSLMSG": "TNX", "APP_N1MM_ID": "7"})
        )
        assert qso.extra == {"QSLMSG": "TNX", "APP_N1MM_ID": "7"}


class TestIdempotency:
    def test_same_uuid_returns_the_first(self, user, station, qso_values):
        qso_uuid = uuid.uuid4()
        first = services.log_qso(user, qso_values(), qso_uuid=qso_uuid)
        second = services.log_qso(
            user, qso_values(call="K2XYZ"), qso_uuid=str(qso_uuid)
        )
        assert second.pk == first.pk
        assert second.call == "K1ABC"
        assert QSO.all_objects.count() == 1
        assert QSORevision.objects.count() == 1

    def test_retry_of_a_deleted_qso_doesnt_bring_it_back(
        self, user, station, qso_values
    ):
        qso_uuid = uuid.uuid4()
        services.log_qso(user, qso_values(), qso_uuid=qso_uuid).delete()
        again = services.log_qso(user, qso_values(), qso_uuid=qso_uuid)
        assert again.is_deleted
        assert QSO.all_objects.count() == 1

    def test_someone_elses_uuid(self, user, other_user, station, qso_values):
        qso_uuid = uuid.uuid4()
        services.log_qso(user, qso_values(), qso_uuid=qso_uuid)
        with pytest.raises(ValidationError, match="already in use"):
            services.log_qso(
                other_user,
                qso_values(),
                qso_uuid=qso_uuid,
                accept_warnings=True,
            )

    def test_bad_uuid(self, user, qso_values):
        with pytest.raises(ValueError):
            services.log_qso(user, qso_values(), qso_uuid="not-a-uuid")


class TestLocation:
    def test_location_details_are_copied(
        self, user, station, qso_values, location
    ):
        qso = services.log_qso(
            user, qso_values(), location=location, accept_warnings=True
        )
        assert qso.my_location == location
        assert qso.my_gridsquare == "FN54vh"
        assert qso.my_pota_ref == "US-0001"
        assert qso.my_lat == Decimal("44.31")

    def test_given_values_win(self, user, station, qso_values, location):
        qso = services.log_qso(
            user,
            qso_values(my_gridsquare="FN54vg"),
            location=location,
            accept_warnings=True,
        )
        assert qso.my_gridsquare == "FN54vg"

    def test_changing_the_location_later_changes_nothing(
        self, user, station, qso_values, location
    ):
        qso = services.log_qso(
            user, qso_values(), location=location, accept_warnings=True
        )
        location.gridsquare = "AA00aa"
        location.save()
        location.references.clear()
        qso.refresh_from_db()
        assert qso.my_gridsquare == "FN54vh"
        assert qso.my_pota_ref == "US-0001"

    def test_logbook_default_location(
        self, user, station, qso_values, location
    ):
        logbook = Logbook.objects.create(
            owner=user, name="Acadia", default_location=location
        )
        qso = services.log_qso(
            user, qso_values(), logbook=logbook, accept_warnings=True
        )
        assert qso.my_location == location


class TestSessions:
    def test_serials_count_up(self, user, station, qso_values, contest):
        serials = [
            services.log_qso(
                user,
                qso_values(time_on=T0 + timedelta(minutes=n), call=f"K{n}AA"),
                session=contest,
            ).stx
            for n in range(1, 4)
        ]
        assert serials == [1, 2, 3]
        assert contest.peek_serial() == 4

    def test_session_details_are_copied(
        self, user, station, qso_values, contest
    ):
        qso = services.log_qso(user, qso_values(), session=contest)
        assert qso.session == contest
        assert qso.contest_id == "CQ-WW-CW"
        assert qso.stx_string == "14"

    def test_serial_start(self, user, station, qso_values):
        session = LogSession.objects.create(
            owner=user, name="Late start", uses_serials=True, serial_start=100
        )
        qso = services.log_qso(user, qso_values(), session=session)
        assert qso.stx == 100

    def test_no_serials_unless_asked(self, user, station, qso_values):
        session = LogSession.objects.create(owner=user, name="Activation")
        assert services.log_qso(user, qso_values(), session=session).stx is None

    def test_failed_qso_hands_its_serial_back(
        self, user, station, qso_values, contest
    ):
        with pytest.raises(services.SanityCheckError):
            services.log_qso(user, qso_values(mode="BANANA"), session=contest)
        with pytest.raises(ValidationError):
            services.log_qso(user, qso_values(call=""), session=contest)
        qso = services.log_qso(user, qso_values(), session=contest)
        assert qso.stx == 1

    def test_serials_of_deleted_qsos_arent_reused(
        self, user, station, qso_values, contest
    ):
        services.log_qso(user, qso_values(), session=contest).delete()
        qso = services.log_qso(user, qso_values(call="K2XYZ"), session=contest)
        assert qso.stx == 2

    def test_given_serial_is_used(self, user, station, qso_values, contest):
        qso = services.log_qso(user, qso_values(stx=57), session=contest)
        assert qso.stx == 57
        assert contest.peek_serial() == 1

    def test_serial_cant_be_used_twice(
        self, user, station, qso_values, contest
    ):
        services.log_qso(user, qso_values(stx=1), session=contest)
        with pytest.raises(ValidationError, match="Serial already used"):
            services.log_qso(
                user,
                qso_values(stx=1, call="K2XYZ"),
                session=contest,
                accept_warnings=True,
            )

    def test_allocate_serial_rolls_back(self, contest):
        with pytest.raises(RuntimeError), transaction.atomic():
            assert contest.allocate_serial() == 1
            raise RuntimeError
        assert contest.peek_serial() == 1

    def test_ended_session(self, user, station, qso_values, contest):
        contest.end(T0)
        with pytest.raises(services.SanityCheckError) as excinfo:
            services.log_qso(
                user,
                qso_values(time_on=T0 + timedelta(minutes=1)),
                session=contest,
            )
        assert excinfo.value.issues[0].code == Code.SESSION_ENDED
        assert contest.peek_serial() == 1


class TestUpdate:
    def test_update(self, qso, other_user):
        services.update_qso(
            qso, {"rst_rcvd": "-05"}, actor=other_user, reason="fix"
        )
        qso.refresh_from_db()
        assert qso.rst_rcvd == "-05"
        revision = qso.revisions.last()
        assert (revision.action, revision.actor, revision.reason) == (
            QSOAction.UPDATED,
            other_user,
            "fix",
        )

    def test_update_with_warnings(self, qso):
        with pytest.raises(services.SanityCheckError):
            services.update_qso(qso, {"band": "40m"})
        qso.refresh_from_db()
        assert qso.band == "20m"
        assert qso.version == 1

    def test_accept_warnings_on_update(self, qso):
        services.update_qso(qso, {"band": "40m"}, accept_warnings=True)
        assert qso.review_status == ReviewStatus.NEEDS_REVIEW

    def test_reviewed_issues_dont_block_later_edits(self, qso):
        services.update_qso(qso, {"band": "40m"}, accept_warnings=True)
        qso.mark_reviewed()
        services.update_qso(qso, {"comment": "worked on the long path"})
        assert qso.review_status == ReviewStatus.REVIEWED

    def test_expected_version(self, qso):
        with pytest.raises(QSOConflict):
            services.update_qso(qso, {"comment": "x"}, expected_version=7)
        services.update_qso(qso, {"comment": "x"}, expected_version=1)
        assert qso.version == 2

    def test_stale_instance(self, qso):
        stale = QSO.objects.get(pk=qso.pk)
        services.update_qso(qso, {"comment": "first"})
        with pytest.raises(QSOConflict):
            services.update_qso(stale, {"comment": "second"})
        qso.refresh_from_db()
        assert qso.comment == "first"

    def test_deleted_qsos_must_be_restored_first(self, qso):
        qso.delete()
        with pytest.raises(ValidationError, match="Restore"):
            services.update_qso(qso, {"comment": "x"})


class TestRevert:
    def test_revert(self, qso):
        services.update_qso(qso, {"call": "K9WRONG", "comment": "oops"})
        services.revert_qso(qso, qso.revisions.first())
        qso.refresh_from_db()
        assert qso.call == "K1ABC"
        assert qso.comment == ""
        assert qso.version == 3
        assert qso.revisions.last().action == QSOAction.REVERTED

    def test_revert_can_be_undone(self, qso):
        services.update_qso(qso, {"comment": "keep me"})
        services.revert_qso(qso, qso.revisions.first())
        services.revert_qso(qso, qso.revisions.get(version=2))
        assert qso.comment == "keep me"

    def test_revert_keeps_extra(self, qso):
        services.update_qso(qso, {"extra": {"QSLMSG": "TNX"}})
        services.update_qso(qso, {"extra": {}})
        services.revert_qso(qso, qso.revisions.get(version=2))
        assert qso.extra == {"QSLMSG": "TNX"}

    def test_revert_doesnt_undelete(self, qso):
        qso.delete()
        services.revert_qso(qso, qso.revisions.first())
        assert qso.is_deleted

    def test_revision_from_another_qso(self, qso, log):
        other = log()
        with pytest.raises(ValidationError):
            services.revert_qso(qso, other.revisions.first())


QRZ_RESPONSE = b"""<?xml version="1.0" encoding="utf-8" ?>
<QRZDatabase version="1.34" xmlns="http://xmldata.qrz.com">
  <Callsign>
    <call>K1ABC</call><fname>Fred</fname><name>Smith</name>
    <addr2>Boston</addr2><state>MA</state><country>United States</country>
    <grid>FN42aa</grid><dxcc>291</dxcc><cqzone>5</cqzone><ituzone>8</ituzone>
    <lat>42.0</lat><lon>-71.5</lon>
  </Callsign>
  <Session><Key>abc</Key></Session>
</QRZDatabase>"""


class TestPrefill:
    @pytest.fixture
    def template(self, user):
        return LogTemplate.objects.create(
            owner=user,
            name="Contest",
            layout=[{"field": "call", "width": 10}],
            defaults={"mode": "CW", "tx_pwr": "100", "rst_sent": "599"},
        )

    def test_template_defaults(self, user, template):
        prefill = services.prefill(user, template=template)
        assert prefill.values == {
            "mode": "CW",
            "tx_pwr": "100",
            "rst_sent": "599",
        }
        assert set(prefill.sources.values()) == {"template"}
        assert prefill.layout == [{"field": "call", "width": 10}]

    def test_logbook_template(self, user, template):
        logbook = Logbook.objects.create(
            owner=user, name="CW", template=template
        )
        assert services.prefill(user, logbook=logbook).values["mode"] == "CW"

    def test_location_and_session(self, user, template, location, contest):
        contest.my_location = location
        contest.save()
        prefill = services.prefill(user, template=template, session=contest)
        assert prefill.values["my_gridsquare"] == "FN54vh"
        assert prefill.values["stx"] == 1
        assert prefill.values["contest_id"] == "CQ-WW-CW"
        assert prefill.sources["my_gridsquare"] == "location"
        assert prefill.sources["stx"] == "session"

    def test_peeking_takes_no_serial(self, user, contest):
        services.prefill(user, session=contest)
        services.prefill(user, session=contest)
        assert contest.peek_serial() == 1

    def test_qrz(self, user, template):
        template.qrz_lookup = True
        template.qrz_fields = ["name", "gridsquare", "lat"]
        template.save()
        QRZCredential.objects.create(user=user, username="u", password="p")

        prefill = services.prefill(
            user,
            call="K1ABC",
            template=template,
            fetch=lambda *args, **kwargs: QRZ_RESPONSE,
        )
        assert prefill.values["name"] == "Fred Smith"
        assert prefill.values["gridsquare"] == "FN42aa"
        assert prefill.values["lat"] == "42"
        assert "dxcc" not in prefill.values
        assert prefill.sources["name"] == "qrz"

    def test_qrz_all_fields_when_none_picked(self, user, template):
        template.qrz_lookup = True
        template.save()
        QRZCredential.objects.create(user=user, username="u", password="p")
        prefill = services.prefill(
            user,
            call="K1ABC",
            template=template,
            fetch=lambda *args, **kwargs: QRZ_RESPONSE,
        )
        assert prefill.values["dxcc"] == 291
        assert prefill.values["cqz"] == 5

    def test_qrz_failure_still_prefills(self, user, template):
        template.qrz_lookup = True
        template.save()
        prefill = services.prefill(user, call="K1ABC", template=template)
        assert prefill.values["mode"] == "CW"
        assert "QRZ lookup failed" in prefill.messages[0]

    def test_no_lookup_without_a_call(self, user, template):
        template.qrz_lookup = True
        template.save()

        def fetch(*args, **kwargs):
            raise AssertionError("QRZ shouldn't be asked")

        assert (
            services.prefill(user, template=template, fetch=fetch).messages
            == []
        )
