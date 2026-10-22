"""ADIF tagged text files (.adi)

Each field is <NAME:LENGTH>data, records end with <EOR>, and an optional
header before them ends with <EOH>. Lengths are counted in characters.
International (_INTL) fields aren't allowed in ADI, so are left out of the
export, use ADX or JSON to keep those"""

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime

from apps.logbook import fields
from apps.logbook.formats import adif


def _field(name: str, value: str) -> str:
    return f"<{name}:{len(value)}>{value}"


def write_header(created: datetime, userdefs: Iterable[str] = ()) -> str:
    lines = [f"Exported by {adif.PROGRAM_NAME} {adif.program_version()}"]
    lines += [
        _field(name, value)
        for name, value in adif.header_fields(created).items()
    ]
    for number, name in enumerate(sorted(userdefs), start=1):
        lines.append(f"<USERDEF{number}:{len(name)}:S>{name}")
    lines.append("<EOH>")
    return "\n".join(lines) + "\n\n"


def write_record(qso) -> str:
    record = adif.qso_to_adif(qso)
    parts = [
        _field(name, fields.replace_unsafe_chars(value))
        for name, value in record.items()
        if not fields.is_intl(name)
    ]
    return " ".join(parts) + " <EOR>\n"


def export(qsos, created: datetime) -> Iterator[str]:
    """The QSOs as an ADI file, a piece at a time

    Takes a queryset, which is streamed, or a list"""
    yield write_header(created, userdefs(qsos))
    for qso in iterate(qsos):
        yield write_record(qso)


def iterate(qsos) -> Iterator:
    return qsos.iterator() if hasattr(qsos, "iterator") else iter(qsos)


def userdefs(qsos, include_intl: bool = False) -> set[str]:
    """User defined fields in use, which the header has to declare"""
    if hasattr(qsos, "values_list"):
        extras = qsos.values_list("extra", flat=True).iterator()
    else:
        extras = (qso.extra for qso in qsos)
    return {
        key
        for extra in extras
        for key in (extra or {})
        if fields.is_userdef_field(key)
        and (include_intl or not fields.is_intl(key))
    }


# Reading


@dataclass
class Record:
    index: int
    fields: dict[str, str]
    raw: str
    problems: list[str] = field(default_factory=list)


@dataclass
class Parsed:
    header: dict[str, str]
    records: list[Record]
    problems: list[str] = field(default_factory=list)


_TAG_RE = re.compile(
    r"<([A-Za-z0-9_][^:<>]*?)(?::(\d+)(?::([A-Za-z]))?)?>",
)
_EOH_RE = re.compile(r"<eoh>", re.IGNORECASE)


def _scan(text: str, start: int, end: int, bytes_possible: bool = True):
    """Yield (name, value, tag start, data end, truncated) for each tag

    value is None for tags without data, such as <EOR>"""
    position = start
    while True:
        opening = text.find("<", position, end)
        if opening == -1:
            return
        match = _TAG_RE.match(text, opening, end)
        if match is None:
            # A stray "<" in text between fields
            position = opening + 1
            continue
        name = match.group(1).strip().upper()
        if match.group(2) is None:
            yield name, None, opening, match.end(), False
            position = match.end()
            continue
        length = int(match.group(2))
        if bytes_possible:
            data_end = _data_end(text, match.end(), length, end)
        else:
            data_end = match.end() + length
        truncated = data_end > end
        data_end = min(data_end, end)
        yield name, text[match.end() : data_end], opening, data_end, truncated
        position = data_end


def _data_end(text: str, start: int, length: int, end: int) -> int:
    """Where a field's data ends

    Lengths should count characters, but some loggers count UTF-8 bytes. If
    the data isn't plain ASCII and reading it as bytes ends cleanly, at a
    space or the next tag, while reading it as characters runs into one,
    the length was counted in bytes"""
    by_chars = start + length
    data = text[start:by_chars]
    if data.isascii():
        return by_chars
    size = 0
    by_bytes = start
    while by_bytes < len(text) and size < length:
        size += len(text[by_bytes].encode("utf-8", "surrogatepass"))
        by_bytes += 1
    if size != length or by_bytes >= by_chars:
        return by_chars
    overrun = text[by_bytes:by_chars]
    ends_cleanly = by_bytes >= end or text[by_bytes] in "< \t\r\n"
    if ends_cleanly and ("<" in overrun or not overrun.strip()):
        return by_bytes
    return by_chars


def parse(text: str) -> Parsed:
    """Read an ADI file, keeping going past anything it can't make sense of

    Every record comes back with its raw text, so a record that can't be
    used still isn't lost"""
    text = text.lstrip("﻿")
    header: dict[str, str] = {}
    problems: list[str] = []
    eoh = _EOH_RE.search(text)
    start = 0
    if eoh is not None:
        for name, value, *_ in _scan(text, 0, eoh.start()):
            if value is not None:
                header[name] = value
        start = eoh.end()
    elif not text.lstrip().startswith("<"):
        problems.append("No <EOH> after the header, reading it as records")

    records: list[Record] = []
    current: dict[str, str] = {}
    current_problems: list[str] = []
    record_start = None
    # Our own files count characters, so there's no need to guess
    ours = header.get("PROGRAMID", "").lower() == adif.PROGRAM_NAME
    for name, value, tag_start, data_end, truncated in _scan(
        text, start, len(text), bytes_possible=not ours
    ):
        if record_start is None:
            record_start = tag_start
        if value is None:
            if name == "EOR":
                records.append(
                    Record(
                        len(records),
                        current,
                        text[record_start:data_end],
                        current_problems,
                    )
                )
                current, current_problems, record_start = {}, [], None
            elif name != "EOH":
                current_problems.append(f"<{name}> has no length, skipped")
            continue
        if truncated:
            current_problems.append(f"{name} runs past the end of the file")
        if name in current:
            current_problems.append(
                f"{name} appears more than once, the first was used, "
                f"the other was {value!r}"
            )
            continue
        current[name] = value
    if current:
        current_problems.append("The last record has no <EOR>")
        records.append(
            Record(
                len(records),
                current,
                text[record_start:],
                current_problems,
            )
        )
    return Parsed(header, records, problems)
