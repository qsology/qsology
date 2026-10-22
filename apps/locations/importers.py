"""Load the reference lists published by POTA, SOTA, IOTA and the ARRL

Each source has a parser, which turns the published file into rows, and the
rows are then saved. Rows that can't be read are skipped and reported, they
never stop the rest of the list from loading. Saving is an upsert, so the same
list can be loaded again safely, and anything that has dropped off the list is
made inactive rather than deleted"""

import csv
import gzip
import io
import json
import logging
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from apps.common import http
from apps.locations import maidenhead
from apps.locations.models import (
    DxccEntity,
    Location,
    Reference,
    ReferenceProgram,
    public_name,
)
from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

BATCH_SIZE = 2000
# A list shorter than this fraction of what is loaded already is probably a
# failed or partial download, so nothing is made inactive from it
MIN_LIST_FRACTION = 0.5

SOURCES = ("dxcc", "pota", "sota", "iota")


class ImportAborted(Exception):
    """The list looked wrong, so nothing was changed"""


@dataclass
class ImportResult:
    source: str
    created: int = 0
    updated: int = 0
    deactivated: int = 0
    public_locations: int = 0
    # Private locations given their owner's callsign, as a public location
    # now has their name
    renamed_locations: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.created + self.updated

    def __str__(self) -> str:
        return (
            f"{self.source}: {self.created} created, {self.updated} updated, "
            f"{self.deactivated} deactivated, "
            f"{self.public_locations} public locations, "
            f"{self.renamed_locations} renamed, "
            f"{len(self.errors)} errors"
        )


def _decode(data: bytes | str) -> str:
    if isinstance(data, str):
        return data
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    # A bad byte shouldn't lose the whole list
    return data.decode("utf-8-sig", errors="replace")


def _decimal(value: str | None, low: int, high: int) -> Decimal | None:
    if value is None or not value.strip():
        return None
    try:
        number = Decimal(value.strip()).quantize(Decimal("0.000001"))
    except InvalidOperation as exc:
        raise ValueError(f"{value!r} is not a number") from exc
    if not number.is_finite() or not low <= number <= high:
        raise ValueError(f"{value!r} is out of range")
    return number


def _int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    return int(Decimal(value.strip()))


def _grid(value: str | None, latitude, longitude) -> str:
    """Use the published grid if it's valid, otherwise work it out"""
    value = (value or "").strip()
    if maidenhead.is_valid_grid(value):
        return maidenhead.normalize_grid(value)
    if latitude is not None and longitude is not None:
        return maidenhead.latlon_to_grid(latitude, longitude)
    return ""


