"""ADIF XML files (.adx)

    <ADX>
      <HEADER><ADIF_VER>3.1.5</ADIF_VER>...</HEADER>
      <RECORDS>
        <RECORD><CALL>M0WME</CALL>...
          <APP PROGRAMID="QSOLOGY" FIELDNAME="UUID" TYPE="S">...</APP>
          <USERDEF FIELDNAME="EPC">...</USERDEF>
        </RECORD>
      </RECORDS>
    </ADX>

Unlike ADI, ADX can hold every field including the _INTL ones"""

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime

from apps.logbook import fields
from apps.logbook.formats import adi, adif
from apps.logbook.formats.adi import Record


def _element(name: str, value: str, **attributes) -> ET.Element:
    element = ET.Element(name, attributes)
    element.text = fields.replace_unsafe_chars(value)
    return element


def write_header(created: datetime, userdefs) -> str:
    header = ET.Element("HEADER")
    for name, value in adif.header_fields(created).items():
        header.append(_element(name, value))
    for number, name in enumerate(sorted(userdefs), start=1):
        header.append(_element("USERDEF", name, FIELDID=str(number), TYPE="S"))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<ADX>\n'
        + serialize(header)
        + "\n<RECORDS>\n"
    )


def record_element(qso) -> ET.Element:
    record = ET.Element("RECORD")
    for name, value in adif.qso_to_adif(qso).items():
        if fields.is_app_field(name):
            program_id, _, field_name = name[4:].partition("_")
            record.append(
                _element(
                    "APP",
                    value,
                    PROGRAMID=program_id,
                    FIELDNAME=field_name,
                    TYPE="S",
                )
            )
        elif fields.is_userdef_field(name):
            record.append(_element("USERDEF", value, FIELDNAME=name))
        else:
            record.append(_element(name, value))
    return record


def serialize(element: ET.Element) -> str:
    # XML readers turn a bare carriage return into a newline, so escape it
    return ET.tostring(element, encoding="unicode").replace("\r", "&#13;")


def export(qsos, created: datetime) -> Iterator[str]:
    """The QSOs as an ADX file, a piece at a time"""
    yield write_header(created, adi.userdefs(qsos, include_intl=True))
    for qso in adi.iterate(qsos):
        yield serialize(record_element(qso)) + "\n"
    yield "</RECORDS>\n</ADX>\n"


# Reading


@dataclass
class Parsed:
    header: dict[str, str]
    records: list[Record]
    problems: list[str] = field(default_factory=list)


def _field_name(element: ET.Element) -> str:
    if element.tag == "APP":
        program_id = element.get("PROGRAMID", "")
        field_name = element.get("FIELDNAME", "")
        return "APP_" + program_id + (f"_{field_name}" if field_name else "")
    if element.tag == "USERDEF":
        return element.get("FIELDNAME", "")
    return element.tag


def parse(text: str) -> Parsed:
    """Read an ADX file

    Raises ValueError if it isn't valid XML, as then nothing in it can be
    trusted"""
    try:
        root = ET.fromstring(text.lstrip("﻿"))
    except ET.ParseError as exc:
        raise ValueError(f"Not valid XML: {exc}") from exc
    if root.tag.upper() != "ADX":
        raise ValueError(f"Expected <ADX>, found <{root.tag}>")
    header = {}
    header_element = root.find("HEADER")
    if header_element is not None:
        for element in header_element:
            if element.tag != "USERDEF":
                header[element.tag.upper()] = element.text or ""
    records = []
    container = root.find("RECORDS")
    for index, element in enumerate(container if container is not None else []):
        problems = []
        values: dict[str, str] = {}
        for child in element:
            name = _field_name(child).upper()
            value = child.text or ""
            if name in values:
                problems.append(
                    f"{name} appears more than once, the first was used, "
                    f"the other was {value!r}"
                )
                continue
            values[name] = value
        records.append(
            Record(
                index,
                values,
                ET.tostring(element, encoding="unicode"),
                problems,
            )
        )
    return Parsed(header, records)
