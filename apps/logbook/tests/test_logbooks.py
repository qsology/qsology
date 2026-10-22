"""Logbooks, templates and sessions"""

from datetime import timedelta
from decimal import Decimal

import pytest
from apps.locations.models import Location
from apps.logbook import query
from apps.logbook.models import (
    QSO,
    Logbook,
    LogSession,
    LogTemplate,
    ProtectedOperation,
)
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .conftest import T0

pytestmark = pytest.mark.django_db


class TestMasterLogbook:
    def test_every_user_has_one(self, user):
        master = Logbook.objects.master_for(user)
        assert master.is_master
        assert master.filter == query.MATCH_ALL
        assert Logbook.objects.master_for(user) == master

    def test_holds_every_qso(self, user, log):
        log(mode="CW", rst_sent="599", rst_rcvd="599")
        log(freq=Decimal("7.074"))
        assert Logbook.objects.master_for(user).qsos().count() == 2

    def test_only_one(self, user):
        Logbook.objects.master_for(user)
        with pytest.raises(IntegrityError), transaction.atomic():
            Logbook.objects.bulk_create(
                [Logbook(owner=user, name="Second", is_master=True)]
            )

    def test_cant_be_filtered(self, user):
        master = Logbook.objects.master_for(user)
        master.set_query("band:20m")
        with pytest.raises(ValidationError, match="every QSO"):
            master.save()

    def test_cant_be_deleted(self, user):
        master = Logbook.objects.master_for(user)
        with pytest.raises(ProtectedOperation):
            master.delete()


class TestLogbooks:
    def test_filtering(self, user, log):
        cw = log(mode="CW", rst_sent="599", rst_rcvd="599")
        log(mode="FT8")
        logbook = Logbook(owner=user, name="CW")
        logbook.set_query("mode:CW")
        logbook.save()
        assert list(logbook.qsos()) == [cw]

    def test_filter_from_json(self, user, log):
        forty = log(freq=Decimal("7.074"))
        log()
        logbook = Logbook.objects.create(
            owner=user,
            name="40m",
            filter={"field": "band", "op": "eq", "value": "40m"},
        )
        assert logbook.query == "band:40m"
        assert list(logbook.qsos()) == [forty]

    def test_query_is_kept_tidy(self, user):
        logbook = Logbook(owner=user, name="Mixed")
        logbook.set_query("  MODE:cw   AND callsign:K1* ")
        logbook.save()
        assert logbook.query == "mode:cw call:K1*"

    def test_bad_query(self, user):
        logbook = Logbook(owner=user, name="Bad")
        with pytest.raises(ValidationError, match="Unknown field"):
            logbook.set_query("colour:blue")

    def test_bad_filter(self, user):
        with pytest.raises(ValidationError, match="filter"):
            Logbook.objects.create(
                owner=user,
                name="Bad",
                filter={"field": "owner__password", "op": "eq", "value": "x"},
            )

    def test_names_are_unique_per_user(self, user, other_user):
        Logbook.objects.create(owner=user, name="POTA")
        Logbook.objects.create(owner=other_user, name="POTA")
        with pytest.raises(ValidationError):
            Logbook.objects.create(owner=user, name="POTA")

    def test_only_shows_the_owners_qsos(self, user, other_user, log):
        log()
        log(owner=other_user, accept_warnings=True)
        logbook = Logbook.objects.create(owner=other_user, name="All")
        assert logbook.qsos().count() == 1
        assert logbook.qsos().get().owner == other_user

    def test_deleted_qsos(self, user, log):
        log().delete()
        logbook = Logbook.objects.create(owner=user, name="All")
        assert logbook.qsos().count() == 0
        assert logbook.qsos(include_deleted=True).count() == 1

    def test_deleting_a_logbook_keeps_its_qsos(self, user, log):
        qso = log()
        logbook = Logbook.objects.create(owner=user, name="All")
        logbook.delete()
        assert QSO.objects.get() == qso
        assert not qso.revisions.filter(action="deleted").exists()

    def test_matches(self, user, log):
        logbook = Logbook(owner=user, name="20m")
        logbook.set_query("band:20m")
        logbook.save()
        assert logbook.matches(log())
        assert not logbook.matches(log(freq=Decimal("7.074")))

    def test_by_location(self, user, station, location, qso_values):
        from apps.logbook import services

        at_park = services.log_qso(
            user, qso_values(), location=location, accept_warnings=True
        )
        services.log_qso(
            user,
            qso_values(time_on=T0 + timedelta(hours=1)),
            accept_warnings=True,
        )
        by_location = Logbook(owner=user, name="Acadia")
        by_location.set_query(f"location:{location.pk}")
        by_location.save()
        by_park = Logbook(owner=user, name="US-0001")
        by_park.set_query("my_pota:US-0001")
        by_park.save()
        assert list(by_location.qsos()) == [at_park]
        assert list(by_park.qsos()) == [at_park]

    def test_someone_elses_template_or_location(self, user, other_user):
        template = LogTemplate.objects.create(
            owner=other_user, name="T", layout=[{"field": "call", "width": 8}]
        )
        location = Location.objects.create(owner=other_user, name="L")
        with pytest.raises(ValidationError, match="someone else"):
            Logbook.objects.create(owner=user, name="A", template=template)
        with pytest.raises(ValidationError, match="someone else"):
            Logbook.objects.create(
                owner=user, name="B", default_location=location
            )


