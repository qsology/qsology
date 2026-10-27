"""With scripts on: QSOs are logged in the background and kept in the
browser until the server has them, look ups never move the cursor, and
nothing breaks the accessibility checks in light or dark"""

import re
import time
from pathlib import Path

import pytest
from apps.logbook import lookup as lookups
from apps.logbook.importing import import_file
from apps.logbook.models import QSO, Export, ExportStatus, ReviewStatus
from apps.logbook.tasks import build_export
from playwright.sync_api import expect

from tests.web.test_imports import ADIF
from tests.web.test_log import logged

pytestmark = pytest.mark.django_db(transaction=True)

PAGES = [
    "/",
    "/log/",
    "/log/qsos/",
    "/exports/",
    "/imports/",
    "/callsigns/",
    "/account/",
    "/account/qrz/",
    "/account/api-keys/",
    "/account/password/",
]


def start_log(tab):
    """The frequency and mode, set once before logging"""
    tab.get_by_label("Frequency (MHz)").fill("14.205")
    tab.get_by_label("Mode").fill("SSB")
    tab.locator("#id_call").focus()


def type_qso(tab, call="W1AW", name="Hiram"):
    tab.keyboard.type(call)
    for value in ("59", "57", name):
        tab.keyboard.press("Tab")
        tab.keyboard.type(value)


def outbox(tab):
    return tab.locator("#outbox")


class TestLogging:
    def test_in_the_background(self, modern):
        tab = modern.go("/log/")
        start_log(tab)
        type_qso(tab)
        tab.keyboard.press("Enter")
        # Straight on to the next, without waiting for the server
        expect(tab.locator("#id_call")).to_be_focused()
        expect(tab.locator("#id_call")).to_have_value("")
        expect(tab.get_by_label("Name")).to_have_value("")
        expect(tab.get_by_label("Frequency (MHz)")).to_have_value("14.205")
        expect(tab.get_by_role("link", name="W1AW")).to_be_visible()
        expect(outbox(tab)).to_be_hidden()
        qso = QSO.objects.get()
        assert (qso.call, qso.name, qso.band) == ("W1AW", "Hiram", "20m")
        assert not modern.problems

    def test_one_after_another(self, modern):
        tab = modern.go("/log/")
        start_log(tab)
        for call in ("W1AW", "K1ABC", "G4XYZ"):
            type_qso(tab, call)
            tab.keyboard.press("Enter")
        for call in ("W1AW", "K1ABC", "G4XYZ"):
            expect(tab.get_by_role("link", name=call)).to_be_visible()
        assert QSO.objects.count() == 3

    def test_logged_while_another_is_being_sent(self, modern, monkeypatch):
        """On a slow connection the next QSO is logged before the last one
        has got there, and it's sent too, without waiting for a retry"""
        from apps.logbook import services

        real = services.log_qso

        def slow(*args, **kwargs):
            time.sleep(1)
            return real(*args, **kwargs)

        monkeypatch.setattr(services, "log_qso", slow)
        tab = modern.go("/log/")
        start_log(tab)
        type_qso(tab, "W1AW")
        tab.keyboard.press("Enter")
        type_qso(tab, "K1ABC")
        tab.keyboard.press("Enter")
        expect(tab.get_by_role("link", name="K1ABC")).to_be_visible(
            timeout=4000
        )
        assert QSO.objects.count() == 2

    def test_warnings_then_log_anyway(self, modern):
        tab = modern.go("/log/")
        tab.get_by_label("Frequency (MHz)").fill("14.205")
        tab.get_by_label("Mode").fill("NOTAMODE")
        tab.locator("#id_call").focus()
        type_qso(tab)
        tab.keyboard.press("Enter")
        expect(outbox(tab)).to_contain_text("NOTAMODE isn't a known mode")
        assert not QSO.objects.exists()
        tab.get_by_role("button", name="Log it anyway, marked to check").click()
        expect(tab.get_by_role("link", name="W1AW")).to_be_visible()
        assert QSO.objects.get().review_status == ReviewStatus.NEEDS_REVIEW

    def test_put_back_in_the_form_to_fix(self, modern):
        tab = modern.go("/log/")
        tab.get_by_label("Frequency (MHz)").fill("14.205")
        tab.get_by_label("Mode").fill("NOTAMODE")
        tab.locator("#id_call").focus()
        type_qso(tab)
        tab.keyboard.press("Enter")
        tab.get_by_role("button", name="Put it back in the form").click()
        expect(tab.locator("#id_call")).to_be_focused()
        expect(tab.locator("#id_call")).to_have_value("W1AW")
        expect(tab.get_by_label("Name")).to_have_value("Hiram")
        tab.get_by_label("Mode").fill("CW")
        tab.locator("#id_call").press("Enter")
        expect(tab.get_by_role("link", name="W1AW")).to_be_visible()
        expect(outbox(tab)).to_be_hidden()
        assert QSO.objects.get().mode == "CW"


