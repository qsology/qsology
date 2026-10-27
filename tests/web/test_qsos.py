"""Finding, changing, deleting and bringing back QSOs, without scripts and
through the API"""

from datetime import UTC, datetime, timedelta

import pytest
from apps.accounts.models import ApiKey
from apps.logbook.models import QSO, ReviewStatus
from apps.logbook.services import update_qso
from django.urls import reverse

from tests.web.test_log import logged

pytestmark = pytest.mark.django_db

LIST = "/log/qsos/"


def page_of(qso):
    return reverse("logbook:qso", args=[qso.uuid])


@pytest.fixture
def api(client, user, callsign):
    _, key = ApiKey.issue(user, "test")

    class Api:
        def __init__(self):
            self.headers = {"Authorization": f"Bearer {key}"}

        def get(self, url, data=None):
            return client.get(url, data, headers=self.headers)

        def send(self, method, url, payload=None):
            return getattr(client, method)(
                url,
                payload or {},
                content_type="application/json",
                headers=self.headers,
            )

    return Api()


class TestFinding:
    def test_text(self, signed_in, user):
        logged(user, "W1AW")
        logged(user, "K1ABC")
        signed_in.get("/")
        signed_in.click("QSOs")
        assert {row["Call"] for row in signed_in.table("QSOs")} == {
            "W1AW",
            "K1ABC",
        }
        signed_in.fill("Callsign", "w1")
        signed_in.press("Search")
        assert [row["Call"] for row in signed_in.table("QSOs")] == ["W1AW"]
        signed_in.click("Show every QSO")
        assert len(signed_in.table("QSOs")) == 2

    def test_pages(self, signed_in, user):
        start = datetime(2024, 1, 1, tzinfo=UTC)
        for minute in range(55):
            logged(
                user, f"W{minute}AW", time_on=start + timedelta(minutes=minute)
            )
        signed_in.get(LIST)
        assert len(signed_in.table("QSOs")) == 50
        assert "page 1 of 2" in signed_in
        signed_in.click("Older")
        assert len(signed_in.table("QSOs")) == 5
        signed_in.click("Newer")
        assert signed_in.table("QSOs")[0]["Call"] == "W54AW"

    def test_filter_in_the_advanced_view(self, signed_in, user):
        user.ui_mode = "advanced"
        user.save()
        logged(user, "W1AW", band="20m")
        logged(user, "K1ABC", band="40m")
        signed_in.get(LIST)
        signed_in.fill("Filter", "band:40m")
        signed_in.press("Search")
        assert [row["Call"] for row in signed_in.table("QSOs")] == ["K1ABC"]

    def test_a_filter_that_cant_be_read(self, signed_in, user):
        user.ui_mode = "advanced"
        user.save()
        signed_in.get(LIST)
        signed_in.fill("Filter", "band:(")
        signed_in.press("Search")
        assert signed_in.field_errors("Filter")

    def test_only_your_own(self, signed_in, other_user):
        logged(other_user, "W1AW")
        signed_in.get(LIST)
        assert "0 QSOs" in signed_in

    def test_api(self, api, user):
        logged(user, "W1AW", band="20m")
        logged(user, "K1ABC", band="40m")
        response = api.get("/api/v1/qsos", {"filter": "band:40m"})
        data = response.json()
        assert data["count"] == 1
        assert data["items"][0]["qso"]["call"] == "K1ABC"
        assert api.get("/api/v1/qsos", {"call": "w1"}).json()["count"] == 1
        assert api.get("/api/v1/qsos", {"filter": "band:("}).status_code == 422


