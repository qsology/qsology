"""Filters for picking QSOs out of the master log

A filter is a small JSON tree, which is what logbooks store and what a UI can
build directly:

    {"field": "band", "op": "eq", "value": "20m"}
    {"and": [node, ...]}  {"or": [node, ...]}  {"not": node}

There is also a text syntax for typing filters in, which parses to the same
tree and can be printed back from it:

    band:20m mode:SSB,CW pota:US-0001 -call:K1* date>=2024-01-01
    (sota:G/LD-* OR wwff:GFF-*) freq:14..14.35 has:gridsquare
    name~"john smith" NOT contest:CQ-WW-CW

Terms next to each other must all match. "field:value" matches a value, a
list "a,b" any of them, "*" and "?" are wildcards and "a..b" is an inclusive
range. "~" is contains, "^" starts with, "!=", ">", ">=", "<" and "<=" do what
they say. "has:field" and "missing:field" test for a value. A word on its own
matches the callsign worked. Text matching ignores case.

Filters only ever reach the fields listed here, and are always applied on
top of the owner's own QSOs"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from functools import reduce
from operator import and_, or_

from apps.logbook import fields
from apps.logbook.fields import Kind
from django.db.models import Q

MAX_DEPTH = 10
MAX_NODES = 200
MAX_LIST = 100
MAX_VALUE_LENGTH = 200


class QueryError(ValueError):
    """The filter can't be understood"""


# Kinds used only by filters
DATE = "date"
REF = "ref"
ENUM = "enum"

TEXT_OPS = frozenset({"eq", "in", "contains", "startswith", "glob", "empty"})
LIST_OPS = TEXT_OPS | {"has"}
NUMBER_OPS = frozenset({"eq", "in", "gt", "gte", "lt", "lte", "empty"})
REF_OPS = frozenset({"eq", "in", "empty"})
ENUM_OPS = frozenset({"eq", "in"})

OPS_BY_KIND = {
    Kind.STRING: TEXT_OPS,
    Kind.LIST: LIST_OPS,
    Kind.INT: NUMBER_OPS,
    Kind.DECIMAL: NUMBER_OPS,
    Kind.COORDINATE: NUMBER_OPS,
    Kind.DATETIME: NUMBER_OPS,
    DATE: NUMBER_OPS,
    REF: REF_OPS,
    ENUM: ENUM_OPS,
}


@dataclass(frozen=True)
class FilterField:
    name: str
    path: str
    kind: str


FILTER_FIELDS: dict[str, FilterField] = {
    field.name: FilterField(field.name, field.name, field.kind)
    for field in fields.FIELDS
} | {
    "date": FilterField("date", "time_on__date", DATE),
    "location": FilterField("location", "my_location_id", REF),
    "session": FilterField("session", "session_id", REF),
    "review": FilterField("review", "review_status", ENUM),
}

REVIEW_VALUES = frozenset({"ok", "needs_review", "reviewed"})

ALIASES = {
    "callsign": "call",
    "my_call": "station_callsign",
    "station": "station_callsign",
    "grid": "gridsquare",
    "my_grid": "my_gridsquare",
    "pota": "pota_ref",
    "my_pota": "my_pota_ref",
    "sota": "sota_ref",
    "my_sota": "my_sota_ref",
    "wwff": "wwff_ref",
    "my_wwff": "my_wwff_ref",
    "contest": "contest_id",
    "op": "operator",
}

EXTRA_PREFIX = "extra."


def resolve_field(name: str) -> FilterField:
    if not isinstance(name, str):
        raise QueryError("Field names must be text")
    if name.lower().startswith(EXTRA_PREFIX):
        key = name[len(EXTRA_PREFIX) :].upper()
        # A double underscore would be read by Django as a lookup
        if not fields.EXTRA_KEY_RE.match(key) or "__" in key:
            raise QueryError(f"{key!r} isn't a valid ADIF field name")
        return FilterField(f"{EXTRA_PREFIX}{key}", f"extra__{key}", Kind.STRING)
    name = ALIASES.get(name.lower(), name.lower())
    if name not in FILTER_FIELDS:
        raise QueryError(f"Unknown field {name!r}")
    return FILTER_FIELDS[name]


