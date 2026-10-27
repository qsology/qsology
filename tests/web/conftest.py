from datetime import UTC, datetime

import pytest
from apps.callsigns.services import add_callsign

from tests.web.checks import check_page
from tests.web.textbrowser import TextBrowser

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def browser(client):
    """Signed out, no scripts, styles or images"""
    return TextBrowser(client, check=check_page)


@pytest.fixture
def callsign(user):
    """user holds M0WME, and has since 2000"""
    return add_callsign(
        user,
        "M0WME",
        valid_from=datetime(2000, 1, 1, tzinfo=UTC),
        is_primary=True,
    )


@pytest.fixture
def signed_in(client, user, callsign):
    """Signed in as user, who holds M0WME"""
    client.force_login(user)
    return TextBrowser(client, check=check_page)


@pytest.fixture
def htmx(client):
    """Requests as htmx, and the page's scripts, make them"""

    class Htmx:
        def get(self, url, data=None, **kwargs):
            return client.get(
                url, data, headers={"HX-Request": "true"}, **kwargs
            )

        def post(self, url, data=None, **kwargs):
            return client.post(
                url, data or {}, headers={"HX-Request": "true"}, **kwargs
            )

    return Htmx()
