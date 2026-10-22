"""Importing files, every record must end up somewhere"""

import json
from decimal import Decimal

import pytest
from apps.logbook import exports, importing
from apps.logbook.models import (
    QSO,
    ImportBatch,
    ImportFormat,
    ImportRecord,
    ImportRecordStatus,
    ProtectedOperation,
    ReviewStatus,
)
from apps.logbook.sanity import Code

pytestmark = pytest.mark.django_db

GOOD = (
    "<CALL:5>K1ABC <QSO_DATE:8>20240601 <TIME_ON:4>1200 <FREQ:6>14.074 "
    "<MODE:3>FT8 <STATION_CALLSIGN:5>M0WME <EOR>\n"
)
# Band and frequency disagree, a warning
WARNING = (
    "<CALL:5>K2ABC <QSO_DATE:8>20240601 <TIME_ON:4>1300 <FREQ:5>7.074 "
    "<BAND:3>20m <MODE:3>FT8 <STATION_CALLSIGN:5>M0WME <EOR>\n"
)
# No time at all, can't be logged
BROKEN = "<CALL:5>K3ABC <QSO_DATE:8>20240601 <MODE:2>CW <EOR>\n"


def adi_file(*records: str) -> str:
    return "Test file\n<ADIF_VER:5>3.1.5 <EOH>\n" + "".join(records)


def accounted_for(batch: ImportBatch) -> bool:
    """Every record in the file is a QSO or kept as an ImportRecord"""
    kept = batch.records.exclude(status=ImportRecordStatus.LOGGED).count()
    logged_with_notes = batch.records.filter(
        status=ImportRecordStatus.LOGGED
    ).count()
    clean = batch.qsos.count() - logged_with_notes
    return clean + logged_with_notes + kept == batch.total


class TestImportADI:
    def test_logs_good_records(self, user, station):
        batch = importing.import_file(user, adi_file(GOOD), filename="a.adi")
        assert (batch.total, batch.logged) == (1, 1)
        qso = QSO.objects.get()
        assert qso.call == "K1ABC"
        assert qso.import_batch == batch
        assert qso.source_record == GOOD.strip()
        assert qso.revisions.get().reason == "Imported from a.adi"
        assert batch.header == {"ADIF_VER": "3.1.5"}
        assert batch.finished_at is not None
        assert accounted_for(batch)

    def test_keeps_the_original_file(self, user, station):
        text = adi_file(GOOD, WARNING, BROKEN)
        batch = importing.import_file(user, text)
        assert batch.original == text

    def test_warnings_are_held(self, user, station):
        batch = importing.import_file(user, adi_file(GOOD, WARNING))
        assert (batch.logged, batch.held) == (1, 1)
        held = batch.records.get()
        assert held.status == ImportRecordStatus.HELD
        assert held.raw == WARNING.strip()
        assert held.data["CALL"] == "K2ABC"
        assert held.issues[0]["code"] == Code.BAND_FREQ_MISMATCH
        assert accounted_for(batch)

    def test_warnings_accepted(self, user, station):
        batch = importing.import_file(
            user, adi_file(GOOD, WARNING), accept_warnings=True
        )
        assert (batch.logged, batch.held) == (2, 0)
        assert QSO.objects.needs_review().get().call == "K2ABC"

    def test_broken_records_are_kept(self, user, station):
        batch = importing.import_file(user, adi_file(GOOD, BROKEN))
        assert (batch.logged, batch.failed) == (1, 1)
        failed = batch.records.get()
        assert failed.status == ImportRecordStatus.FAILED
        assert failed.raw == BROKEN.strip()
        assert "time_on" in failed.error.lower()
        assert accounted_for(batch)

    def test_unreadable_values_are_kept_and_held(self, user, station):
        record = GOOD.replace("<FREQ:6>14.074", "<FREQ:4>fast")
        batch = importing.import_file(user, adi_file(record))
        held = batch.records.get()
        assert held.status == ImportRecordStatus.HELD
        assert {issue["code"] for issue in held.issues} >= {
            Code.UNREADABLE_VALUE
        }

        batch = importing.import_file(
            user, adi_file(record), accept_warnings=True
        )
        qso = batch.qsos.get()
        assert qso.extra["FREQ"] == "fast"
        assert qso.review_status == ReviewStatus.NEEDS_REVIEW

    def test_reading_problems_are_noted_on_logged_records(self, user, station):
        record = GOOD.replace("<EOR>", "<MODE:2>CW <EOR>")
        batch = importing.import_file(user, adi_file(record))
        assert batch.logged == 1
        note = batch.records.get()
        assert note.status == ImportRecordStatus.LOGGED
        assert note.qso == QSO.objects.get()
        assert "MODE appears more than once" in note.error
        assert accounted_for(batch)

    def test_records_dont_affect_each_other(self, user, station):
        records = [
            GOOD.replace("1200", f"{hour:02d}00").replace(
                "K1ABC", f"K{hour % 10}XY{chr(65 + hour)}"
            )
            for hour in range(10)
        ]
        records.insert(3, BROKEN)
        records.insert(6, WARNING)
        batch = importing.import_file(user, adi_file(*records))
        assert (batch.total, batch.logged, batch.held, batch.failed) == (
            12,
            10,
            1,
            1,
        )
        indexes = sorted(batch.records.values_list("index", flat=True))
        assert indexes == [3, 6]

    def test_header_only(self, user):
        batch = importing.import_file(user, "header <EOH>")
        assert batch.total == 0
        assert batch.original == "header <EOH>"


