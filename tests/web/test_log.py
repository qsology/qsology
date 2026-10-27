"""Logging QSOs: without scripts (the text browser), as the page's scripts
and htmx do it, and through the API. Above all, a QSO is never lost: see
the TestNeverLost classes"""

from datetime import UTC, datetime, timedelta

import pytest
from apps.accounts.models import ApiKey
from apps.logbook.models import QSO, ReviewStatus
from apps.logbook.services import log_qso
from apps.qrz.client import QRZError, QRZResult
from apps.qrz.models import QRZCredential
from bs4 import BeautifulSoup
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from tests.web.checks import check_html, check_page
from tests.web.conftest import PASSWORD
from tests.web.textbrowser import TextBrowser

pytestmark = pytest.mark.django_db

LOG = "/log/"


def logged(user, call="W1AW", **values):
    values.setdefault("time_on", timezone.now() - timedelta(hours=1))
    values.setdefault("band", "20m")
    values.setdefault("mode", "SSB")
    values.setdefault("station_callsign", "M0WME")
    return log_qso(user, {"call": call, **values}, accept_warnings=True)


def entry(signed_in, **fields):
    signed_in.get(LOG)
    defaults = {
        "Call": "w1aw",
        "RST sent": "59",
        "RST received": "57",
        "Frequency (MHz)": "14.205",
        "Mode": "SSB",
    }
    for label, value in {**defaults, **fields}.items():
        signed_in.fill(label, value)
    return signed_in


class TestLogging:
    def test_text(self, signed_in, user):
        signed_in.get("/")
        signed_in.click("Log")
        signed_in.fill("Call", "w1aw")
        signed_in.fill("RST sent", "59")
        signed_in.fill("RST received", "57")
        signed_in.fill("Name", "Hiram")
        signed_in.fill("Frequency (MHz)", "14.205")
        signed_in.fill("Mode", "ssb")
        signed_in.press("Log QSO")
        assert signed_in.path == LOG
        assert "Logged W1AW." in signed_in.messages()
        row = signed_in.table("The last")[0]
        assert (row["Call"], row["Band"], row["Mode"]) == ("W1AW", "20m", "SSB")
        qso = QSO.objects.get()
        assert qso.station_callsign == "M0WME"
        assert qso.review_status == ReviewStatus.OK
        assert timezone.now() - qso.time_on < timedelta(minutes=1)

    def test_cursor_starts_in_the_callsign(self, signed_in):
        signed_in.get(LOG)
        assert signed_in.soup.find(autofocus=True)["name"] == "call"

    def test_the_next_keeps_frequency_and_mode(self, signed_in):
        entry(signed_in).press("Log QSO")
        assert signed_in.value("Call") == ""
        assert signed_in.value("RST sent") == ""
        assert signed_in.value("Frequency (MHz)") == "14.205"
        assert signed_in.value("Mode") == "SSB"
        assert signed_in.value("Station callsign") == "M0WME"

    def test_a_given_date_and_time(self, signed_in):
        entry(signed_in, **{"Date (UTC)": "20240301", "Time (UTC)": "1405"})
        signed_in.press("Log QSO")
        assert QSO.objects.get().time_on == datetime(
            2024, 3, 1, 14, 5, tzinfo=UTC
        )

    def test_an_earlier_date_needs_a_time(self, signed_in):
        entry(signed_in, **{"Date (UTC)": "2024-03-01", "Name": "Hiram"})
        signed_in.press("Log QSO")
        assert signed_in.status == 422
        assert signed_in.field_errors("Time (UTC)") == ["Enter the time"]
        assert not QSO.objects.exists()

    def test_the_advanced_view_has_more(self, signed_in, user):
        user.ui_mode = "advanced"
        user.save()
        entry(signed_in, **{"Grid square": "fn31pr", "Notes": "Big signal"})
        signed_in.press("Log QSO")
        qso = QSO.objects.get()
        assert (qso.gridsquare, qso.notes) == ("FN31pr", "Big signal")


class TestWarnings:
    def test_text(self, signed_in):
        entry(signed_in, Mode="NOTAMODE")
        signed_in.press("Log QSO")
        assert signed_in.status == 422
        assert "NOTAMODE isn't a known mode" in signed_in
        assert not QSO.objects.exists()
        signed_in.check("Log it anyway, I've checked these")
        signed_in.press("Log QSO")
        assert "Logged W1AW." in signed_in.messages()
        assert QSO.objects.get().review_status == ReviewStatus.NEEDS_REVIEW

    def test_fixing_it_instead(self, signed_in):
        entry(signed_in, Mode="NOTAMODE")
        signed_in.press("Log QSO")
        signed_in.fill("Mode", "CW")
        signed_in.press("Log QSO")
        assert QSO.objects.get().review_status == ReviewStatus.OK


