import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from apps.locations import importers
from apps.locations.importers import ImportAborted, ImportResult
from apps.locations.models import DxccEntity, Reference

from .conftest import IOTA_SAMPLE

pytestmark = pytest.mark.django_db


def parse(parser, data, **kwargs):
    result = ImportResult("test")
    return list(parser(data, result, **kwargs)), result


class TestParsePota:
    def test_sample(self, pota_csv):
        rows, result = parse(importers.parse_pota, pota_csv)
        assert result.errors == []
        assert [row["code"] for row in rows] == [
            "US-0001",
            "US-0002",
            "US-0003",
            "US-0004",
            "US-0005",
        ]
        assert rows[0] == {
            "code": "US-0001",
            "name": "Acadia National Park",
            "dxcc_code": 291,
            "region": "US-ME",
            "latitude": Decimal("44.31"),
            "longitude": Decimal("-68.2034"),
            "gridsquare": "FN54vh",
            "altitude_m": None,
            "is_active": True,
            "data": {"location_desc": "US-ME"},
        }

    def test_inactive_and_bad_grid(self):
        data = (
            '"reference","name","active","entityId","locationDesc",'
            '"latitude","longitude","grid"\n'
            '"gb-0001","Old Park","0","223","GB-ENG","51.5074","-0.1278",'
            '"nope"\n'
        )
        (row,), _ = parse(importers.parse_pota, data)
        assert row["code"] == "GB-0001"
        assert not row["is_active"]
        # A bad published grid is worked out from the coordinates
        assert row["gridsquare"] == "IO91wm"

    def test_bad_rows_reported_and_skipped(self):
        data = (
            "reference,name,active,entityId,locationDesc,latitude,"
            "longitude,grid\n"
            "US-0001,Good,1,291,US-ME,44.31,-68.2034,FN54vh\n"
            ",No reference,1,291,US-ME,44.31,-68.2034,FN54vh\n"
            "US-0003,Bad latitude,1,291,US-ME,95,-68.2034,\n"
            "US-0004,Not a number,1,291,US-ME,north,-68.2034,\n"
            "US-0005,Good too,1,,US-ME,,,\n"
        )
        rows, result = parse(importers.parse_pota, data)
        assert [row["code"] for row in rows] == ["US-0001", "US-0005"]
        assert len(result.errors) == 3
        assert all(error.startswith("line ") for error in result.errors)
        assert rows[1]["dxcc_code"] is None
        assert rows[1]["gridsquare"] == ""

    def test_bom_and_bad_bytes(self):
        data = (
            "﻿reference,name,active,entityId,locationDesc,latitude,"
            "longitude,grid\nUS-0001,Caf\xe9,1,291,US-ME,1,1,\n"
        ).encode()
        data = data.replace("é".encode(), b"\xff")
        (row,), _ = parse(importers.parse_pota, data)
        assert row["name"] == "Caf�"


