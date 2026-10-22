"""The qsology JSON format

Like ADIF, but plain JSON so it is easy for other programs to work with:

    {
      "format": "qsology",
      "version": 1,
      "adif_version": "3.1.5",
      "program": {"id": "qsology", "version": "2026.01"},
      "created": "2024-06-01T12:00:00Z",
      "qsos": [
        {
          "uuid": "...",
          "call": "M0WME",
          "time_on": "2024-06-01T11:58:03Z",
          "freq": "14.074",
          "mode": "FT8",
          "extra": {"QSLMSG": "TNX"}
        }
      ]
    }

Field names are the lower case ADIF names, see apps.logbook.fields, with
QSO_DATE and TIME_ON joined into time_on, and the same for time_off. Times
are ISO 8601 in UTC. Decimals are strings so they keep their exact value,
whole numbers are numbers. Empty fields are left out. Any other ADIF field is
in extra under its ADIF name"""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime

from apps.logbook import fields
from apps.logbook.formats import adi, adif

FORMAT = "qsology"
VERSION = 1


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def export(qsos, created: datetime) -> Iterator[str]:
    """The QSOs as a JSON document, a piece at a time"""
    header = {
        "format": FORMAT,
        "version": VERSION,
        "adif_version": fields.ADIF_VERSION,
        "program": {
            "id": adif.PROGRAM_NAME,
            "version": adif.program_version(),
        },
        "created": fields.format_datetime(created),
    }
    yield _dump(header)[:-1] + ',"qsos":[\n'
    separator = ""
    for qso in adi.iterate(qsos):
        yield separator + _dump(qso.to_data())
        separator = ",\n"
    yield "\n]}\n"


@dataclass
class Record:
    index: int
    data: dict
    raw: str
    problems: list[str] = field(default_factory=list)


@dataclass
class Parsed:
    header: dict
    records: list[Record]
    problems: list[str] = field(default_factory=list)


def parse(text: str) -> Parsed:
    """Read a JSON export

    Raises ValueError if it isn't one, as then nothing in it can be
    trusted"""
    try:
        document = json.loads(text.lstrip("﻿"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON: {exc}") from exc
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise ValueError("Not a qsology JSON export")
    if document.get("version") != VERSION:
        raise ValueError(
            f"JSON export version {document.get('version')!r} isn't supported"
        )
    qsos = document.get("qsos")
    if not isinstance(qsos, list):
        raise ValueError("The export has no list of QSOs")
    header = {key: value for key, value in document.items() if key != "qsos"}
    records = [
        Record(index, item if isinstance(item, dict) else {}, _dump(item))
        for index, item in enumerate(qsos)
    ]
    for record, item in zip(records, qsos, strict=True):
        if not isinstance(item, dict):
            record.problems.append("Each QSO must be an object")
    return Parsed(header, records)