class TestNeverLostWithoutScripts:
    def test_mistakes_keep_everything_typed(self, signed_in):
        entry(signed_in, **{"Name": "Hiram", "Frequency (MHz)": "fourteen"})
        signed_in.fill("Comment", "Nice QSO")
        signed_in.press("Log QSO")
        assert signed_in.status == 422
        assert signed_in.field_errors("Frequency (MHz)") == ["Must be a number"]
        assert signed_in.errors() == ["Frequency (MHz): Must be a number"]
        for label, value in (
            ("Call", "w1aw"),
            ("Name", "Hiram"),
            ("Comment", "Nice QSO"),
            ("RST received", "57"),
            ("Frequency (MHz)", "fourteen"),
        ):
            assert signed_in.value(label) == value

    def test_sending_twice_logs_once(self, signed_in):
        """Back and send again, after a response that never arrived, say"""
        entry(signed_in).press("Log QSO")
        signed_in.back()
        signed_in.press("Log QSO")
        assert QSO.objects.count() == 1
        assert "W1AW was already logged, it was sent twice." in " ".join(
            signed_in.messages()
        )

    def test_the_page_is_kept_for_back(self, signed_in):
        """Browsers only keep what was typed for Back if the page may be
        kept, and (Firefox) the fields don't have autocomplete off"""
        signed_in.get(LOG)
        assert "no-store" not in signed_in.response.get("Cache-Control", "")
        form = signed_in.soup.find("form", id="qso-entry")
        assert not form.find_all(autocomplete="off")
        assert form.get("autocomplete") != "off"

    def test_signed_out_meanwhile(self, client, user, callsign):
        browser = TextBrowser(client, check=check_page)
        client.force_login(user)
        entry(browser, Name="Hiram")
        client.logout()
        browser.press("Log QSO")
        assert browser.path == reverse("accounts:login")
        assert "You'd been signed out" in " ".join(browser.messages())
        browser.fill("Username", user.username)
        browser.fill("Password", PASSWORD)
        browser.press("Sign in")
        assert browser.path == LOG
        assert "hasn't been logged yet" in browser
        assert browser.value("Call") == "w1aw"
        assert browser.value("Name") == "Hiram"
        browser.press("Log QSO")
        assert "Logged W1AW." in browser.messages()
        assert QSO.objects.get().name == "Hiram"
        # And it's gone from those waiting
        assert "hasn't been logged yet" not in browser

    def test_signed_out_and_thrown_away(self, client, user, callsign):
        browser = TextBrowser(client, check=check_page)
        client.force_login(user)
        entry(browser)
        client.logout()
        browser.press("Log QSO")
        client.force_login(user)
        browser.get(LOG)
        browser.press("Throw this QSO away")
        assert "hasn't been logged yet" not in browser
        assert browser.value("Call") == ""
        assert not QSO.objects.exists()

    def test_after_signing_in_again_elsewhere(self, user, callsign):
        """Signing in again changes the CSRF token, so a page loaded before
        then can't be sent. What was in it is shown to send again"""
        client = Client(enforce_csrf_checks=True)
        browser = TextBrowser(client, check=check_page)
        client.force_login(user)
        entry(browser, Name="Hiram")
        # Signed in again in another tab
        client.cookies["csrftoken"] = "x" * 32
        browser.press("Log QSO")
        assert browser.status == 403
        assert "Hiram" in browser
        assert not QSO.objects.exists()
        browser.press("Send it again")
        assert "Logged W1AW." in browser.messages()
        assert QSO.objects.get().name == "Hiram"

    def test_resending_only_for_our_own_pages(self, user):
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        response = client.post(
            LOG,
            {"call": "W1AW"},
            headers={"Origin": "https://elsewhere.example"},
        )
        assert response.status_code == 403
        assert b"Send it again" not in response.content

    def test_resending_never_shows_a_password(self, user):
        client = Client(enforce_csrf_checks=True)
        response = client.post(
            reverse("accounts:login"),
            {"username": user.username, "password": PASSWORD},
            headers={"Referer": "http://testserver/account/sign-in/"},
        )
        assert response.status_code == 403
        assert PASSWORD.encode() not in response.content
        assert b"Send it again" not in response.content