# Validating


def _coerce(field: FilterField, op: str, value):
    """Check a value suits the field, returning its canonical form"""
    if op == "empty":
        if not isinstance(value, bool):
            raise QueryError(f"{field.name}: empty takes true or false")
        return value
    if op == "in":
        if not isinstance(value, list) or not value:
            raise QueryError(f"{field.name}: in takes a list of values")
        if len(value) > MAX_LIST:
            raise QueryError(f"{field.name}: no more than {MAX_LIST} values")
        return [_coerce(field, "eq", item) for item in value]
    if isinstance(value, bool) or value is None:
        raise QueryError(f"{field.name}: {value!r} isn't a valid value")
    if field.kind in (Kind.INT, REF):
        try:
            number = Decimal(str(value).strip())
        except InvalidOperation as exc:
            raise QueryError(f"{field.name}: {value!r} isn't a number") from exc
        if not number.is_finite() or number != number.to_integral_value():
            raise QueryError(f"{field.name}: {value!r} isn't a whole number")
        return int(number)
    if field.kind in (Kind.DECIMAL, Kind.COORDINATE):
        try:
            number = Decimal(str(value).strip())
        except InvalidOperation as exc:
            raise QueryError(f"{field.name}: {value!r} isn't a number") from exc
        if not number.is_finite():
            raise QueryError(f"{field.name}: {value!r} isn't a number")
        return fields.format_decimal(number)
    if not isinstance(value, str):
        raise QueryError(f"{field.name}: {value!r} must be text")
    if len(value) > MAX_VALUE_LENGTH:
        raise QueryError(f"{field.name}: value is too long")
    if field.kind == Kind.DATETIME:
        _parse_datetime(field, value)
    elif field.kind == DATE:
        _parse_date(field, value)
    elif field.kind == ENUM and value not in REVIEW_VALUES:
        raise QueryError(
            f"{field.name}: must be one of {', '.join(sorted(REVIEW_VALUES))}"
        )
    if op == "glob" and not _BARE_SAFE_RE.fullmatch(value):
        raise QueryError(
            f"{field.name}: wildcards can't be used with spaces, quotes, "
            "commas or brackets"
        )
    return value