class TestTemplates:
    def make(self, user, **kwargs):
        kwargs.setdefault("layout", [{"field": "call", "width": 10}])
        return LogTemplate.objects.create(owner=user, name="T", **kwargs)

    def test_layout_keeps_its_order(self, user):
        layout = [
            {"field": "time_on", "width": 16},
            {"field": "call", "width": 10, "label": "Callsign"},
            {"field": "rst_sent", "width": 4},
        ]
        template = self.make(user, layout=layout)
        template.refresh_from_db()
        assert template.layout == layout
        assert template.field_names == ["time_on", "call", "rst_sent"]

    @pytest.mark.parametrize(
        "layout",
        [
            {"call": 10},
            [{"field": "call"}],
            [{"field": "call", "width": 0}],
            [{"field": "call", "width": 101}],
            [{"field": "call", "width": "10"}],
            [{"field": "call", "width": True}],
            [{"field": "call", "width": 10, "colour": "red"}],
            [{"field": "call", "width": 10, "label": 5}],
            [{"field": "colour", "width": 10}],
            [{"field": "call", "width": 10}, {"field": "call", "width": 5}],
            [{"field": "mode", "width": 10}],
        ],
    )
    def test_bad_layouts(self, user, layout):
        with pytest.raises(ValidationError) as excinfo:
            self.make(user, layout=layout)
        assert "layout" in excinfo.value.message_dict

    def test_defaults(self, user):
        template = self.make(
            user, defaults={"mode": "SSB", "tx_pwr": 5, "my_dxcc": 223}
        )
        assert template.defaults["tx_pwr"] == 5

    @pytest.mark.parametrize(
        "defaults",
        [
            ["mode"],
            {"colour": "red"},
            {"call": "K1ABC"},
            {"time_on": "2024-01-01T00:00:00Z"},
            {"stx": 1},
            {"tx_pwr": "lots"},
            {"my_dxcc": "England"},
        ],
    )
    def test_bad_defaults(self, user, defaults):
        with pytest.raises(ValidationError) as excinfo:
            self.make(user, defaults=defaults)
        assert "defaults" in excinfo.value.message_dict

    @pytest.mark.parametrize(
        "qrz_fields", ["name", ["name", "name"], ["password"], ["call"]]
    )
    def test_bad_qrz_fields(self, user, qrz_fields):
        with pytest.raises(ValidationError) as excinfo:
            self.make(user, qrz_fields=qrz_fields)
        assert "qrz_fields" in excinfo.value.message_dict

    def test_deleting_a_template_keeps_the_logbook(self, user):
        template = self.make(user)
        logbook = Logbook.objects.create(
            owner=user, name="A", template=template
        )
        template.delete()
        logbook.refresh_from_db()
        assert logbook.template is None


class TestSessions:
    def test_next_serial_starts_at_serial_start(self, user):
        session = LogSession.objects.create(
            owner=user, name="S", serial_start=50
        )
        assert session.next_serial == 50
        assert session.allocate_serial() == 50
        assert session.allocate_serial() == 51
        assert session.peek_serial() == 52

    def test_must_end_after_it_starts(self, user):
        with pytest.raises(ValidationError):
            LogSession.objects.create(
                owner=user, name="S", started_at=T0, ended_at=T0 - timedelta(1)
            )

    def test_is_active(self, user, contest):
        assert contest.is_active
        contest.end()
        assert not contest.is_active

    def test_someone_elses_location(self, user, other_user):
        location = Location.objects.create(owner=other_user, name="L")
        with pytest.raises(ValidationError, match="someone else"):
            LogSession.objects.create(
                owner=user, name="S", my_location=location
            )

    def test_deleting_an_empty_session(self, contest):
        contest.delete()
        assert not LogSession.objects.exists()

    def test_qso_defaults(self, contest):
        assert contest.qso_defaults() == {
            "contest_id": "CQ-WW-CW",
            "station_callsign": "M0WME",
            "stx_string": "14",
        }
