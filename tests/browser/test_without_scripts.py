"""Everything works in a real browser with scripts off and no images"""

import re
from pathlib import Path

import pytest
from apps.logbook.models import QSO, ReviewStatus
from playwright.sync_api import expect

from tests.web.test_imports import ADIF
from tests.web.test_log import logged

pytestmark = pytest.mark.django_db(transaction=True)


def fill_qso(tab, call="W1AW", **fields):
    tab.get_by_label("Call", exact=False).first.fill(call)
    tab.get_by_label("RST sent").fill(fields.get("rst_sent", "59"))
    tab.get_by_label("RST received").fill(fields.get("rst_rcvd", "57"))
    tab.get_by_label("Name").fill(fields.get("name", "Hiram"))
    tab.get_by_label("Frequency (MHz)").fill(fields.get("freq", "14.205"))
    tab.get_by_label("Mode").fill(fields.get("mode", "SSB"))


class TestLogging:
    def test_with_the_keyboard(self, no_js):
        tab = no_js.go("/log/")
        # The cursor starts in the callsign
        expect(tab.locator("#id_call")).to_be_focused()
        # Set once, and kept from one QSO to the next
        tab.get_by_label("Frequency (MHz)").fill("14.205")
        tab.get_by_label("Mode").fill("SSB")
        tab.locator("#id_call").focus()
        tab.keyboard.type("w1aw")
        for value in ("59", "57", "Hiram"):
            tab.keyboard.press("Tab")
            tab.keyboard.type(value)
        tab.keyboard.press("Enter")
        expect(tab.get_by_text("Logged W1AW.")).to_be_visible()
        expect(tab.locator("#id_call")).to_be_focused()
        qso = QSO.objects.get()
        assert (qso.rst_sent, qso.rst_rcvd, qso.name) == ("59", "57", "Hiram")
        assert not no_js.problems

    def test_warnings(self, no_js):
        tab = no_js.go("/log/")
        fill_qso(tab, mode="NOTAMODE")
        tab.get_by_role("button", name="Log QSO").click()
        expect(tab.get_by_text("NOTAMODE isn't a known mode")).to_be_visible()
        tab.get_by_label("Log it anyway").check()
        tab.get_by_role("button", name="Log QSO").click()
        expect(tab.get_by_text("Logged W1AW.")).to_be_visible()
        assert QSO.objects.get().review_status == ReviewStatus.NEEDS_REVIEW

    def test_look_up(self, no_js, user):
        logged(user, "W1AW", name="Hiram")
        tab = no_js.go("/log/")
        tab.get_by_label("Call", exact=False).first.fill("W1AW")
        tab.get_by_role("button", name="Look up callsign").click()
        expect(tab.get_by_text("Worked 1 time before")).to_be_visible()
        expect(tab.get_by_label("Name")).to_have_value("Hiram")
        assert QSO.objects.count() == 1


class TestNeverLost:
    def test_back_after_a_failed_send_has_everything(self, no_js):
        tab = no_js.go("/log/")
        fill_qso(tab, name="Hiram Percy")
        tab.get_by_label("Comment").fill("Big signal")

        def drop_posts(route):
            if route.request.method == "POST":
                route.abort("connectionfailed")
            else:
                route.continue_()

        tab.route("**/log/", drop_posts)
        tab.get_by_role("button", name="Log QSO").click()
        tab.wait_for_load_state()
        # Browsers either stay on the form or show an error page, from
        # which Back goes to the form
        if not tab.get_by_label("Name").count():
            tab.go_back()
        expect(tab.get_by_label("Name")).to_have_value("Hiram Percy")
        expect(tab.get_by_label("Comment")).to_have_value("Big signal")
        expect(tab.get_by_label("Call", exact=False).first).to_have_value(
            "W1AW"
        )
        tab.unroute("**/log/")
        tab.get_by_role("button", name="Log QSO").click()
        expect(tab.get_by_text("Logged W1AW.")).to_be_visible()
        assert QSO.objects.get().comment == "Big signal"

    def test_back_after_a_server_error_has_everything(self, no_js, monkeypatch):
        tab = no_js.go("/log/")
        fill_qso(tab, name="Hiram Percy")

        def broken(*args, **kwargs):
            raise RuntimeError("The database has gone")

        # The live server runs in this process, so this breaks it for real
        monkeypatch.setattr("apps.logbook.services.log_qso", broken)
        tab.get_by_role("button", name="Log QSO").click()
        expect(
            tab.get_by_text("press your browser's back button")
        ).to_be_visible()
        tab.go_back()
        expect(tab.get_by_label("Name")).to_have_value("Hiram Percy")
        monkeypatch.undo()
        tab.get_by_role("button", name="Log QSO").click()
        expect(tab.get_by_text("Logged W1AW.")).to_be_visible()


class TestEverythingElse:
    def test_export_and_download(self, no_js, user, live_server):
        logged(user, "W1AW")
        tab = no_js.go("/exports/")
        tab.get_by_label("ADIF (.adi)").check()
        tab.get_by_role("button", name="Make export").click()
        tab.get_by_role("link", name="Check again").or_(
            tab.get_by_role("link", name=re.compile("^Download"))
        ).first.wait_for()
        for _ in range(10):
            download_link = tab.get_by_role(
                "link", name=re.compile("^Download")
            )
            if download_link.count():
                break
            tab.get_by_role("link", name="Check again").click()
        with tab.expect_download() as download:
            download_link.click()
        path = download.value.path()
        assert b"<CALL:4>W1AW" in Path(path).read_bytes()

    def test_import(self, no_js, tmp_path):
        upload = tmp_path / "log.adi"
        upload.write_bytes(ADIF)
        tab = no_js.go("/imports/")
        tab.get_by_label("ADIF file").set_input_files(str(upload))
        tab.get_by_role("button", name="Import").click()
        expect(
            tab.get_by_role("heading", name="Import of log.adi")
        ).to_be_visible()
        tab.get_by_role(
            "button", name=re.compile("Log it, marked to check")
        ).click()
        expect(tab.get_by_text("Record 2 is logged.")).to_be_visible()
        assert QSO.objects.count() == 2

    def test_dark_colours(self, no_js):
        tab = no_js.go("/")
        tab.get_by_label("Colours").select_option("dark")
        tab.get_by_role("button", name="Use these colours").click()
        expect(tab.locator("html")).to_have_attribute("data-theme", "dark")

    def test_advanced_view_and_back(self, no_js):
        tab = no_js.go("/log/")
        tab.get_by_role("button", name="Switch to the advanced view").click()
        expect(tab.get_by_label("Notes")).to_be_visible()
        tab.get_by_role("button", name="Switch back to the simple view").click()
        expect(tab.get_by_label("Notes")).to_have_count(0)

    def test_sign_out(self, no_js):
        tab = no_js.go("/")
        tab.get_by_role("button", name="Sign out").click()
        expect(tab.get_by_role("link", name="Sign in").first).to_be_visible()
