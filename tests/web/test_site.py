"""The landing and home pages, and the preferences every page has: light
or dark colours, and the simple or advanced view"""

import pytest
from apps.accounts.models import ApiKey
from apps.audit.recording import record

from tests.web.test_log import logged

pytestmark = pytest.mark.django_db


class TestLanding:
    def test_text(self, browser):
        browser.get("/")
        assert browser.h1 == "qsology"
        assert "never loses a QSO" in browser
        assert browser.link("Sign in")


class TestHome:
    def test_text(self, signed_in, user):
        logged(user, "W1AW")
        record(user.pk, "export.built", "Your export was built", visible=True)
        record(user.pk, "account.secret", "Only for staff")
        signed_in.get("/")
        assert signed_in.h1 == "Hello, m0wme"
        assert "1 QSO in your log" in signed_in
        assert signed_in.table("Your latest QSOs")[0]["Call"] == "W1AW"
        assert "Your export was built" in signed_in
        assert "Only for staff" not in signed_in

    def test_api(self, client, user):
        record(user.pk, "export.built", "Your export was built", visible=True)
        record(user.pk, "account.secret", "Only for staff")
        _, key = ApiKey.issue(user, "test")
        response = client.get(
            "/api/v1/activity", headers={"Authorization": f"Bearer {key}"}
        )
        assert [e["action"] for e in response.json()["items"]] == [
            "export.built"
        ]


def theme_of(browser) -> str | None:
    return browser.soup.find("html").get("data-theme")


class TestColours:
    def test_follows_the_device_to_start_with(self, browser):
        browser.get("/")
        assert theme_of(browser) is None
        content = browser.soup.find("meta", attrs={"name": "color-scheme"})
        assert content["content"] == "light dark"

    def test_signed_in(self, signed_in, user):
        signed_in.get("/log/")
        signed_in.choose("Colours", "Dark")
        signed_in.press("Use these colours")
        # Back on the page it was changed on
        assert signed_in.path == "/log/"
        assert theme_of(signed_in) == "dark"
        user.refresh_from_db()
        assert user.theme == "dark"
        signed_in.choose("Colours", "Same as my device")
        signed_in.press("Use these colours")
        assert theme_of(signed_in) is None

    def test_signed_out(self, browser):
        browser.get("/")
        browser.choose("Colours", "Light")
        browser.press("Use these colours")
        assert theme_of(browser) == "light"
        browser.click("Sign in")
        assert theme_of(browser) == "light"

    def test_never_sent_somewhere_else(self, client):
        response = client.post(
            "/preferences/theme/",
            {"theme": "dark", "next": "https://elsewhere.example/"},
        )
        assert response["Location"] == "/"


class TestViews:
    def test_advanced_and_back(self, signed_in, user):
        signed_in.get("/log/")
        assert "Notes" not in signed_in
        signed_in.press("Switch to the advanced view")
        assert signed_in.path == "/log/"
        assert signed_in.field("Notes")
        # The way back is on every page
        for page in ("/", "/log/qsos/", "/exports/", "/account/"):
            signed_in.get(page)
            assert signed_in.button("Switch back to the simple view")
        signed_in.press("Switch back to the simple view")
        assert "Notes" not in signed_in
        user.refresh_from_db()
        assert user.ui_mode == "simple"

    def test_api(self, client, user):
        _, key = ApiKey.issue(user, "test")
        response = client.patch(
            "/api/v1/me",
            {"ui_mode": "advanced"},
            content_type="application/json",
            headers={"Authorization": f"Bearer {key}"},
        )
        assert response.json()["ui_mode"] == "advanced"
