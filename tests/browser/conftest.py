"""Real browsers, for what only a browser can show: that the pages work
with scripts off, that the scripts never move the focus or lose a QSO, and
axe-core's accessibility checks in light and dark colours.

Each test runs in every browser in QSOLOGY_BROWSERS (chromium,firefox by
default). Install them with: task test:browsers. A browser that can't
start fails the tests rather than skipping them"""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from apps.callsigns.services import add_callsign
from playwright.sync_api import Error, sync_playwright

AXE = Path(__file__).parent / "vendor" / "axe-4.14.0.min.js"
BROWSERS = os.environ.get("QSOLOGY_BROWSERS", "chromium,firefox").split(",")
PASSWORD = "correct-horse-battery-staple"
IMAGES = "**/*.{png,jpg,jpeg,gif,svg,webp,ico}"


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests/browser" in str(item.fspath):
            item.add_marker(pytest.mark.browser)


# Only for the browser tests, so its event loop is gone before any other
# tests run
@pytest.fixture(scope="package")
def playwright():
    # Playwright's sync API runs an event loop, which Django takes to mean
    # it's in async code. The tests only use the ORM between browser calls
    os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
    try:
        with sync_playwright() as running:
            yield running
    finally:
        del os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"]


# After the live server, so the browser closes its connections to it first
@pytest.fixture(scope="package", params=BROWSERS)
def browser_engine(request, playwright, live_server):
    try:
        engine = getattr(playwright, request.param).launch()
    except Error as exc:
        pytest.fail(
            f"{request.param} won't start, install it with "
            f"`task test:browsers`: {exc.message.splitlines()[0]}"
        )
    yield engine
    engine.close()


class Page:
    """A browser tab, signed in or not, that fails the test on any CSP
    violation or script error"""

    def __init__(self, context, base_url):
        self.context = context
        self.base_url = base_url
        self.tab = context.new_page()
        self.problems = []
        self.tab.on("pageerror", lambda error: self.problems.append(str(error)))
        self.tab.on(
            "console",
            lambda message: (
                message.type == "error" and self.problems.append(message.text)
            ),
        )

    def go(self, path):
        self.tab.goto(self.base_url + path)
        return self.tab

    def sign_in(self, user):
        self.go("/account/sign-in/")
        self.tab.get_by_label("Username").fill(user.username)
        self.tab.get_by_label("Password").fill(PASSWORD)
        self.tab.get_by_role("button", name="Sign in").click()
        self.tab.wait_for_url(self.base_url + "/")
        return self

    def axe(self, theme=None):
        """axe-core's violations on the page as it is now, in the theme.
        Needs a page made with axe=True"""
        if theme:
            self.tab.evaluate(
                "theme => document.documentElement.dataset.theme = theme",
                theme,
            )
        results = self.tab.evaluate(
            "() => axe.run(document, {resultTypes: ['violations']})"
        )
        return [
            f"{violation['id']}: {violation['help']} "
            f"({', '.join(node['target'][0] for node in violation['nodes'])})"
            for violation in results["violations"]
        ]


@pytest.fixture
def make_page(browser_engine, live_server):
    contexts = []

    def make(javascript=True, axe=False, **options):
        context = browser_engine.new_context(
            java_script_enabled=javascript, **options
        )
        if axe:
            # As an init script, which the site's CSP doesn't apply to,
            # unlike a <script> added to the page
            context.add_init_script(path=str(AXE))
        # Nothing may need an image
        context.route(IMAGES, lambda route: route.abort())
        contexts.append(context)
        page = Page(context, live_server.url)
        return page

    yield make
    for context in contexts:
        context.close()


@pytest.fixture
def held_callsign(user):
    return add_callsign(
        user, "M0WME", datetime(2000, 1, 1, tzinfo=UTC), is_primary=True
    )


@pytest.fixture
def no_js(make_page, user, held_callsign):
    """Signed in, scripts off and no images: the least a browser can do"""
    return make_page(javascript=False).sign_in(user)


@pytest.fixture
def modern(make_page, user, held_callsign):
    """Signed in, scripts on"""
    return make_page().sign_in(user)


@pytest.fixture(autouse=True)
def _static_served(settings):
    # The live server serves static files from where they are
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
    assert settings.STATICFILES_DIRS