class TestParseSota:
    TODAY = date(2026, 10, 20)

    def test_sample_skips_title_line(self, sota_csv):
        rows, result = parse(importers.parse_sota, sota_csv, today=self.TODAY)
        assert result.errors == []
        assert len(rows) == 4
        first = rows[0]
        assert first["code"] == "3B8/MU-001"
        assert first["name"] == (
            "Piton de la Petite Rivière Noire (Black River Peak)"
        )
        assert first["region"] == "Mauritius"
        assert first["latitude"] == Decimal("-20.40882")
        assert first["longitude"] == Decimal("57.40771")
        assert first["altitude_m"] == 828
        assert first["is_active"]
        assert first["data"] == {
            "region_name": "Mauritius",
            "points": 10,
            "bonus_points": 0,
            "valid_from": "2026-08-01",
            "valid_to": "2099-12-31",
        }

    def test_without_title_line(self, sota_csv):
        data = sota_csv.split(b"\n", 1)[1]
        rows, _ = parse(importers.parse_sota, data, today=self.TODAY)
        assert len(rows) == 4

    @pytest.mark.parametrize(
        ("valid_from", "valid_to", "active"),
        [
            ("01/01/2020", "31/12/2099", True),
            ("01/01/2020", "20/10/2026", True),  # Last day
            ("01/01/2020", "19/10/2026", False),  # Retired
            ("21/10/2026", "31/12/2099", False),  # Not valid yet
            ("", "", True),
        ],
    )
    def test_valid_dates(self, valid_from, valid_to, active):
        data = (
            "SummitCode,AssociationName,RegionName,SummitName,AltM,AltFt,"
            "GridRef1,GridRef2,Longitude,Latitude,Points,BonusPoints,"
            "ValidFrom,ValidTo\n"
            f"G/LD-001,England,Lake District,Scafell Pike,978,3209,,,"
            f"-3.211528,54.454222,10,3,{valid_from},{valid_to}\n"
        )
        (row,), _ = parse(importers.parse_sota, data, today=self.TODAY)
        assert row["is_active"] is active
        assert row["gridsquare"] == "IO84jk"

    def test_bad_rows_reported(self):
        data = (
            "SummitCode,AssociationName,RegionName,SummitName,AltM,AltFt,"
            "GridRef1,GridRef2,Longitude,Latitude,Points,BonusPoints,"
            "ValidFrom,ValidTo\n"
            "G/LD-001,E,LD,Scafell,978,0,,,-3.2,54.4,10,0,bad date,\n"
            "G/LD-002,E,LD,Helvellyn,950,0,,,-3.0,54.5,10,0,,\n"
        )
        rows, result = parse(importers.parse_sota, data, today=self.TODAY)
        assert [row["code"] for row in rows] == ["G/LD-002"]
        assert len(result.errors) == 1


class TestParseIota:
    def test_sample(self, iota_json):
        rows, result = parse(importers.parse_iota, iota_json)
        assert result.errors == []
        assert [row["code"] for row in rows] == ["AF-001", "EU-005", "OC-999"]

    def test_centre_with_swapped_latitudes(self, iota_json):
        agalega, *_ = parse(importers.parse_iota, iota_json)[0]
        assert agalega["latitude"] == Decimal("-10.375")
        assert agalega["longitude"] == Decimal("56.625")
        assert agalega["dxcc_code"] == 4
        assert agalega["gridsquare"] == "LH89hp"
        assert agalega["data"]["sub_groups"][0]["islands"][0][
            "island_name"
        ] == ("North")

    def test_centre_across_the_antimeridian(self, iota_json):
        *_, dateline = parse(importers.parse_iota, iota_json)[0]
        assert dateline["longitude"] == Decimal("-180")
        assert dateline["latitude"] == Decimal("-16.5")
        assert dateline["dxcc_code"] is None

    def test_antimeridian_centre_east_of_it(self):
        group = dict(
            IOTA_SAMPLE[2], longitude_min="178.00", longitude_max="-179.00"
        )
        (row,), _ = parse(importers.parse_iota, json.dumps([group]))
        assert row["longitude"] == Decimal("179.5")

    def test_active(self, iota_json):
        rows, _ = parse(importers.parse_iota, iota_json)
        # No sub groups counts as active, all deleted doesn't
        assert [row["is_active"] for row in rows] == [True, True, False]

    def test_missing_bounds(self):
        group = dict(IOTA_SAMPLE[0], latitude_min="", latitude_max="")
        (row,), _ = parse(importers.parse_iota, json.dumps([group]))
        assert row["latitude"] is None
        assert row["longitude"] is None
        assert row["gridsquare"] == ""

    def test_bad_groups_reported(self):
        groups = [
            IOTA_SAMPLE[0],
            {"name": "No reference"},
            dict(IOTA_SAMPLE[1], latitude_min="north"),
            "not a group",
        ]
        rows, result = parse(importers.parse_iota, json.dumps(groups))
        assert [row["code"] for row in rows] == ["AF-001"]
        assert len(result.errors) == 3

    def test_not_a_list(self):
        with pytest.raises(ImportAborted):
            parse(importers.parse_iota, b'{"refno": "AF-001"}')