class TestDuplicates:
    def test_reimporting_an_export_logs_nothing_new(self, user, log):
        for _ in range(3):
            log()
        for format in ImportFormat.values:
            text = exports.export_text(QSO.objects.all(), format)
            batch = importing.import_file(user, text, format=format)
            assert (batch.total, batch.duplicates, batch.logged) == (3, 3, 0)
        assert QSO.all_objects.count() == 3

    def test_duplicates_point_at_the_qso(self, user, qso):
        text = exports.export_text(QSO.objects.all(), "adi")
        record = importing.import_file(user, text).records.get()
        assert record.status == ImportRecordStatus.DUPLICATE
        assert record.qso == qso

    def test_reimporting_a_deleted_qso_doesnt_restore_it(self, user, qso):
        text = exports.export_text(QSO.objects.all(), "json")
        qso.delete()
        batch = importing.import_file(user, text)
        assert batch.duplicates == 1
        assert not QSO.objects.exists()

    def test_another_users_export_is_logged_fresh(self, user, other_user, qso):
        text = exports.export_text(QSO.objects.all(), "adx")
        batch = importing.import_file(other_user, text, accept_warnings=True)
        assert batch.logged == 1
        theirs = QSO.objects.get(owner=other_user)
        assert theirs.uuid != qso.uuid
        assert theirs.call == qso.call

    def test_bad_uuid_is_ignored(self, user, station):
        record = GOOD.replace("<EOR>", "<APP_QSOLOGY_UUID:4>nope <EOR>")
        batch = importing.import_file(user, adi_file(record))
        assert batch.logged == 1


class TestUnreadableFiles:
    @pytest.mark.parametrize(
        ("format", "text"),
        [
            ("adx", "<ADX><RECORDS><RECORD>"),
            ("json", '{"format": "qsology", "version": 1, "qsos": [{'),
            ("json", '{"format": "something else"}'),
        ],
    )
    def test_whole_file_is_kept(self, user, format, text):
        batch = importing.import_file(user, text, format=format)
        assert (batch.total, batch.failed) == (1, 1)
        record = batch.records.get()
        assert record.raw == text
        assert record.error
        assert batch.original == text


class TestEncodings:
    def test_utf8(self, user, station):
        text = adi_file(GOOD.replace("<EOR>", "<NAME:5>Józef <EOR>"))
        batch = importing.import_file(user, text.encode("utf-8"))
        assert batch.qsos.get().name == "Józef"

    def test_utf8_with_bom(self, user, station):
        content = b"\xef\xbb\xbf" + adi_file(GOOD).encode()
        assert importing.import_file(user, content).logged == 1

    def test_windows_1252(self, user, station):
        text = adi_file(GOOD.replace("<EOR>", "<NAME:5>Józef <EOR>"))
        batch = importing.import_file(user, text.encode("cp1252"))
        assert batch.qsos.get().name == "Józef"

    def test_anything_decodes(self):
        assert importing.decode(bytes(range(256)))


class TestGuessFormat:
    @pytest.mark.parametrize(
        ("filename", "text", "expected"),
        [
            ("log.ADI", "", "adi"),
            ("log.adif", "", "adi"),
            ("log.adx", "", "adx"),
            ("log.json", "", "json"),
            ("", '﻿  {"format": "qsology"}', "json"),
            ("", "<?xml version='1.0'?><ADX/>", "adx"),
            ("", "<adx></adx>", "adx"),
            ("", "Some header <EOH>", "adi"),
            ("", "<CALL:1>X<EOR>", "adi"),
        ],
    )
    def test_guess(self, filename, text, expected):
        assert importing.guess_format(filename, text) == expected


