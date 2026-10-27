"""Exports, without scripts, as htmx keeps an export's row up to date, and
through the API. Celery runs tasks straight away in tests, so an export is
ready as soon as it's asked for, unless the test stops it"""

import pytest
from apps.accounts.models import ApiKey
from apps.logbook.models import Export, ExportStatus
from bs4 import BeautifulSoup
from django.urls import reverse

from tests.web.checks import check_html
from tests.web.test_log import logged

pytestmark = pytest.mark.django_db

EXPORTS = "/exports/"


class TestExporting:
    def test_text(self, signed_in, user, django_capture_on_commit_callbacks):
        logged(user, "W1AW")
        signed_in.get("/")
        signed_in.click("Export")
        signed_in.choose("ADIF (.adi)", "adi")
        with django_capture_on_commit_callbacks(execute=True):
            signed_in.press("Make export", follow=False)
        signed_in.get(EXPORTS)
        assert "is being made" in " ".join(signed_in.messages())
        row = signed_in.table("Your exports")[0]
        assert row["What"] == "Master log, ADIF (.adi)"
        assert row["QSOs"] == "1"
        signed_in.click("Download Master log, ADIF (.adi)")
        assert signed_in.status == 200
        content = b"".join(signed_in.response.streaming_content)
        assert b"<CALL:4>W1AW" in content

    def test_api(self, client, user, django_capture_on_commit_callbacks):
        logged(user, "W1AW")
        _, key = ApiKey.issue(user, "test")
        headers = {"Authorization": f"Bearer {key}"}
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(
                "/api/v1/exports",
                {"format": "json"},
                content_type="application/json",
                headers=headers,
            )
        assert response.status_code == 202
        export = client.get(
            f"/api/v1/exports/{response.json()['id']}", headers=headers
        ).json()
        assert export["downloadable"] is True
        listed = client.get("/api/v1/exports", headers=headers).json()
        assert listed["items"][0]["id"] == export["id"]
        response = client.get(
            f"/api/v1/exports/{export['id']}/download", headers=headers
        )
        assert response.status_code == 200
        assert b'"call":"W1AW"' in b"".join(response.streaming_content)

    def test_api_bad_format(self, client, user):
        _, key = ApiKey.issue(user, "test")
        response = client.post(
            "/api/v1/exports",
            {"format": "xls"},
            content_type="application/json",
            headers={"Authorization": f"Bearer {key}"},
        )
        assert response.status_code == 422


@pytest.fixture
def waiting(user):
    return Export.objects.create(owner=user, name="Master log", format="adi")


class TestWhileItsBeingMade:
    def test_text_can_check_again(self, signed_in, waiting):
        signed_in.get(EXPORTS)
        assert signed_in.table("Your exports")[0]["Status"].startswith(
            "Waiting"
        )
        signed_in.click("Check again")
        assert signed_in.path == EXPORTS

    def test_the_row_asks_for_itself(self, signed_in, waiting):
        signed_in.get(EXPORTS)
        row = signed_in.soup.find("tr", id=f"export-{waiting.pk}")
        assert row["hx-get"] == reverse("logbook:export", args=[waiting.pk])
        assert row["hx-trigger"] == "every 10s"
        assert row["hx-swap"] == "outerHTML"
        # Nothing in it can have the focus, so swapping it can't move it
        assert row.find(["button", "input", "select", "textarea"]) is None

    def test_htmx_gets_the_row(self, signed_in, htmx, waiting):
        response = htmx.get(reverse("logbook:export", args=[waiting.pk]))
        check_html(response.content)
        assert b"hx-get" in response.content
        waiting.status = ExportStatus.FAILED
        waiting.save()
        response = htmx.get(reverse("logbook:export", args=[waiting.pk]))
        row = BeautifulSoup(response.content, "html.parser").find("tr")
        # Done, so it stops asking
        assert not row.has_attr("hx-get")
        assert "couldn't be made" in row.text

    def test_without_htmx_its_a_page(self, signed_in, waiting):
        signed_in.get(reverse("logbook:export", args=[waiting.pk]))
        assert signed_in.h1 == "Master log, ADIF (.adi)"

    def test_someone_elses(self, signed_in, other_user):
        theirs = Export.objects.create(owner=other_user, name="x", format="adi")
        signed_in.get(reverse("logbook:export", args=[theirs.pk]))
        assert signed_in.status == 404