class TestNeverLost:
    def test_no_connection(self, modern):
        tab = modern.go("/log/")
        start_log(tab)
        modern.context.set_offline(True)
        type_qso(tab)
        tab.keyboard.press("Enter")
        expect(outbox(tab)).to_contain_text("1 QSO not sent yet")
        expect(outbox(tab)).to_contain_text("no connection")
        # The form's clear for the next one meanwhile
        expect(tab.locator("#id_call")).to_have_value("")
        assert not QSO.objects.exists()
        modern.context.set_offline(False)
        tab.evaluate("window.dispatchEvent(new Event('online'))")
        expect(outbox(tab)).to_be_hidden()
        assert QSO.objects.get().call == "W1AW"

    def test_kept_when_the_page_is_closed(self, modern, make_page):
        tab = modern.go("/log/")
        start_log(tab)
        modern.context.set_offline(True)
        type_qso(tab)
        tab.keyboard.press("Enter")
        expect(outbox(tab)).to_contain_text("1 QSO not sent yet")
        tab.close()
        modern.context.set_offline(False)
        # Opening the log again sends it
        again = modern.context.new_page()
        again.goto(modern.base_url + "/log/")
        expect(again.get_by_role("link", name="W1AW")).to_be_visible()
        assert QSO.objects.count() == 1

    def test_server_errors_are_tried_again(self, modern, monkeypatch):
        tab = modern.go("/log/")
        start_log(tab)
        broken = {"times": 0}
        real = QSO.save

        def flaky(self, *args, **kwargs):
            if broken["times"] < 1:
                broken["times"] += 1
                raise RuntimeError("The database has gone")
            return real(self, *args, **kwargs)

        monkeypatch.setattr(QSO, "save", flaky)
        type_qso(tab)
        tab.keyboard.press("Enter")
        expect(outbox(tab)).to_contain_text("couldn't take it")
        tab.get_by_role("button", name="Try sending now").click()
        # The box stays while the user's in it, saying so
        expect(outbox(tab)).to_contain_text("Everything's been sent")
        assert QSO.objects.count() == 1
        tab.locator("#id_call").focus()
        expect(outbox(tab)).to_be_hidden()

    def test_downloading_whats_waiting(self, modern, user):
        tab = modern.go("/log/")
        start_log(tab)
        modern.context.set_offline(True)
        type_qso(tab)
        tab.keyboard.press("Enter")
        with tab.expect_download() as download:
            tab.get_by_role("button", name="Download as ADIF").click()
        content = Path(download.value.path()).read_text()
        assert "<CALL:4>W1AW" in content
        assert "<FREQ:6>14.205" in content
        assert re.search(r"<APP_QSOLOGY_UUID:36>[0-9a-f-]{36}", content)
        # Imported, it's the same QSO, so sending it later logs nothing more
        import_file(user, content, filename="waiting.adi")
        assert QSO.objects.count() == 1
        modern.context.set_offline(False)
        tab.get_by_role("button", name="Try sending now").click()
        expect(outbox(tab)).to_contain_text("Everything's been sent")
        assert QSO.objects.count() == 1

    def test_signed_out_meanwhile(self, modern, user):
        tab = modern.go("/log/")
        start_log(tab)
        modern.context.clear_cookies()
        type_qso(tab)
        tab.keyboard.press("Enter")
        expect(outbox(tab)).to_contain_text("signed out")
        assert not QSO.objects.exists()
        tab.get_by_role("button", name="Sign in again").click()
        tab.get_by_label("Username").fill(user.username)
        tab.get_by_label("Password").fill("correct-horse-battery-staple")
        tab.get_by_role("button", name="Sign in").click()
        tab.wait_for_url(re.compile("/log/$"))
        expect(tab.get_by_role("link", name="W1AW")).to_be_visible()
        assert QSO.objects.count() == 1

    def test_only_sent_as_who_logged_it(self, modern, make_page, other_user):
        """Another user signing in on the same browser doesn't send them"""
        tab = modern.go("/log/")
        start_log(tab)
        modern.context.set_offline(True)
        type_qso(tab)
        tab.keyboard.press("Enter")
        expect(outbox(tab)).to_contain_text("1 QSO not sent yet")
        modern.context.set_offline(False)
        modern.context.clear_cookies()
        modern.sign_in(other_user)
        modern.go("/log/")
        expect(outbox(tab)).to_be_hidden()
        assert not QSO.objects.exists()