class TestJSONImport:
    def document(self, *qsos):
        return json.dumps({"format": "qsology", "version": 1, "qsos": qsos})

    def test_logs(self, user, station):
        batch = importing.import_file(
            user,
            self.document(
                {
                    "call": "K1ABC",
                    "time_on": "2024-06-01T12:00:00Z",
                    "freq": 14.074,
                    "mode": "FT8",
                    "dxcc": "291",
                    "station_callsign": "M0WME",
                    "extra": {"QSLMSG": "TNX", "N": 5, "EMPTY": ""},
                }
            ),
        )
        qso = batch.qsos.get()
        assert qso.freq == Decimal("14.074")
        assert qso.dxcc == 291
        assert qso.extra == {"QSLMSG": "TNX", "N": "5"}

    def test_unknown_keys_are_kept(self, user, station):
        batch = importing.import_file(
            user,
            self.document(
                {
                    "call": "K1ABC",
                    "time_on": "2024-06-01T12:00:00Z",
                    "band": "20m",
                    "mode": "FT8",
                    "station_callsign": "M0WME",
                    "colour": "blue",
                }
            ),
        )
        assert batch.qsos.get().extra == {"COLOUR": "blue"}
        assert "colour" in batch.records.get().error

    def test_bad_values_are_kept(self, user, station):
        document = self.document(
            {
                "call": "K1ABC",
                "time_on": "2024-06-01T12:00:00Z",
                "freq": "fast",
                "band": "20m",
                "mode": "FT8",
                "station_callsign": "M0WME",
            }
        )
        held = importing.import_file(user, document).records.get()
        assert held.status == ImportRecordStatus.HELD
        batch = importing.import_file(user, document, accept_warnings=True)
        assert batch.qsos.get().extra == {"FREQ": "fast"}

    def test_naive_time_fails(self, user, station):
        batch = importing.import_file(
            user,
            self.document({"call": "K1ABC", "time_on": "2024-06-01T12:00"}),
        )
        assert batch.failed == 1

    def test_non_object_record_fails(self, user):
        batch = importing.import_file(user, self.document("K1ABC"))
        record = batch.records.get()
        assert record.status == ImportRecordStatus.FAILED
        assert record.raw == '"K1ABC"'


class TestRetry:
    def test_held_record_can_be_accepted(self, user, station):
        batch = importing.import_file(user, adi_file(WARNING))
        record = importing.retry_record(batch.records.get())
        assert record.status == ImportRecordStatus.LOGGED
        assert record.qso.call == "K2ABC"
        assert record.qso.review_status == ReviewStatus.NEEDS_REVIEW
        assert record.qso.import_batch == batch
        batch.refresh_from_db()
        assert (batch.logged, batch.held) == (1, 0)

    def test_retry_without_accepting_stays_held(self, user, station):
        batch = importing.import_file(user, adi_file(WARNING))
        record = importing.retry_record(
            batch.records.get(), accept_warnings=False
        )
        assert record.status == ImportRecordStatus.HELD
        batch.refresh_from_db()
        assert batch.held == 1

    def test_failed_record_still_fails(self, user, station):
        batch = importing.import_file(user, adi_file(BROKEN))
        record = importing.retry_record(batch.records.get())
        assert record.status == ImportRecordStatus.FAILED
        batch.refresh_from_db()
        assert batch.failed == 1

    def test_retry_after_it_was_logged_another_way(self, user, station):
        text = adi_file(
            WARNING.replace(
                "<EOR>",
                "<APP_QSOLOGY_UUID:36>12345678-1234-5678-1234-567812345678 "
                "<EOR>",
            )
        )
        held = importing.import_file(user, text).records.get()
        importing.import_file(user, text, accept_warnings=True)
        record = importing.retry_record(held)
        assert record.status == ImportRecordStatus.DUPLICATE
        assert QSO.objects.count() == 1
        batch = held.batch
        batch.refresh_from_db()
        assert (batch.held, batch.duplicates) == (0, 1)

    def test_logged_records_are_left_alone(self, user, station):
        record = ImportRecord(status=ImportRecordStatus.LOGGED)
        assert importing.retry_record(record) is record


class TestBatchDeletion:
    def test_cant_delete_with_records_waiting(self, user, station):
        batch = importing.import_file(user, adi_file(WARNING))
        with pytest.raises(ProtectedOperation):
            batch.delete()

    def test_can_delete_once_done_and_qsos_stay(self, user, station):
        batch = importing.import_file(user, adi_file(GOOD, WARNING))
        importing.retry_record(batch.records.get())
        batch.delete()
        assert QSO.objects.count() == 2