class TestChanging:
    def test_text(self, signed_in, user):
        qso = logged(user, "W1AW", name="Hiram")
        signed_in.get(LIST)
        signed_in.click("W1AW")
        assert signed_in.value("Name") == "Hiram"
        signed_in.fill("Name", "Hiram Percy")
        signed_in.fill("Comment", "")
        signed_in.press("Save changes")
        assert "W1AW has been saved." in signed_in.messages()
        qso.refresh_from_db()
        assert qso.name == "Hiram Percy"
        assert qso.version == 2

    def test_emptying_a_field(self, signed_in, user):
        qso = logged(user, "W1AW", name="Hiram")
        signed_in.get(page_of(qso))
        signed_in.fill("Name", "")
        signed_in.press("Save changes")
        qso.refresh_from_db()
        assert qso.name == ""

    def test_fields_with_values_are_always_shown(self, signed_in, user):
        """Even in the simple view, so nothing's hidden or lost"""
        qso = logged(user, "W1AW", sota_ref="G/LD-001")
        signed_in.get(page_of(qso))
        assert signed_in.value("SOTA") == "G/LD-001"

    def test_fields_not_shown_are_left_alone(self, signed_in, user):
        qso = logged(user, "W1AW", my_rig="IC-705")
        signed_in.get(page_of(qso))
        signed_in.fill("Name", "Hiram")
        signed_in.press("Save changes")
        qso.refresh_from_db()
        assert (qso.name, qso.my_rig) == ("Hiram", "IC-705")

    def test_changed_elsewhere_meanwhile(self, signed_in, user):
        qso = logged(user, "W1AW")
        signed_in.get(page_of(qso))
        signed_in.fill("Name", "Mine")
        update_qso(QSO.objects.get(), {"name": "Theirs"})
        signed_in.press("Save changes")
        assert signed_in.status == 422
        assert "changed somewhere else" in signed_in
        # Ours is still in the form, and saving again puts it in
        assert signed_in.value("Name") == "Mine"
        signed_in.press("Save changes")
        qso.refresh_from_db()
        assert qso.name == "Mine"

    def test_warnings(self, signed_in, user):
        qso = logged(user, "W1AW")
        signed_in.get(page_of(qso))
        signed_in.fill("Mode", "NOTAMODE")
        signed_in.press("Save changes")
        assert "NOTAMODE isn't a known mode" in signed_in
        signed_in.check("Log it anyway, I've checked these")
        signed_in.press("Save changes")
        qso.refresh_from_db()
        assert qso.mode == "NOTAMODE"

    def test_history_in_the_advanced_view(self, signed_in, user):
        user.ui_mode = "advanced"
        user.save()
        qso = logged(user, "W1AW")
        update_qso(qso, {"name": "Hiram"}, reason="Fixed the name")
        signed_in.get(page_of(qso))
        reasons = [row["Why"] for row in signed_in.table("Every change")]
        assert reasons[0] == "Fixed the name"

    def test_someone_elses(self, signed_in, other_user):
        qso = logged(other_user, "W1AW")
        signed_in.get(page_of(qso))
        assert signed_in.status == 404

    def test_api(self, api, user):
        qso = logged(user, "W1AW")
        response = api.send(
            "patch",
            f"/api/v1/qsos/{qso.uuid}",
            {"changes": {"name": "Hiram"}, "version": qso.version},
        )
        assert response.status_code == 200
        assert response.json()["qso"]["name"] == "Hiram"
        stale = api.send(
            "patch",
            f"/api/v1/qsos/{qso.uuid}",
            {"changes": {"name": "Old"}, "version": qso.version},
        )
        assert stale.status_code == 409
        revisions = api.get(f"/api/v1/qsos/{qso.uuid}/revisions").json()
        assert [r["version"] for r in revisions] == [2, 1]


class TestDeleting:
    def test_text(self, signed_in, user):
        qso = logged(user, "W1AW")
        signed_in.get(page_of(qso))
        signed_in.click("Delete this QSO")
        assert signed_in.h1.startswith("Delete W1AW")
        signed_in.press("Delete it")
        assert "can be brought back" in " ".join(signed_in.messages())
        assert not QSO.objects.exists()
        signed_in.get(LIST)
        assert "0 QSOs" in signed_in

    def test_keeping_it(self, signed_in, user):
        qso = logged(user, "W1AW")
        signed_in.get(reverse("logbook:qso-delete", args=[qso.uuid]))
        signed_in.click("Keep it")
        assert QSO.objects.exists()

    def test_bringing_it_back(self, signed_in, user):
        qso = logged(user, "W1AW")
        qso.delete()
        signed_in.get(page_of(qso))
        assert "This QSO was deleted" in signed_in
        signed_in.press("Bring it back")
        assert QSO.objects.filter(pk=qso.pk).exists()

    def test_deleted_ones_can_be_found(self, signed_in, user):
        user.ui_mode = "advanced"
        user.save()
        logged(user, "W1AW").delete()
        signed_in.get(LIST)
        signed_in.check("Show deleted QSOs")
        signed_in.press("Search")
        assert signed_in.table("QSOs")[0]["Status"] == "Deleted"

    def test_api(self, api, user):
        qso = logged(user, "W1AW")
        response = api.send("delete", f"/api/v1/qsos/{qso.uuid}")
        assert response.json()["deleted"] is True
        assert api.get("/api/v1/qsos").json()["count"] == 0
        response = api.send("post", f"/api/v1/qsos/{qso.uuid}/restore")
        assert response.json()["deleted"] is False


class TestChecking:
    def test_text(self, signed_in, user):
        qso = logged(user, "W1AW", mode="NOTAMODE")
        assert qso.review_status == ReviewStatus.NEEDS_REVIEW
        signed_in.get("/")
        signed_in.click("1 to check")
        signed_in.click("W1AW")
        assert "NOTAMODE isn't a known mode" in signed_in
        signed_in.press("It's right, I've checked it")
        qso.refresh_from_db()
        assert qso.review_status == ReviewStatus.REVIEWED

    def test_api(self, api, user):
        qso = logged(user, "W1AW", mode="NOTAMODE")
        response = api.send("post", f"/api/v1/qsos/{qso.uuid}/checked")
        assert response.json()["review_status"] == "reviewed"
