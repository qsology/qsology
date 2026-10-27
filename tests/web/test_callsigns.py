"""The callsigns a user holds, without scripts and through the API"""

from datetime import UTC, datetime

import pytest
from apps.accounts.models import ApiKey
from apps.callsigns.models import CallsignAllocation
from apps.callsigns.services import add_callsign
from django.urls import reverse

pytestmark = pytest.mark.django_db

LIST = "/callsigns/"


class TestAdding:
    def test_text(self, signed_in, user):
        signed_in.get("/")
        signed_in.click("Callsigns")
        signed_in.fill("Callsign", "2e0abc")
        signed_in.fill("Held from", "2020-01-01")
        signed_in.press("Add callsign")
        assert "2E0ABC has been added." in signed_in.messages()
        rows = signed_in.table("Callsigns held now")
        assert {row["Callsign"] for row in rows} == {"M0WME (main)", "2E0ABC"}

    def test_making_it_the_main_one(self, signed_in, user):
        signed_in.get(LIST)
        signed_in.fill("Callsign", "2E0ABC")
        signed_in.check("This is my main callsign")
        signed_in.press("Add callsign")
        main = CallsignAllocation.objects.get(is_primary=True)
        assert main.callsign.call == "2E0ABC"

    def test_with_a_suffix(self, signed_in):
        signed_in.get(LIST)
        signed_in.fill("Callsign", "M0ABC/P")
        signed_in.press("Add callsign")
        assert signed_in.status == 422
        errors = signed_in.field_errors("Callsign")
        assert "without a prefix or suffix, e.g. M0ABC" in errors[0]
        assert signed_in.value("Callsign") == "M0ABC/P"

    def test_held_by_someone_else(self, signed_in, other_user):
        add_callsign(other_user, "K1ABC", datetime(2020, 1, 1, tzinfo=UTC))
        signed_in.get(LIST)
        signed_in.fill("Callsign", "K1ABC")
        signed_in.press("Add callsign")
        assert "already allocated" in " ".join(signed_in.errors())

    def test_api(self, client, user, callsign):
        _, key = ApiKey.issue(user, "test")
        headers = {"Authorization": f"Bearer {key}"}
        response = client.post(
            "/api/v1/callsigns",
            {"call": "2e0abc", "valid_from": "2020-01-01"},
            content_type="application/json",
            headers=headers,
        )
        assert response.status_code == 201
        assert response.json()["call"] == "2E0ABC"
        listed = client.get("/api/v1/callsigns", headers=headers).json()
        assert {each["call"] for each in listed} == {"M0WME", "2E0ABC"}
        response = client.post(
            "/api/v1/callsigns",
            {"call": "M0ABC/P"},
            content_type="application/json",
            headers=headers,
        )
        assert response.status_code == 422
        assert "call" in response.json()["errors"]


class TestChanging:
    def test_text(self, signed_in, callsign):
        signed_in.get(LIST)
        signed_in.click("Change M0WME")
        signed_in.fill("Held until", "2030-01-01")
        signed_in.press("Save")
        callsign.refresh_from_db()
        assert callsign.valid_until == datetime(2030, 1, 1, tzinfo=UTC)

    def test_no_longer_held(self, signed_in, callsign):
        signed_in.get(reverse("callsigns:change", args=[callsign.pk]))
        signed_in.press("I no longer hold M0WME")
        assert "Held before" in signed_in
        callsign.refresh_from_db()
        assert not callsign.is_active()

    def test_someone_elses(self, signed_in, other_user):
        theirs = add_callsign(
            other_user, "K1ABC", datetime(2020, 1, 1, tzinfo=UTC)
        )
        signed_in.get(reverse("callsigns:change", args=[theirs.pk]))
        assert signed_in.status == 404

    def test_api(self, client, user, callsign):
        _, key = ApiKey.issue(user, "test")
        headers = {"Authorization": f"Bearer {key}"}
        url = f"/api/v1/callsigns/{callsign.pk}"
        response = client.patch(
            url,
            {"valid_until": "2030-01-01", "is_primary": True},
            content_type="application/json",
            headers=headers,
        )
        assert response.json()["valid_until"].startswith("2030-01-01")
        response = client.post(url + "/end", headers=headers)
        assert response.json()["is_current"] is False