def _sota_date(value: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    return datetime.strptime(value, "%d/%m/%Y").date()


def _rows(
    records: Iterable[tuple[str, dict]],
    parse,
    result: ImportResult,
) -> Iterator[dict]:
    """Parse each record, reporting the ones that can't be read"""
    for label, record in records:
        try:
            row = parse(record)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            result.errors.append(f"{label}: {exc}")
            continue
        if row is not None:
            yield row


def parse_pota(data: bytes | str, result: ImportResult) -> Iterator[dict]:
    """Rows from the POTA all_parks_ext.csv list"""
    reader = csv.DictReader(io.StringIO(_decode(data)))

    def parse(record):
        code = record["reference"].strip().upper()
        if not code:
            raise ValueError("no reference")
        latitude = _decimal(record.get("latitude"), -90, 90)
        longitude = _decimal(record.get("longitude"), -180, 180)
        location = (record.get("locationDesc") or "").strip()
        return {
            "code": code,
            "name": record["name"].strip()[:255],
            "dxcc_code": _int(record.get("entityId")),
            "region": location[:100],
            "latitude": latitude,
            "longitude": longitude,
            "gridsquare": _grid(record.get("grid"), latitude, longitude),
            "altitude_m": None,
            "is_active": (record.get("active") or "").strip() == "1",
            "data": {"location_desc": location},
        }

    records = ((f"line {reader.line_num}", record) for record in reader)
    return _rows(records, parse, result)


def parse_sota(
    data: bytes | str, result: ImportResult, today: date | None = None
) -> Iterator[dict]:
    """Rows from the SOTA summitslist.csv list

    The file starts with a title line before the header, which is skipped"""
    today = today or timezone.now().date()
    lines = io.StringIO(_decode(data))
    position = lines.tell()
    first = lines.readline()
    if first.startswith("SummitCode"):
        lines.seek(position)
    reader = csv.DictReader(lines)

    def parse(record):
        code = record["SummitCode"].strip().upper()
        if not code:
            raise ValueError("no summit code")
        latitude = _decimal(record.get("Latitude"), -90, 90)
        longitude = _decimal(record.get("Longitude"), -180, 180)
        valid_from = _sota_date(record.get("ValidFrom") or "")
        valid_to = _sota_date(record.get("ValidTo") or "")
        is_active = (valid_from is None or valid_from <= today) and (
            valid_to is None or valid_to >= today
        )
        return {
            "code": code,
            "name": record["SummitName"].strip()[:255],
            "dxcc_code": None,
            "region": (record.get("AssociationName") or "").strip()[:100],
            "latitude": latitude,
            "longitude": longitude,
            "gridsquare": _grid(None, latitude, longitude),
            "altitude_m": _int(record.get("AltM")),
            "is_active": is_active,
            "data": {
                "region_name": (record.get("RegionName") or "").strip(),
                "points": _int(record.get("Points")),
                "bonus_points": _int(record.get("BonusPoints")),
                "valid_from": valid_from.isoformat() if valid_from else None,
                "valid_to": valid_to.isoformat() if valid_to else None,
            },
        }

    records = ((f"line {reader.line_num}", record) for record in reader)
    return _rows(records, parse, result)


def _centre(low: Decimal, high: Decimal, wraps: bool) -> Decimal:
    low, high = sorted((low, high))
    if wraps and high - low > 180:
        # The group crosses the antimeridian, e.g. 179E to 179W
        centre = (low + high + 360) / 2
        if centre >= 180:
            centre -= 360
        return centre.quantize(Decimal("0.000001"))
    return ((low + high) / 2).quantize(Decimal("0.000001"))


def parse_iota(data: bytes | str, result: ImportResult) -> Iterator[dict]:
    """Rows from the IOTA fulllist.json list

    IOTA only gives a box around each group, the centre of it is used"""
    groups = json.loads(_decode(data))
    if not isinstance(groups, list):
        raise ImportAborted("IOTA list is not a list of groups")

    def parse(group):
        code = group["refno"].strip().upper()
        if not code:
            raise ValueError("no reference")
        latitude = longitude = None
        lat_values = (group.get("latitude_min"), group.get("latitude_max"))
        lon_values = (group.get("longitude_min"), group.get("longitude_max"))
        if all(lat_values) and all(lon_values):
            latitude = _centre(
                *(_decimal(value, -90, 90) for value in lat_values),
                wraps=False,
            )
            longitude = _centre(
                *(_decimal(value, -180, 180) for value in lon_values),
                wraps=True,
            )
        sub_groups = group.get("sub_groups") or []
        statuses = {sub.get("status") for sub in sub_groups}
        return {
            "code": code,
            "name": group["name"].strip()[:255],
            "dxcc_code": _int(group.get("dxcc_num")),
            "region": (group.get("grp_region") or "").strip()[:100],
            "latitude": latitude,
            "longitude": longitude,
            "gridsquare": _grid(None, latitude, longitude),
            "altitude_m": None,
            # Groups are active unless every sub group says otherwise
            "is_active": not sub_groups or bool(statuses & {"Active", ""}),
            "data": {
                "latitude_range": [str(value) for value in lat_values],
                "longitude_range": [str(value) for value in lon_values],
                "pc_credited": group.get("pc_credited"),
                "comment": group.get("comment", ""),
                "sub_groups": sub_groups,
            },
        }

    records = ((f"group {index}", group) for index, group in enumerate(groups))
    return _rows(records, parse, result)


REFERENCE_FIELDS = (
    "name",
    "dxcc_code",
    "region",
    "latitude",
    "longitude",
    "gridsquare",
    "altitude_m",
    "is_active",
    "data",
    "updated_at",
)


def save_references(
    program: str, rows: Iterable[dict], result: ImportResult
) -> ImportResult:
    """Upsert the references, making any that weren't in the list inactive"""
    existing = dict(
        Reference.objects.filter(program=program).values_list(
            "code", "is_active"
        )
    )
    now = timezone.now()
    seen: set[str] = set()
    batch: list[Reference] = []

    def flush():
        Reference.objects.bulk_create(
            batch,
            update_conflicts=True,
            unique_fields=["program", "code"],
            update_fields=REFERENCE_FIELDS,
        )
        batch.clear()

    with transaction.atomic():
        for row in rows:
            if row["code"] in seen:
                result.errors.append(f"{row['code']}: listed more than once")
                continue
            seen.add(row["code"])
            if row["code"] in existing:
                result.updated += 1
            else:
                result.created += 1
            batch.append(Reference(program=program, updated_at=now, **row))
            if len(batch) >= BATCH_SIZE:
                flush()
        active = {code for code, is_active in existing.items() if is_active}
        if active and len(seen) < len(active) * MIN_LIST_FRACTION:
            raise ImportAborted(
                f"{program} list has {len(seen)} references but "
                f"{len(active)} are loaded, it may be incomplete"
            )
        if batch:
            flush()
        missing = sorted(active - seen)
        for start in range(0, len(missing), BATCH_SIZE):
            result.deactivated += Reference.objects.filter(
                program=program, code__in=missing[start : start + BATCH_SIZE]
            ).update(is_active=False, updated_at=now)
        if program in PUBLIC_LOCATION_PROGRAMS:
            result.public_locations = sync_public_locations(program)
            result.renamed_locations = Location.qualify_clashing_names()
    logger.info("Imported %s", result)
    return result


# Programmes whose references each get a public location. IOTA references
# are groups of islands, so there's no one place to put a location
PUBLIC_LOCATION_PROGRAMS = (ReferenceProgram.POTA, ReferenceProgram.SOTA)

PUBLIC_LOCATION_FIELDS = (
    "name",
    "full_name",
    "latitude",
    "longitude",
    "gridsquare",
    "altitude_m",
    "dxcc",
    "cq_zone",
    "itu_zone",
    "updated_at",
)


def _public_location(reference: Reference, entities: dict) -> Location:
    entity = entities.get(reference.dxcc_code)
    location = Location(
        owner=None,
        source_reference=reference,
        name=reference.name[:100],
        full_name=public_name(reference),
        latitude=reference.latitude,
        longitude=reference.longitude,
        gridsquare=reference.gridsquare,
        altitude_m=reference.altitude_m,
        dxcc=entity,
    )
    location.fill_zones()
    return location


def sync_public_locations(program: str) -> int:
    """Make or update the public location for every reference in a
    programme, returning how many there are

    Public locations are never removed, a reference dropping off its list
    leaves its location as it was"""
    entities = {entity.code: entity for entity in DxccEntity.objects.all()}
    references = Reference.objects.filter(program=program).order_by("pk")
    count = 0
    with transaction.atomic():
        batch = []
        for reference in references.iterator(chunk_size=BATCH_SIZE):
            batch.append(_public_location(reference, entities))
            if len(batch) >= BATCH_SIZE:
                count += _save_public_locations(batch)
        if batch:
            count += _save_public_locations(batch)
        # Link each location to its reference, as it would be for a user
        Through = Location.references.through
        links = Location.objects.public().filter(
            source_reference__program=program
        )
        rows = links.values_list("pk", "source_reference_id").iterator(
            chunk_size=BATCH_SIZE
        )
        pending = []
        for location_id, reference_id in rows:
            pending.append(
                Through(location_id=location_id, reference_id=reference_id)
            )
            if len(pending) >= BATCH_SIZE:
                Through.objects.bulk_create(pending, ignore_conflicts=True)
                pending.clear()
        Through.objects.bulk_create(pending, ignore_conflicts=True)
    return count


def _save_public_locations(batch: list[Location]) -> int:
    Location.objects.bulk_create(
        batch,
        update_conflicts=True,
        unique_fields=["source_reference"],
        update_fields=PUBLIC_LOCATION_FIELDS,
    )
    count = len(batch)
    batch.clear()
    return count


def _tqsl_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def parse_dxcc(data: bytes | str, result: ImportResult) -> Iterator[dict]:
    """DXCC entities from the TQSL config.tq6 published for LoTW

    The file is gzipped XML, each entity looks like
    <entity arrlId="223" deleted="0" valid="..." zonemap="27:14">ENGLAND"""
    try:
        root = ET.fromstring(_decode(data))
    except ET.ParseError as exc:
        raise ImportAborted(f"TQSL config is not valid XML: {exc}") from exc

    def parse(entity):
        itu_zones, cq_zones = set(), set()
        zonemap = entity.get("zonemap") or ""
        for pair in filter(None, zonemap.split(",")):
            itu, cq = pair.split(":")
            itu_zones.add(int(itu))
            cq_zones.add(int(cq))
        return {
            "code": int(entity.get("arrlId")),
            "name": (entity.text or "").strip()[:100],
            "is_deleted": entity.get("deleted") == "1",
            "valid_from": _tqsl_datetime(entity.get("valid")),
            "valid_until": _tqsl_datetime(entity.get("invalid")),
            "cq_zones": sorted(cq_zones),
            "itu_zones": sorted(itu_zones),
        }

    records = (
        (f"entity {entity.get('arrlId')}", entity)
        for entity in root.iter("entity")
    )
    return _rows(records, parse, result)


def save_dxcc(rows: Iterable[dict], result: ImportResult) -> ImportResult:
    """Upsert DXCC entities, entities are never removed"""
    rows = list(rows)
    if not rows:
        raise ImportAborted("TQSL config has no DXCC entities")
    existing = set(DxccEntity.objects.values_list("code", flat=True))
    now = timezone.now()
    with transaction.atomic():
        DxccEntity.objects.bulk_create(
            [DxccEntity(updated_at=now, **row) for row in rows],
            update_conflicts=True,
            unique_fields=["code"],
            update_fields=[
                "name",
                "is_deleted",
                "valid_from",
                "valid_until",
                "cq_zones",
                "itu_zones",
                "updated_at",
            ],
        )
    for row in rows:
        if row["code"] in existing:
            result.updated += 1
        else:
            result.created += 1
    logger.info("Imported %s", result)
    return result


PARSERS = {
    ReferenceProgram.POTA: parse_pota,
    ReferenceProgram.SOTA: parse_sota,
    ReferenceProgram.IOTA: parse_iota,
}


def import_data(source: str, data: bytes | str) -> ImportResult:
    """Load a downloaded list into the database"""
    result = ImportResult(source)
    if source == "dxcc":
        return save_dxcc(parse_dxcc(data, result), result)
    if source not in PARSERS:
        raise ValueError(f"Unknown source {source!r}")
    return save_references(source, PARSERS[source](data, result), result)


def refresh(source: str, fetch: http.Fetch | None = None) -> ImportResult:
    """Download the latest list for a source and load it"""
    fetch = fetch or http.get
    data = fetch(settings.REFERENCE_SOURCES[source], timeout=300)
    return import_data(source, data)