class TestParseDxcc:
    def test_plain_and_gzipped_match(self, tqsl_xml, tqsl_gz):
        assert (
            parse(importers.parse_dxcc, tqsl_xml)[0]
            == (parse(importers.parse_dxcc, tqsl_gz)[0])
        )

    def test_entities(self, tqsl_gz):
        rows, result = parse(importers.parse_dxcc, tqsl_gz)
        assert result.errors == []
        by_code = {row["code"]: row for row in rows}
        assert sorted(by_code) == [0, 1, 2, 223, 291]
        assert by_code[223] == {
            "code": 223,
            "name": "ENGLAND",
            "is_deleted": False,
            "valid_from": datetime(1945, 11, 15, tzinfo=UTC),
            "valid_until": None,
            "cq_zones": [14],
            "itu_zones": [27],
        }

    def test_zonemap_is_itu_then_cq(self, tqsl_gz):
        rows, _ = parse(importers.parse_dxcc, tqsl_gz)
        canada = next(row for row in rows if row["code"] == 1)
        assert canada["itu_zones"] == [2, 3, 9]
        assert canada["cq_zones"] == [1, 3, 5]

    def test_deleted(self, tqsl_gz):
        rows, _ = parse(importers.parse_dxcc, tqsl_gz)
        abu_ail = next(row for row in rows if row["code"] == 2)
        assert abu_ail["is_deleted"]
        assert abu_ail["valid_until"] == datetime(1991, 3, 31, tzinfo=UTC)

    def test_no_zones(self, tqsl_gz):
        rows, _ = parse(importers.parse_dxcc, tqsl_gz)
        none = next(row for row in rows if row["code"] == 0)
        assert none["cq_zones"] == []

    def test_bad_entities_reported(self):
        data = (
            "<tqsldata><dxcc>"
            '<entity arrlId="223" zonemap="27:14">ENGLAND</entity>'
            '<entity arrlId="x">BAD ID</entity>'
            '<entity arrlId="9" zonemap="27">BAD ZONES</entity>'
            '<entity arrlId="8" valid="yesterday">BAD DATE</entity>'
            "</dxcc></tqsldata>"
        )
        rows, result = parse(importers.parse_dxcc, data)
        assert [row["code"] for row in rows] == [223]
        assert len(result.errors) == 3

    def test_not_xml(self):
        with pytest.raises(ImportAborted, match="XML"):
            parse(importers.parse_dxcc, b"<tqsldata>")


