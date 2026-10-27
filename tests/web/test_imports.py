"""Importing ADIF, without scripts, with htmx and through the API"""

import pytest
from apps.accounts.models import ApiKey
from apps.logbook.models import QSO, ImportRecordStatus, ReviewStatus
from bs4 import BeautifulSoup
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from tests.web.checks import check_html

pytestmark = pytest.mark.django_db

GOOD = (
    "<CALL:4>W1AW <QSO_DATE:8>20240301 <TIME_ON:4>1405 <BAND:3>20m "
    "<MODE:3>SSB <STATION_CALLSIGN:5>M0WME <EOR>"
)
# No mode, so it has a warning
HELD = (
    "<CALL:5>K1ABC <QSO_DATE:8>20240301 <TIME_ON:4>1410 <BAND:3>20m "
    "<STATION_CALLSIGN:5>M0WME <EOR>"
)
ADIF = f"Made by a test\n<EOH>\n{GOOD}\n{HELD}\n".encode()


class TestImporting:
    def test_text(self, signed_in):
        signed_in.get("/")
        signed_in.click("Import")
        signed_in.attach("ADIF file", "log.adi", ADIF)
        signed_in.press("Import")
        assert signed_in.h1 == "Import of log.adi"
        assert "Records in the file 2" in signed_in
        assert "Held, with warnings 1" in signed_in
        assert QSO.objects.get().call == "W1AW"
        signed_in.press("Log it, marked to check (record 2)")
        assert "Record 2 is logged." in signed_in.messages()
        held = QSO.objects.get(call="K1ABC")
        assert held.review_status == ReviewStatus.NEEDS_REVIEW
        assert "Nothing is waiting to be looked at." in signed_in

    def test_logging_warnings_straight_away(self, signed_in):
        signed_in.get("/imports/")
        signed_in.attach("ADIF file", "log.adi", ADIF)
        signed_in.check("Log QSOs with warnings straight away")
        signed_in.press("Import")
        assert QSO.objects.count() == 2

    def test_importing_again_logs_nothing_twice(self, signed_in, user):
        signed_in.get("/imports/")
        signed_in.attach("ADIF file", "log.adi", ADIF)
        signed_in.check("Log QSOs with warnings straight away")
        signed_in.press("Import")
        signed_in.get("/imports/")
        signed_in.attach("ADIF file", "log.adi", ADIF)
        signed_in.press("Import")
        # Without a uuid in the file they can't be told for sure, so
        # they're held as possible duplicates
        assert "Held, with warnings 2" in signed_in
        assert "already logged" in signed_in
        assert QSO.objects.count() == 2

    def test_too_big(self, signed_in, settings):
        settings.IMPORT_MAX_BYTES = 10
        signed_in.get("/imports/")
        signed_in.attach("ADIF file", "log.adi", ADIF)
        signed_in.press("Import")
        assert signed_in.status == 422
        assert "too big" in signed_in.field_errors("ADIF file")[0]

    def test_listed(self, signed_in):
        signed_in.get("/imports/")
        signed_in.attach("ADIF file", "log.adi", ADIF)
        signed_in.press("Import")
        signed_in.click("Every import")
        row = signed_in.table("Your imports")[0]
        assert (row["File"], row["Logged"], row["To look at"]) == (
            "log.adi",
            "1",
            "1",
        )

    def test_someone_elses(self, signed_in, client, other_user):
        from apps.logbook.importing import import_file

        batch = import_file(other_user, ADIF, filename="theirs.adi")
        signed_in.get(reverse("logbook:import", args=[batch.pk]))
        assert signed_in.status == 404


class TestLoggingAHeldRecordWithHtmx:
    def test_the_record_is_swapped_for_its_result(self, signed_in, htmx):
        signed_in.get("/imports/")
        signed_in.attach("ADIF file", "log.adi", ADIF)
        signed_in.press("Import")
        form = signed_in.button("Log it, marked to check").find_parent("form")
        record = form.find_parent("li")
        assert form["hx-target"] == f"#{record['id']}"
        assert form["hx-swap"] == "outerHTML"
        response = htmx.post(form["hx-post"])
        check_html(response.content)
        swapped = BeautifulSoup(response.content, "html.parser")
        # The focus goes back on the element with the button's id
        button_id = signed_in.button("Log it, marked to check")["id"]
        assert swapped.find(id=button_id).text.startswith("Logged")


class TestImportingWithTheApi:
    def test_api(self, client, user, callsign):
        _, key = ApiKey.issue(user, "test")
        headers = {"Authorization": f"Bearer {key}"}
        response = client.post(
            "/api/v1/imports",
            {"file": SimpleUploadedFile("log.adi", ADIF)},
            headers=headers,
        )
        assert response.status_code == 201
        batch = response.json()
        assert (batch["logged"], batch["held"]) == (1, 1)
        record = batch["waiting"][0]
        assert record["status"] == ImportRecordStatus.HELD
        response = client.post(
            f"/api/v1/imports/{batch['id']}/records/{record['id']}/log",
            headers=headers,
        )
        assert response.json()["status"] == ImportRecordStatus.LOGGED
        listed = client.get("/api/v1/imports", headers=headers).json()
        assert listed["items"][0]["id"] == batch["id"]
        detail = client.get(f"/api/v1/imports/{batch['id']}", headers=headers)
        assert detail.json()["waiting"] == []