@pytest.fixture
def slow_lookup(monkeypatch):
    """Look ups that take a second, as on a slow connection"""
    real = lookups.lookup

    def slow(*args, **kwargs):
        time.sleep(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(lookups, "lookup", slow)


class TestLookUpNeverMovesTheCursor:
    def test_fills_empty_fields(self, modern, user):
        logged(user, "W1AW", name="Hiram", qth="Newington")
        tab = modern.go("/log/")
        tab.locator("#id_call").fill("W1AW")
        tab.keyboard.press("Tab")
        expect(tab.locator("#lookup")).to_contain_text("Worked 1 time before")
        expect(tab.get_by_label("Name")).to_have_value("Hiram")

    def test_while_typing_elsewhere(self, modern, user, slow_lookup):
        logged(user, "W1AW", name="Hiram")
        tab = modern.go("/log/")
        tab.locator("#id_call").focus()
        tab.keyboard.type("W1AW")
        tab.keyboard.press("Tab")
        # Typing on while the look up is on its way
        tab.keyboard.type("59")
        tab.keyboard.press("ArrowLeft")
        expect(tab.locator("#lookup")).to_contain_text("Worked 1 time before")
        expect(tab.get_by_label("RST sent")).to_be_focused()
        assert tab.evaluate("document.activeElement.selectionStart") == 1
        expect(tab.get_by_label("RST sent")).to_have_value("59")
        expect(tab.get_by_label("Name")).to_have_value("Hiram")

    def test_never_the_field_the_cursor_is_in(self, modern, user, slow_lookup):
        logged(user, "W1AW", name="Hiram")
        tab = modern.go("/log/")
        tab.locator("#id_call").fill("W1AW")
        tab.get_by_label("Name").focus()
        expect(tab.locator("#lookup")).to_contain_text("Worked 1 time before")
        expect(tab.get_by_label("Name")).to_be_focused()
        expect(tab.get_by_label("Name")).to_have_value("")

    def test_never_what_was_typed(self, modern, user, slow_lookup):
        logged(user, "W1AW", name="Hiram")
        tab = modern.go("/log/")
        tab.locator("#id_call").fill("W1AW")
        tab.get_by_label("Name").focus()
        tab.keyboard.type("Bob")
        tab.get_by_label("Comment").focus()
        expect(tab.locator("#lookup")).to_contain_text("Worked 1 time before")
        expect(tab.get_by_label("Name")).to_have_value("Bob")

    def test_arrow_keys_ask_for_nothing(self, modern):
        tab = modern.go("/log/")
        asked = []
        tab.on(
            "request",
            lambda request: (
                "/log/lookup/" in request.url and asked.append(request.url)
            ),
        )
        tab.locator("#id_call").focus()
        tab.keyboard.type("W1AW")
        for key in ("ArrowLeft", "ArrowLeft", "ArrowRight", "Home", "End"):
            tab.keyboard.press(key)
        tab.wait_for_timeout(500)
        assert asked == []
        expect(tab.locator("#id_call")).to_be_focused()

    def test_nothing_swapped_out_from_under_the_cursor(self, modern, user):
        """If the user is in what a swap would replace, it's dropped"""
        logged(user, "W1AW", name="Hiram")
        tab = modern.go("/log/")
        tab.evaluate(
            """() => {
              const button = document.createElement('button');
              button.id = 'in-the-way';
              button.textContent = 'In the way';
              document.getElementById('lookup').append(button);
            }"""
        )
        tab.locator("#id_call").fill("W1AW")
        tab.locator("#in-the-way").focus()
        tab.wait_for_timeout(1000)
        expect(tab.locator("#in-the-way")).to_be_focused()
        expect(tab.locator("#lookup")).not_to_contain_text("Worked")

    def test_for_the_call_in_the_form_only(self, modern, user, slow_lookup):
        """A look up for a call that's since been logged fills nothing"""
        logged(user, "W1AW", name="Hiram")
        tab = modern.go("/log/")
        start_log(tab)
        tab.keyboard.type("W1AW")
        tab.keyboard.press("Tab")
        tab.keyboard.type("59")
        tab.keyboard.press("Enter")
        tab.wait_for_timeout(1500)
        expect(tab.get_by_label("Name")).to_have_value("")


class TestExportsAndImports:
    def test_export_row_shows_when_its_ready(self, modern, user):
        logged(user, "W1AW")
        export = Export.objects.create(
            owner=user, name="Master log", format="adi"
        )
        tab = modern.go("/exports/")
        tab.get_by_label("ADIF (.adi)").focus()
        build_export(str(export.pk))
        assert Export.objects.get().status == ExportStatus.READY
        expect(
            tab.get_by_role("link", name=re.compile("^Download"))
        ).to_be_visible(timeout=15000)
        expect(tab.get_by_label("ADIF (.adi)")).to_be_focused()

    def test_logging_a_held_record_keeps_the_focus_there(
        self, modern, tmp_path
    ):
        upload = tmp_path / "log.adi"
        upload.write_bytes(ADIF)
        tab = modern.go("/imports/")
        tab.get_by_label("ADIF file").set_input_files(str(upload))
        tab.get_by_role("button", name="Import").click()
        button = tab.get_by_role("button", name=re.compile("Log it, marked"))
        button_id = button.get_attribute("id")
        button.focus()
        tab.keyboard.press("Enter")
        logged_text = tab.locator(f"#{button_id}")
        expect(logged_text).to_contain_text("Logged")
        expect(logged_text).to_be_focused()
        assert QSO.objects.count() == 2


class TestEveryPage:
    @pytest.mark.parametrize("path", PAGES)
    def test_no_script_or_csp_errors(self, modern, path):
        modern.go(path)
        modern.tab.wait_for_load_state("networkidle")
        assert modern.tab.evaluate(
            "document.documentElement.classList.contains('js')"
        )
        assert not modern.problems

    @pytest.mark.parametrize(
        ("theme", "device"),
        [("light", "light"), ("dark", "light"), ("auto", "dark")],
        ids=["light", "dark", "device-dark"],
    )
    @pytest.mark.parametrize("path", [*PAGES, "/account/sign-in/"])
    def test_accessibility(
        self, make_page, user, held_callsign, path, theme, device
    ):
        """axe-core finds nothing, in the light and dark colours, chosen or
        from the device"""
        logged(user, "W1AW", mode="NOTAMODE")
        user.theme = theme
        user.save()
        page = make_page(axe=True, color_scheme=device)
        if path != "/account/sign-in/":
            page.sign_in(user)
        page.go(path)
        assert page.axe() == []

    @pytest.mark.parametrize("path", PAGES)
    def test_fits_a_phone(self, make_page, user, held_callsign, path):
        """Nothing scrolls sideways on a small phone, tables aside, which
        scroll in their own box"""
        logged(user, "W1AW")
        page = make_page(viewport={"width": 360, "height": 640})
        page.sign_in(user)
        tab = page.go(path)
        width = tab.evaluate("document.documentElement.scrollWidth")
        assert width <= 360

    def test_without_htmx_everything_still_works(self, modern, user):
        """htmx failing to load (an old browser, say) loses nothing"""
        modern.context.route("**/htmx-*.js", lambda route: route.abort())
        tab = modern.go("/log/")
        start_log(tab)
        type_qso(tab)
        tab.keyboard.press("Enter")
        expect(tab.get_by_role("link", name="W1AW")).to_be_visible()
        assert QSO.objects.count() == 1