class TestLookUp:
    def test_text(self, signed_in, user):
        logged(user, name="Hiram", qth="Newington", gridsquare="FN31")
        entry(signed_in)
        signed_in.press("Look up callsign")
        assert "Worked 1 time before" in signed_in
        assert signed_in.value("Name") == "Hiram"
        assert signed_in.value("Call") == "w1aw"
        assert signed_in.value("RST received") == "57"
        # Nothing logged, and the cursor goes on to what's left to fill in
        assert QSO.objects.count() == 1
        assert signed_in.soup.find(autofocus=True)["name"] == "comment"

    def test_never_changes_what_was_typed(self, signed_in, user):
        logged(user, name="Hiram")
        entry(signed_in, Name="Bob")
        signed_in.press("Look up callsign")
        assert signed_in.value("Name") == "Bob"

    def test_htmx(self, signed_in, htmx, user):
        logged(user, name="Hiram")
        response = htmx.get(reverse("logbook:lookup"), {"call": " w1aw "})
        check_html(response.content)
        result = BeautifulSoup(response.content, "html.parser").find(
            class_="lookup-result"
        )
        assert result["data-call"] == "W1AW"
        assert result["data-fill"] == '{"name": "Hiram"}'

    def test_page_asks_when_the_cursor_leaves_the_call(self, signed_in):
        signed_in.get(LOG)
        call = signed_in.field("Call")
        assert call["hx-get"] == reverse("logbook:lookup")
        assert call["hx-trigger"] == "change"
        assert call["hx-target"] == "#lookup"
        # Only the panel outside the form changes, never a field
        lookup = signed_in.soup.find(id="lookup")
        assert lookup.find_parent("form") is None
        assert lookup.find(["input", "select", "textarea"]) is None

    def test_without_js_its_a_page(self, signed_in, user):
        logged(user)
        signed_in.get(reverse("logbook:lookup") + "?call=W1AW")
        assert signed_in.h1 == "Look up a callsign"
        assert "Worked 1 time before" in signed_in

    def test_qrz(self, signed_in, htmx, user, monkeypatch):
        logged(user, name="Old name")
        QRZCredential.objects.create(user=user, username="u", password="p")
        found = QRZResult(
            "W1AW",
            {"fname": "Hiram", "name": "Maxim", "addr2": "Newington"},
        )
        monkeypatch.setattr(
            "apps.qrz.services.lookup", lambda user, call, fetch=None: found
        )
        response = htmx.get(reverse("logbook:lookup"), {"call": "W1AW"})
        assert b"Hiram Maxim" in response.content
        # QRZ.com wins over the last QSO
        assert b"&quot;name&quot;: &quot;Hiram Maxim&quot;" in response.content

    def test_qrz_down(self, signed_in, htmx, user, monkeypatch):
        QRZCredential.objects.create(user=user, username="u", password="p")

        def down(user, call, fetch=None):
            raise QRZError("timeout")

        monkeypatch.setattr("apps.qrz.services.lookup", down)
        response = htmx.get(reverse("logbook:lookup"), {"call": "W1AW"})
        assert response.status_code == 200
        assert b"QRZ.com couldn&#x27;t be reached" in response.content

    def test_api(self, client, user):
        logged(user, name="Hiram")
        _, key = ApiKey.issue(user, "test")
        response = client.get(
            "/api/v1/lookup/w1aw", headers={"Authorization": f"Bearer {key}"}
        )
        data = response.json()
        assert (data["call"], data["count"]) == ("W1AW", 1)
        assert data["fill"] == {"name": "Hiram"}
        assert data["previous"][0]["qso"]["call"] == "W1AW"


def fields_for(**values):
    data = {
        "call": "W1AW",
        "rst_sent": "59",
        "rst_rcvd": "57",
        "freq": "14.205",
        "mode": "SSB",
        "date": "2024-03-01",
        "time": "14:05:00",
        "station_callsign": "M0WME",
        "uuid": "6f9c1c58-2f3a-4c7c-9a52-3b8e9d1f2a10",
        "shown": "call,rst_sent,rst_rcvd,freq,mode,date,time,station_callsign",
    }
    data.update(values)
    return data


