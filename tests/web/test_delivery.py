"""How pages get to the browser: small, compressed, from here only, under
a strict CSP. See docs/web.md"""

import gzip
import hashlib
import json
import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from django.conf import settings
from django.contrib.staticfiles import finders
from django.template import engines

from tests.web.checks import MAX_COMPRESSED_BYTES, MAX_ELEMENTS
from tests.web.test_log import logged

pytestmark = pytest.mark.django_db

STATIC = Path(settings.BASE_DIR) / "static"
TEMPLATES = [
    path
    for root in (
        Path(settings.BASE_DIR) / "templates",
        Path(settings.BASE_DIR) / "apps",
    )
    for path in root.rglob("*.html")
]
PAGES = [
    "/",
    "/log/",
    "/log/qsos/",
    "/exports/",
    "/imports/",
    "/callsigns/",
    "/account/",
    "/account/api-keys/",
]


class TestVendoredFiles:
    def test_are_as_published(self):
        manifest = json.loads((STATIC / "vendor" / "vendor.json").read_text())
        files = {k: v for k, v in manifest.items() if not k.startswith("_")}
        assert files
        for name, entry in files.items():
            digest = hashlib.sha256((STATIC / "vendor" / name).read_bytes())
            assert digest.hexdigest() == entry["sha256"], name
            assert entry["version"] in name

    def test_nothing_else_in_vendor(self):
        listed = set(
            json.loads((STATIC / "vendor" / "vendor.json").read_text())
        )
        present = {p.name for p in (STATIC / "vendor").iterdir()}
        assert present - {"vendor.json"} == listed - {"_comment"}


class TestNothingFromElsewhere:
    def test_templates_load_nothing_from_other_sites(self):
        for template in TEMPLATES:
            text = template.read_text()
            for tag in re.findall(r"<(?:script|link|img|iframe)[^>]*>", text):
                assert "//" not in tag, f"{template}: {tag}"

    def test_scripts_are_modules(self):
        for template in TEMPLATES:
            for tag in re.findall(r"<script[^>]*>", template.read_text()):
                assert 'type="module"' in tag, f"{template}: {tag}"

    def test_our_scripts_only_move_the_focus_for_the_user(self):
        """Every .focus() in our scripts runs from the user's own click or
        key, and says which with a "// Focus:" comment above it. Nothing
        that happens in the background may move the focus"""
        for script in (STATIC / "js").glob("*.js"):
            lines = script.read_text().splitlines()
            for number, line in enumerate(lines):
                if ".focus(" in line:
                    above = lines[number - 1].strip()
                    assert above.startswith("// Focus:"), (
                        f"{script.name}:{number + 1} moves the focus"
                    )


class TestHeaders:
    def test_csp(self, signed_in):
        signed_in.get("/log/")
        policy = signed_in.response["Content-Security-Policy"]
        assert "script-src 'self'" in policy
        assert "style-src 'self'" in policy
        assert "unsafe" not in policy
        assert "frame-ancestors 'none'" in policy

    def test_pages_are_compressed(self, client, user, callsign):
        client.force_login(user)
        response = client.get("/log/", headers={"Accept-Encoding": "gzip"})
        assert response["Content-Encoding"] == "gzip"


class TestBudget:
    """Pages stay small with a full page of QSOs on them"""

    @pytest.fixture
    def full_log(self, user):
        for number in range(60):
            logged(
                user,
                f"W{number}AW",
                name="A long enough name",
                comment="x" * 40,
            )

    @pytest.mark.parametrize("url", PAGES)
    def test_size(self, client, user, callsign, full_log, url):
        client.force_login(user)
        response = client.get(url)
        assert response.status_code == 200
        compressed = len(gzip.compress(response.content))
        assert compressed <= MAX_COMPRESSED_BYTES, f"{url}: {compressed}"
        elements = BeautifulSoup(response.content, "html.parser").find_all(True)
        assert len(elements) <= MAX_ELEMENTS

    def test_whats_downloaded_once(self):
        """The CSS and scripts, compressed, cached after the first page"""
        total = 0
        for name in (
            "vendor/pico-2.1.1.min.css",
            "css/qsology.css",
            "js/app.js",
            "js/log.js",
            "vendor/htmx-4.0.0.esm.min.js",
        ):
            total += len(gzip.compress(Path(finders.find(name)).read_bytes()))
        assert total < 40 * 1024


class TestTemplates:
    def test_all_compile(self):
        engine = engines["django"]
        for template in TEMPLATES:
            relative = template.relative_to(
                template.parents[
                    [p.name for p in template.parents].index("templates")
                ]
            )
            engine.get_template(str(relative))
