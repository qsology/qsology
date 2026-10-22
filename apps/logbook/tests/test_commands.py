import io

import pytest
from apps.logbook import exports
from apps.logbook.formats import adi
from apps.logbook.models import QSO, Logbook
from django.core.management import CommandError, call_command

pytestmark = pytest.mark.django_db


class TestExportLog:
    def test_to_stdout(self, user, qso, capsys):
        call_command("export_log", "m0wme", "--format", "json")
        assert str(qso.uuid) in capsys.readouterr().out

    def test_to_file(self, user, qso, tmp_path):
        path = tmp_path / "log.adi"
        stderr = io.StringIO()
        call_command("export_log", "m0wme", "--output", path, stderr=stderr)
        assert len(adi.parse(path.read_text("utf-8")).records) == 1
        assert "Exported 1 QSOs" in stderr.getvalue()

    def test_logbook(self, user, log, tmp_path):
        log(mode="CW", rst_sent="599", rst_rcvd="599")
        log()
        logbook = Logbook(owner=user, name="CW")
        logbook.set_query("mode:CW")
        logbook.save()
        path = tmp_path / "cw.adx"
        call_command(
            "export_log",
            "m0wme",
            "--format",
            "adx",
            "--logbook",
            "CW",
            "--output",
            path,
            stderr=io.StringIO(),
        )
        assert path.read_text("utf-8").count("<RECORD>") == 1

    def test_unknown_user(self, db):
        with pytest.raises(CommandError, match="No user"):
            call_command("export_log", "nobody")

    def test_unknown_logbook(self, user):
        with pytest.raises(CommandError, match="No logbook"):
            call_command("export_log", "m0wme", "--logbook", "Nope")


class TestImportLog:
    def test_import(self, user, station, log, other_user, tmp_path):
        log()
        path = tmp_path / "log.adi"
        path.write_text(exports.export_text(QSO.objects.all(), "adi"))
        stdout = io.StringIO()
        call_command(
            "import_log",
            "k4gbi",
            str(path),
            "--accept-warnings",
            stdout=stdout,
        )
        assert "1 records, 1 logged" in stdout.getvalue()
        assert QSO.objects.filter(owner=other_user).count() == 1

    def test_held_without_accepting(self, user, log, other_user, tmp_path):
        log()
        path = tmp_path / "log.json"
        path.write_text(exports.export_text(QSO.objects.all(), "json"))
        stdout = io.StringIO()
        call_command("import_log", "k4gbi", str(path), stdout=stdout)
        assert "1 held for review" in stdout.getvalue()

    def test_missing_file(self, user, tmp_path):
        with pytest.raises(CommandError):
            call_command("import_log", "m0wme", str(tmp_path / "nope.adi"))

    def test_unknown_user(self, db, tmp_path):
        with pytest.raises(CommandError, match="No user"):
            call_command("import_log", "nobody", str(tmp_path))