def _parse_date(field: FilterField, value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise QueryError(
            f"{field.name}: {value!r} isn't a date, use YYYY-MM-DD"
        ) from exc


def _parse_datetime(field: FilterField, value: str) -> datetime:
    try:
        if len(value) == 10:
            return datetime.combine(date.fromisoformat(value), time(), UTC)
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise QueryError(
            f"{field.name}: {value!r} isn't a date or time"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def validate(node, _depth: int = 0, _count: list | None = None) -> dict:
    """Check a filter tree, returning it in canonical form

    Nested and/or of the same kind are flattened, single child and/or are
    unwrapped and values are coerced to suit their field"""
    count = _count if _count is not None else [0]
    count[0] += 1
    if count[0] > MAX_NODES:
        raise QueryError(f"Filters can have at most {MAX_NODES} parts")
    if _depth > MAX_DEPTH:
        raise QueryError(f"Filters can nest at most {MAX_DEPTH} deep")
    if not isinstance(node, dict):
        raise QueryError("Each part of a filter must be an object")
    keys = set(node)
    if keys in ({"and"}, {"or"}):
        kind = keys.pop()
        children = node[kind]
        if not isinstance(children, list):
            raise QueryError(f"{kind} takes a list")
        flat = []
        for child in children:
            child = validate(child, _depth + 1, count)
            if kind in child:
                flat.extend(child[kind])
            elif child != {"and": []}:
                flat.append(child)
            elif kind == "or":
                # Matching everything in an or matches everything
                return {"and": []}
        if len(flat) == 1:
            return flat[0]
        if kind == "or" and not flat:
            raise QueryError("or needs at least one condition")
        return {kind: flat}
    if keys == {"not"}:
        child = validate(node["not"], _depth + 1, count)
        if child == {"and": []}:
            raise QueryError("not can't be used on an empty filter")
        if "not" in child:
            return child["not"]
        return {"not": child}
    if keys == {"field", "op", "value"}:
        field = resolve_field(node["field"])
        op = node["op"]
        if not isinstance(op, str) or op not in OPS_BY_KIND[field.kind]:
            raise QueryError(f"{field.name} can't be used with {op!r}")
        value = _coerce(field, op, node["value"])
        # Keep one spelling for each condition, so that printing a filter and
        # parsing it back gives the same tree
        if op == "in" and len(value) == 1:
            op, value = "eq", value[0]
        if op == "glob" and "*" not in value and "?" not in value:
            op = "eq"
        if op == "eq" and field.kind == Kind.LIST:
            op = "has"
        return {"field": field.name, "op": op, "value": value}
    raise QueryError(f"Unrecognised filter part {sorted(keys)}")


MATCH_ALL = {"and": []}


# Compiling to a Django Q


def _regex_glob(value: str) -> str:
    return (
        "^"
        + "".join(
            ".*" if char == "*" else "." if char == "?" else re.escape(char)
            for char in value
        )
        + "$"
    )


def _regex_has(value: str) -> str:
    # A list element, optionally followed by an @location, e.g. US-0001@US-ME
    return rf"(^|,)\s*{re.escape(value)}(@[^,]*)?\s*(,|$)"


def _condition(node: dict) -> Q:
    field = resolve_field(node["field"])
    op, value, path = node["op"], node["value"], field.path
    is_extra = field.name.startswith(EXTRA_PREFIX)
    if op == "empty":
        if is_extra:
            key = field.name[len(EXTRA_PREFIX) :]
            empty = ~Q(extra__has_key=key) | Q(**{path: ""})
        elif field.kind in (Kind.STRING, Kind.LIST):
            empty = Q(**{path: ""}) | Q(**{f"{path}__isnull": True})
        else:
            empty = Q(**{f"{path}__isnull": True})
        return empty if value else ~empty
    if op == "in":
        return reduce(
            or_,
            (
                _condition({"field": field.name, "op": "eq", "value": item})
                for item in value
            ),
        )
    if field.kind == DATE:
        value = _parse_date(field, value)
    elif field.kind == Kind.DATETIME:
        value = _parse_datetime(field, value)
    elif field.kind in (Kind.DECIMAL, Kind.COORDINATE):
        value = Decimal(value)
    if field.kind in (Kind.STRING, Kind.LIST):
        lookup = {
            "eq": "iexact",
            "contains": "icontains",
            "startswith": "istartswith",
            "glob": "iregex",
            "has": "iregex",
        }[op]
        if op == "glob":
            value = _regex_glob(value)
        elif op == "has":
            value = _regex_has(value)
        elif op == "eq" and field.kind == Kind.LIST:
            # An in on a list field reaches here, it matches any element
            lookup, value = "iregex", _regex_has(value)
        return Q(**{f"{path}__{lookup}": value})
    lookup = {"eq": "exact", "gt": "gt", "gte": "gte", "lt": "lt", "lte": "lte"}
    return Q(**{f"{path}__{lookup[op]}": value})


def _compile(node: dict) -> Q:
    if "and" in node:
        return reduce(and_, (_compile(child) for child in node["and"]), Q())
    if "or" in node:
        return reduce(or_, (_compile(child) for child in node["or"]))
    if "not" in node:
        return ~_compile(node["not"])
    return _condition(node)


def compile_filter(node: dict) -> Q:
    """Turn a filter tree into a Q to filter QSOs with"""
    return _compile(validate(node))


# Parsing the text syntax

_BARE_SAFE_RE = re.compile(r'[^\s"(),]+')
_TOKEN_RE = re.compile(
    r"""
    (?P<space>\s+)
    | (?P<lparen>\()
    | (?P<rparen>\))
    | (?P<term>
        (?P<negate>-)?
        (?:(?P<field>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)?)
           (?P<op>!=|>=|<=|:|=|>|<|~|\^))?
        (?P<value>(?:"(?:[^"\\]|\\.)*"|[^\s()",]+)
                  (?:,(?:"(?:[^"\\]|\\.)*"|[^\s()",]+))*)?
      )
    """,
    re.VERBOSE,
)
KEYWORDS = {"AND", "OR", "NOT"}


@dataclass
class _Token:
    kind: str
    text: str
    position: int
    negate: bool = False
    field: str | None = None
    op: str | None = None
    values: list[tuple[str, bool]] | None = None  # (value, was quoted)


def _split_values(text: str) -> list[tuple[str, bool]]:
    values = []
    for match in re.finditer(r'"((?:[^"\\]|\\.)*)"|([^,]+)', text):
        if match.group(1) is not None:
            values.append((re.sub(r"\\(.)", r"\1", match.group(1)), True))
        else:
            values.append((match.group(2), False))
    return values


def _tokenize(text: str) -> list[_Token]:
    tokens = []
    position = 0
    while position < len(text):
        match = _TOKEN_RE.match(text, position)
        if match is None or match.end() == position:
            raise QueryError(f"Can't understand {text[position:]!r}")
        kind = match.lastgroup if match.group("term") is None else "term"
        if kind == "term":
            word = match.group("term")
            if word in KEYWORDS:
                tokens.append(_Token(word, word, position))
            else:
                value = match.group("value")
                if value is None:
                    raise QueryError(f"{word!r} needs a value")
                tokens.append(
                    _Token(
                        "term",
                        word,
                        position,
                        negate=bool(match.group("negate")),
                        field=match.group("field"),
                        op=match.group("op"),
                        values=_split_values(value),
                    )
                )
        elif kind != "space":
            tokens.append(_Token(kind, match.group(kind), position))
        position = match.end()
    return tokens


def _term_node(token: _Token) -> dict:
    values = token.values or []
    field_name, op = token.field, token.op
    if field_name is None:
        field_name, op = "call", ":"
    if op == ":" and field_name.lower() in {"has", "missing"}:
        if len(values) != 1 or values[0][1]:
            raise QueryError(f"{field_name}: takes a single field name")
        return {
            "field": resolve_field(values[0][0]).name,
            "op": "empty",
            "value": field_name.lower() == "missing",
        }
    field = resolve_field(field_name)
    if len(values) > 1:
        if op not in (":", "="):
            raise QueryError("Lists of values can only be used with ':'")
        return {
            "field": field.name,
            "op": "in",
            "value": [value for value, _ in values],
        }
    value, quoted = values[0]
    if op in (":", "="):
        if not quoted and ".." in value and field.kind not in TEXT_KINDS:
            low, _, high = value.partition("..")
            return {
                "and": [
                    {"field": field.name, "op": "gte", "value": low},
                    {"field": field.name, "op": "lte", "value": high},
                ]
            }
        if not quoted and ("*" in value or "?" in value):
            return {"field": field.name, "op": "glob", "value": value}
        op_name = "has" if field.kind == Kind.LIST else "eq"
        return {"field": field.name, "op": op_name, "value": value}
    if op == "!=":
        return {"not": {"field": field.name, "op": "eq", "value": value}}
    op_name = {
        "~": "contains",
        "^": "startswith",
        ">": "gt",
        ">=": "gte",
        "<": "lt",
        "<=": "lte",
    }[op]
    return {"field": field.name, "op": op_name, "value": value}


TEXT_KINDS = (Kind.STRING, Kind.LIST)


class _Parser:
    def __init__(self, tokens: list[_Token]):
        self.tokens = tokens
        self.index = 0
        self.depth = 0

    def peek(self) -> _Token | None:
        return (
            self.tokens[self.index] if self.index < len(self.tokens) else None
        )

    def take(self) -> _Token:
        token = self.tokens[self.index]
        self.index += 1
        return token

    def parse(self) -> dict:
        node = self.or_expr()
        if self.peek() is not None:
            token = self.peek()
            raise QueryError(f"Unexpected {token.text!r} at {token.position}")
        return node

    def or_expr(self) -> dict:
        children = [self.and_expr()]
        while self.peek() is not None and self.peek().kind == "OR":
            self.take()
            children.append(self.and_expr())
        return children[0] if len(children) == 1 else {"or": children}

    def and_expr(self) -> dict:
        children = []
        while True:
            token = self.peek()
            if token is None or token.kind in ("OR", "rparen"):
                break
            if token.kind == "AND":
                self.take()
                if not children:
                    raise QueryError("AND needs something before it")
                following = self.peek()
                if following is None or following.kind in (
                    "AND",
                    "OR",
                    "rparen",
                ):
                    raise QueryError("AND needs something after it")
                continue
            children.append(self.unary())
        if not children:
            token = self.peek()
            where = f" before {token.text!r}" if token else ""
            raise QueryError(f"Expected a condition{where}")
        return children[0] if len(children) == 1 else {"and": children}

    def unary(self) -> dict:
        token = self.take()
        if token.kind in ("NOT", "lparen"):
            # Brackets and NOTs nest, keep them from exhausting the stack
            self.depth += 1
            if self.depth > MAX_DEPTH * 2:
                raise QueryError(f"Filters can nest at most {MAX_DEPTH} deep")
            try:
                return self._nested(token)
            finally:
                self.depth -= 1
        if token.kind == "term":
            node = _term_node(token)
            return {"not": node} if token.negate else node
        raise QueryError(f"Unexpected {token.text!r} at {token.position}")

    def _nested(self, token: _Token) -> dict:
        if token.kind == "NOT":
            if self.peek() is None:
                raise QueryError("NOT needs a condition after it")
            return {"not": self.unary()}
        node = self.or_expr()
        closing = self.peek()
        if closing is None or closing.kind != "rparen":
            raise QueryError(f"Unclosed bracket at {token.position}")
        self.take()
        return node


def parse(text: str) -> dict:
    """Parse the text syntax into a validated filter tree

    An empty string matches everything"""
    if not isinstance(text, str):
        raise QueryError("Queries must be text")
    if len(text) > MAX_VALUE_LENGTH * 20:
        raise QueryError("Query is too long")
    tokens = _tokenize(text)
    if not tokens:
        return dict(MATCH_ALL)
    return validate(_Parser(tokens).parse())


# Printing back to the text syntax


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _literal(value) -> str:
    """A value that reads back as exactly itself, never a wildcard or list"""
    text = str(value)
    if (
        _BARE_SAFE_RE.fullmatch(text)
        and "*" not in text
        and "?" not in text
        and ".." not in text
        and text not in KEYWORDS
        and not text.startswith("-")
    ):
        return text
    return _quote(text)


def _format_condition(node: dict) -> str:
    field, op, value = node["field"], node["op"], node["value"]
    if op == "empty":
        return f"{'missing' if value else 'has'}:{field}"
    if op == "in":
        return f"{field}:" + ",".join(_literal(item) for item in value)
    if op == "glob":
        return f"{field}:{value}"
    if op in ("eq", "has"):
        return f"{field}:{_literal(value)}"
    symbol = {
        "contains": "~",
        "startswith": "^",
        "gt": ">",
        "gte": ">=",
        "lt": "<",
        "lte": "<=",
    }[op]
    return f"{field}{symbol}{_literal(value)}"


def _format(node: dict, parent: str = "") -> str:
    if "and" in node:
        text = " ".join(_format(child, "and") for child in node["and"])
        return f"({text})" if parent in ("not",) else text
    if "or" in node:
        text = " OR ".join(_format(child, "or") for child in node["or"])
        return f"({text})" if parent in ("and", "not") else text
    if "not" in node:
        child = node["not"]
        if "field" in child:
            return f"-{_format_condition(child)}"
        return f"NOT {_format(child, 'not')}"
    return _format_condition(node)


def format_filter(node: dict) -> str:
    """Write a filter tree in the text syntax"""
    node = validate(node)
    if node == MATCH_ALL:
        return ""
    return _format(node)
