"""Zones filled in from grid squares"""

import pytest
from apps.logbook import importing, services

pytestmark = pytest.mark.django_db


class TestLogging:
    def test_their_zones_from_their_grid(self, log):
        qso = log(gridsquare="FN54vh")
        assert (qso.cqz, qso.ituz) == (5, 8)

    def test_my_zones_from_my_grid(self, log):
        qso = log(my_gridsquare="IO91wm")
        assert (qso.my_cq_zone, qso.my_itu_zone) == (14, 27)

    def test_grid_is_tidied_first(self, log):
        qso = log(gridsquare=" fn54VH ")
        assert (qso.cqz, qso.ituz) == (5, 8)

    def test_given_zones_win(self, log):
        qso = log(gridsquare="FN54vh", cqz=4)
        assert (qso.cqz, qso.ituz) == (4, 8)

    def test_no_grid(self, log):
        qso = log()
        assert (qso.cqz, qso.ituz) == (None, None)
        assert (qso.my_cq_zone, qso.my_itu_zone) == (None, None)

    def test_square_across_a_border(self, log):
        qso = log(gridsquare="JN46")
        assert (qso.cqz, qso.ituz) == (None, 28)

    def test_bad_grid_is_still_refused(self, log):
        with pytest.raises(Exception, match="valid grid square"):
            log(gridsquare="ZZ99")

    def test_location_zones(self, user, station, qso_values, location):
        qso = services.log_qso(
            user, qso_values(), location=location, accept_warnings=True
        )
        assert (qso.my_cq_zone, qso.my_itu_zone) == (5, 8)

    def test_zones_are_in_the_revision(self, log):
        qso = log(gridsquare="FN54vh")
        snapshot = qso.revisions.get().data
        assert (snapshot["cqz"], snapshot["ituz"]) == (5, 8)


class TestUpdating:
    def test_grid_added_later(self, log):
        qso = services.update_qso(log(), {"gridsquare": "FN54vh"})
        qso.refresh_from_db()
        assert (qso.cqz, qso.ituz) == (5, 8)

    def test_zones_already_there_are_kept(self, log):
        qso = log(gridsquare="FN54vh")
        services.update_qso(qso, {"gridsquare": "IO91wm"})
        qso.refresh_from_db()
        assert (qso.cqz, qso.ituz) == (5, 8)


class TestImporting:
    def test_imported_qsos_are_as_they_were(self, user, station):
        batch = importing.import_file(
            user,
            "<CALL:5>K1ABC <QSO_DATE:8>20240601 <TIME_ON:4>1200 "
            "<FREQ:6>14.074 <MODE:3>FT8 <STATION_CALLSIGN:5>M0WME "
            "<GRIDSQUARE:6>FN54vh <EOR>\n",
            filename="a.adi",
        )
        qso = batch.qsos.get()
        assert qso.gridsquare == "FN54vh"
        assert (qso.cqz, qso.ituz) == (None, None)