class TestSaveReferences:
    def test_creates(self, pota_csv):
        result = importers.import_data("pota", pota_csv)
        assert (result.created, result.updated, result.deactivated) == (5, 0, 0)
        assert result.total == 5
        assert Reference.objects.program("pota").count() == 5
        park = Reference.objects.get(code="US-0001")
        assert park.name == "Acadia National Park"
        assert park.gridsquare == "FN54vh"

    def test_reimport_is_idempotent(self, pota_csv):
        importers.import_data("pota", pota_csv)
        before = list(
            Reference.objects.values_list("pk", "code", "name", "is_active")
        )
        result = importers.import_data("pota", pota_csv)
        assert (result.created, result.updated, result.deactivated) == (0, 5, 0)
        after = list(
            Reference.objects.values_list("pk", "code", "name", "is_active")
        )
        assert before == after

    def test_updates_changed_reference(self, pota_csv):
        importers.import_data("pota", pota_csv)
        renamed = pota_csv.replace(b"Acadia National Park", b"Acadia NP")
        importers.import_data("pota", renamed)
        assert Reference.objects.get(code="US-0001").name == "Acadia NP"

    def test_missing_references_made_inactive(self, pota_csv):
        importers.import_data("pota", pota_csv)
        lines = pota_csv.splitlines(keepends=True)
        result = importers.import_data("pota", b"".join(lines[:-1]))
        assert result.deactivated == 1
        badlands = Reference.objects.get(code="US-0005")
        assert not badlands.is_active
        # Never deleted, locations and QSOs can still point at it
        assert Reference.objects.count() == 5

    def test_returning_reference_made_active(self, pota_csv):
        lines = pota_csv.splitlines(keepends=True)
        importers.import_data("pota", pota_csv)
        importers.import_data("pota", b"".join(lines[:-1]))
        importers.import_data("pota", pota_csv)
        assert Reference.objects.get(code="US-0005").is_active

    def test_other_programmes_untouched(self, pota_csv, summit):
        importers.import_data("pota", pota_csv)
        summit.refresh_from_db()
        assert summit.is_active

    def test_short_list_aborts_without_changes(self, pota_csv):
        importers.import_data("pota", pota_csv)
        lines = pota_csv.splitlines(keepends=True)
        renamed = b"".join(lines[:3]).replace(b"Acadia National", b"Changed")
        before = list(
            Reference.objects.values_list("code", "name", "is_active")
        )

        with pytest.raises(ImportAborted, match="incomplete"):
            importers.import_data("pota", renamed)

        after = list(Reference.objects.values_list("code", "name", "is_active"))
        assert before == after

    def test_empty_list_aborts(self, pota_csv):
        importers.import_data("pota", pota_csv)
        with pytest.raises(ImportAborted):
            importers.import_data("pota", pota_csv.splitlines()[0])
        assert Reference.objects.active().count() == 5

    def test_first_load_of_anything_is_fine(self, pota_csv):
        header = pota_csv.splitlines()[0]
        result = importers.import_data("pota", header)
        assert result.total == 0

    def test_duplicate_codes_reported(self, pota_csv):
        lines = pota_csv.splitlines(keepends=True)
        result = importers.import_data("pota", pota_csv + lines[1])
        assert result.created == 5
        assert result.errors == ["US-0001: listed more than once"]

    def test_bad_rows_dont_stop_the_rest(self, pota_csv):
        result = importers.import_data(
            "pota", pota_csv + b"US-0009,Bad,1,291,US-ME,999,0,\n"
        )
        assert result.created == 5
        assert len(result.errors) == 1

    def test_batches(self, pota_csv, monkeypatch):
        monkeypatch.setattr(importers, "BATCH_SIZE", 2)
        importers.import_data("pota", pota_csv)
        lines = pota_csv.splitlines(keepends=True)
        result = importers.import_data("pota", b"".join(lines[:-1]))
        assert Reference.objects.count() == 5
        assert result.deactivated == 1

    def test_sota_and_iota(self, sota_csv, iota_json):
        assert importers.import_data("sota", sota_csv).created == 4
        assert importers.import_data("iota", iota_json).created == 3
        assert Reference.objects.lookup("iota", "EU-005").name == (
            "Great Britain"
        )

    def test_str(self, pota_csv):
        result = importers.import_data("pota", pota_csv)
        assert str(result) == (
            "pota: 5 created, 0 updated, 0 deactivated, "
            "5 public locations, 0 renamed, 0 errors"
        )

    def test_unknown_source(self):
        with pytest.raises(ValueError, match="Unknown source"):
            importers.import_data("wwff", b"")


class TestSaveDxcc:
    def test_creates_and_updates(self, tqsl_gz, england):
        england.name = "OLD NAME"
        england.save()
        result = importers.import_data("dxcc", tqsl_gz)
        assert (result.created, result.updated) == (4, 1)
        england.refresh_from_db()
        assert england.name == "ENGLAND"
        assert DxccEntity.objects.count() == 5

    def test_reimport(self, tqsl_gz):
        importers.import_data("dxcc", tqsl_gz)
        result = importers.import_data("dxcc", tqsl_gz)
        assert (result.created, result.updated) == (0, 5)
        assert DxccEntity.objects.count() == 5

    def test_entities_never_removed(self, tqsl_gz):
        importers.import_data("dxcc", tqsl_gz)
        importers.import_data(
            "dxcc",
            '<tqsldata><dxcc><entity arrlId="223">ENGLAND</entity>'
            "</dxcc></tqsldata>",
        )
        assert DxccEntity.objects.count() == 5

    def test_empty_aborts(self):
        with pytest.raises(ImportAborted):
            importers.import_data("dxcc", "<tqsldata/>")


class TestRefresh:
    def test_downloads_from_the_source(self, settings, pota_csv, fake_fetch):
        settings.REFERENCE_SOURCES = {"pota": "https://pota.test/parks.csv"}
        fetch = fake_fetch(pota_csv)
        result = importers.refresh("pota", fetch=fetch)
        assert result.created == 5
        assert fetch.calls[0]["url"] == "https://pota.test/parks.csv"

    def test_uses_http_by_default(self, monkeypatch, tqsl_gz, fake_fetch):
        fetch = fake_fetch(tqsl_gz)
        monkeypatch.setattr("apps.common.http.get", fetch)
        assert importers.refresh("dxcc").created == 5
        assert len(fetch.calls) == 1
