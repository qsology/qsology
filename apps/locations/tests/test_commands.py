from io import StringIO

import pytest
from apps.locations import importers, tasks
from apps.locations.importers import ImportAborted
from apps.locations.models import DxccEntity, Reference
from django.core.management import CommandError, call_command

from .conftest import DATA

pytestmark = pytest.mark.django_db


def run(*args):
    out, err = StringIO(), StringIO()
    call_command("import_references", *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


class TestImportReferencesCommand:
    def test_from_file(self):
        out, _ = run("pota", "--file", str(DATA / "pota_sample.csv"))
        assert "pota: 5 created" in out
        assert Reference.objects.count() == 5

    def test_file_needs_one_source(self):
        with pytest.raises(CommandError, match="exactly one"):
            run("pota", "sota", "--file", str(DATA / "pota_sample.csv"))

    def test_file_needs_a_source(self):
        with pytest.raises(CommandError, match="exactly one"):
            run("--file", str(DATA / "pota_sample.csv"))

    def test_missing_file(self, tmp_path):
        with pytest.raises(CommandError, match="pota"):
            run("pota", "--file", str(tmp_path / "missing.csv"))

    def test_unknown_source(self):
        with pytest.raises(CommandError):
            run("wwff")

    def test_errors_are_shown(self, tmp_path):
        path = tmp_path / "parks.csv"
        path.write_bytes(
            (DATA / "pota_sample.csv").read_bytes()
            + b"US-0009,Bad,1,291,US-ME,999,0,\n"
        )
        out, err = run("pota", "--file", str(path))
        assert "1 errors" in out
        assert "out of range" in err

    def test_aborted_import(self, tmp_path):
        run("pota", "--file", str(DATA / "pota_sample.csv"))
        path = tmp_path / "parks.csv"
        path.write_bytes((DATA / "pota_sample.csv").read_bytes()[:200])
        with pytest.raises(CommandError, match="incomplete"):
            run("pota", "--file", str(path))

    def test_downloads_every_source(self, monkeypatch):
        refreshed = []

        def refresh(source):
            refreshed.append(source)
            return importers.ImportResult(source, created=1)

        monkeypatch.setattr(importers, "refresh", refresh)
        out, _ = run()
        assert refreshed == list(importers.SOURCES)
        assert out.count("1 created") == 4

    def test_downloads_chosen_source(
        self, monkeypatch, fake_fetch, tqsl_gz, settings
    ):
        fetch = fake_fetch(tqsl_gz)
        monkeypatch.setattr("apps.common.http.get", fetch)
        out, _ = run("dxcc")
        assert "dxcc: 5 created" in out
        assert fetch.calls[0]["url"] == settings.REFERENCE_SOURCES["dxcc"]
        assert DxccEntity.objects.count() == 5


class TestRefreshReferencesTask:
    def test_one_failure_doesnt_stop_the_rest(self, monkeypatch):
        def refresh(source):
            if source == "sota":
                raise ImportAborted("sota list looks short")
            return importers.ImportResult(source, created=2)

        monkeypatch.setattr(importers, "refresh", refresh)
        results = tasks.refresh_references.delay().get()

        assert set(results) == set(importers.SOURCES)
        assert results["sota"] == "failed: sota list looks short"
        assert results["pota"].startswith("pota: 2 created")
        assert results["dxcc"].startswith("dxcc: 2 created")