class TestLoggingWithScripts:
    """What the log page's script (static/js/log.js) sends and gets back"""

    def test_logged_gives_its_row(self, signed_in, htmx):
        response = htmx.post(LOG, fields_for())
        assert response.status_code == 201
        check_html(response.content)
        row = BeautifulSoup(response.content, "html.parser").find("tr")
        assert row["id"] == "qso-6f9c1c58-2f3a-4c7c-9a52-3b8e9d1f2a10"
        assert QSO.objects.get().time_on == datetime(
            2024, 3, 1, 14, 5, tzinfo=UTC
        )

    def test_sent_twice(self, signed_in, htmx):
        htmx.post(LOG, fields_for())
        response = htmx.post(LOG, fields_for())
        assert response.status_code == 200
        assert b"data-already" in response.content
        assert QSO.objects.count() == 1

    def test_warnings(self, signed_in, htmx):
        response = htmx.post(LOG, fields_for(mode="NOTAMODE"))
        assert response.status_code == 422
        check_html(response.content)
        problem = BeautifulSoup(response.content, "html.parser").find(
            class_="qso-problem"
        )
        assert problem.has_attr("data-warnings")
        assert "NOTAMODE isn't a known mode" in problem.text
        response = htmx.post(
            LOG, fields_for(mode="NOTAMODE", accept_warnings="on")
        )
        assert response.status_code == 201

    def test_errors(self, signed_in, htmx):
        response = htmx.post(LOG, fields_for(freq="lots"))
        assert response.status_code == 422
        problem = BeautifulSoup(response.content, "html.parser").find(
            class_="qso-problem"
        )
        assert not problem.has_attr("data-warnings")
        assert "Frequency (MHz): Must be a number" in problem.text

    def test_signed_out(self, client, htmx):
        response = htmx.post(LOG, fields_for())
        assert response.status_code == 401
        assert not QSO.objects.exists()

    def test_fields_the_page_didnt_show_are_kept(self, signed_in, htmx):
        """From a page loaded in the advanced view, say"""
        htmx.post(LOG, fields_for(notes="Big signal"))
        assert QSO.objects.get().notes == "Big signal"


@pytest.mark.usefixtures("callsign")
class TestLoggingWithTheApi:
    def headers(self, user):
        _, key = ApiKey.issue(user, "test")
        return {"Authorization": f"Bearer {key}"}

    def post(self, client, headers, **payload):
        return client.post(
            "/api/v1/qsos",
            payload,
            content_type="application/json",
            headers=headers,
        )

    def test_logging(self, client, user):
        headers = self.headers(user)
        qso = {
            "call": "w1aw",
            "time_on": "2024-03-01T14:05:00Z",
            "freq": "14.205",
            "mode": "SSB",
            "station_callsign": "M0WME",
        }
        response = self.post(client, headers, qso=qso)
        assert response.status_code == 201
        data = response.json()
        assert data["qso"]["call"] == "W1AW"
        assert data["qso"]["band"] == "20m"
        assert data["version"] == 1

    def test_sending_twice_logs_once(self, client, user):
        headers = self.headers(user)
        qso = {"call": "W1AW", "time_on": "2024-03-01T14:05:00Z"}
        uuid = "6f9c1c58-2f3a-4c7c-9a52-3b8e9d1f2a10"
        first = self.post(
            client, headers, qso=qso, uuid=uuid, accept_warnings=True
        )
        again = self.post(
            client, headers, qso=qso, uuid=uuid, accept_warnings=True
        )
        assert (first.status_code, again.status_code) == (201, 200)
        assert QSO.objects.count() == 1

    def test_warnings(self, client, user):
        headers = self.headers(user)
        qso = {"call": "W1AW", "time_on": "2024-03-01T14:05:00Z"}
        response = self.post(client, headers, qso=qso)
        assert response.status_code == 422
        codes = {w["code"] for w in response.json()["warnings"]}
        assert "mode_missing" in codes
        response = self.post(client, headers, qso=qso, accept_warnings=True)
        assert response.status_code == 201
        assert response.json()["review_status"] == "needs_review"

    def test_errors_name_the_field(self, client, user):
        headers = self.headers(user)
        response = self.post(
            client,
            headers,
            qso={"call": "W1AW", "time_on": "yesterday", "colour": "red"},
        )
        assert response.status_code == 422
        errors = response.json()["errors"]
        assert set(errors) == {"time_on", "colour"}

    def test_session_needs_csrf(self, user):
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        response = client.post(
            "/api/v1/qsos",
            {"qso": {"call": "W1AW"}},
            content_type="application/json",
        )
        assert response.status_code == 403
