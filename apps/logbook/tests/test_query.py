"""The filter language logbooks use to pick QSOs out of the master log"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from apps.callsigns.models import Callsign, CallsignAllocation
from apps.locations.models import Location
from apps.logbook import query, services
from apps.logbook.models import QSO, Logbook, LogSession
from apps.logbook.query import MATCH_ALL, QueryError
from hypothesis import assume, given
from hypothesis import strategies as st


def cond(field, op, value):
    return {"field": field, "op": op, "value": value}


class TestParseTerms:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("band:20m", cond("band", "eq", "20m")),
            ("band=20m", cond("band", "eq", "20m")),
            ("call!=K1ABC", {"not": cond("call", "eq", "K1ABC")}),
            ("name~smith", cond("name", "contains", "smith")),
            ("call^K1", cond("call", "startswith", "K1")),
            ("freq>14", cond("freq", "gt", "14")),
            ("freq>=14.074", cond("freq", "gte", "14.074")),
            ("freq<7", cond("freq", "lt", "7")),
            ("freq<=7.3", cond("freq", "lte", "7.3")),
            ("dxcc:223", cond("dxcc", "eq", 223)),
            ("date>=2024-01-01", cond("date", "gte", "2024-01-01")),
            ("review:needs_review", cond("review", "eq", "needs_review")),
            ("location:3", cond("location", "eq", 3)),
        ],
    )
    def test_operators(self, text, expected):
        assert query.parse(text) == expected

    def test_list_is_in(self):
        assert query.parse("mode:SSB,CW") == cond("mode", "in", ["SSB", "CW"])

    def test_list_with_quoted_items(self):
        assert query.parse('name:"Smith, J","a b",c') == cond(
            "name", "in", ["Smith, J", "a b", "c"]
        )

    def test_list_only_with_colon(self):
        with pytest.raises(QueryError, match="Lists"):
            query.parse("freq>1,2")

    @pytest.mark.parametrize("value", ["K1*", "K?ABC", "*/P"])
    def test_wildcards_are_glob(self, value):
        assert query.parse(f"call:{value}") == cond("call", "glob", value)

    def test_quoted_wildcards_are_literal(self):
        assert query.parse('call:"K1*"') == cond("call", "eq", "K1*")

    @pytest.mark.parametrize(
        ("text", "field", "low", "high"),
        [
            ("freq:14..14.35", "freq", "14", "14.35"),
            ("dxcc:1..300", "dxcc", 1, 300),
            ("date:2024-01-01..2024-01-31", "date", "2024-01-01", "2024-01-31"),
            (
                "time_on:2024-01-01T00:00:00Z..2024-01-02",
                "time_on",
                "2024-01-01T00:00:00Z",
                "2024-01-02",
            ),
        ],
    )
    def test_ranges(self, text, field, low, high):
        assert query.parse(text) == {
            "and": [cond(field, "gte", low), cond(field, "lte", high)]
        }

    def test_dots_on_text_are_literal(self):
        assert query.parse("comment:a..b") == cond("comment", "eq", "a..b")

    def test_has_and_missing(self):
        assert query.parse("has:gridsquare") == cond(
            "gridsquare", "empty", False
        )
        assert query.parse("missing:freq") == cond("freq", "empty", True)

    def test_has_resolves_aliases_and_extra(self):
        assert query.parse("has:grid") == cond("gridsquare", "empty", False)
        assert query.parse("missing:extra.qslmsg") == cond(
            "extra.QSLMSG", "empty", True
        )

    @pytest.mark.parametrize("text", ['has:"call"', "has:call,name"])
    def test_has_takes_one_bare_field(self, text):
        with pytest.raises(QueryError, match="single field"):
            query.parse(text)

    def test_bare_word_is_call(self):
        assert query.parse("K1ABC") == cond("call", "eq", "K1ABC")
        assert query.parse("K1*") == cond("call", "glob", "K1*")

    @pytest.mark.parametrize(
        ("alias", "field"),
        [
            ("callsign", "call"),
            ("my_call", "station_callsign"),
            ("station", "station_callsign"),
            ("grid", "gridsquare"),
            ("my_grid", "my_gridsquare"),
            ("sota", "sota_ref"),
            ("my_sota", "my_sota_ref"),
            ("wwff", "wwff_ref"),
            ("contest", "contest_id"),
            ("op", "operator"),
            ("CALL", "call"),
        ],
    )
    def test_aliases(self, alias, field):
        assert query.parse(f"{alias}:X") == cond(field, "eq", "X")

    def test_pota_alias_is_a_list(self):
        assert query.parse("pota:US-0001") == cond("pota_ref", "has", "US-0001")
        assert query.parse("my_pota:US-0001") == cond(
            "my_pota_ref", "has", "US-0001"
        )

    def test_extra_fields(self):
        assert query.parse("extra.qslmsg:TNX") == cond(
            "extra.QSLMSG", "eq", "TNX"
        )

    def test_quoted_values_unescape(self):
        assert query.parse(r'name:"say \"hi\" \\o/"') == cond(
            "name", "eq", 'say "hi" \\o/'
        )

    def test_unicode(self):
        assert query.parse("name:Józef") == cond("name", "eq", "Józef")

    def test_keywords_only_in_upper_case(self):
        # Lower case "or" is a callsign search, not OR
        assert query.parse("a or") == {
            "and": [cond("call", "eq", "a"), cond("call", "eq", "or")]
        }


class TestParseStructure:
    def test_empty_matches_everything(self):
        assert query.parse("") == MATCH_ALL
        assert query.parse("   ") == MATCH_ALL

    def test_terms_are_anded(self):
        assert query.parse("band:20m mode:CW") == {
            "and": [cond("band", "eq", "20m"), cond("mode", "eq", "CW")]
        }

    def test_explicit_and(self):
        assert query.parse("band:20m AND mode:CW") == query.parse(
            "band:20m mode:CW"
        )

    def test_and_binds_tighter_than_or(self):
        assert query.parse("a b OR c") == {
            "or": [
                {"and": [cond("call", "eq", "a"), cond("call", "eq", "b")]},
                cond("call", "eq", "c"),
            ]
        }

    def test_brackets(self):
        assert query.parse("a (b OR c)") == {
            "and": [
                cond("call", "eq", "a"),
                {"or": [cond("call", "eq", "b"), cond("call", "eq", "c")]},
            ]
        }

    def test_redundant_brackets_unwrap(self):
        assert query.parse("((a))") == cond("call", "eq", "a")

    def test_nesting_is_flattened(self):
        assert query.parse("a (b c) (d (e))") == {
            "and": [cond("call", "eq", value) for value in "abcde"]
        }
        assert query.parse("a OR (b OR c)") == {
            "or": [cond("call", "eq", value) for value in "abc"]
        }

    def test_negation(self):
        assert query.parse("-a") == {"not": cond("call", "eq", "a")}
        assert query.parse("NOT a") == {"not": cond("call", "eq", "a")}
        assert query.parse("NOT (a OR b)") == {
            "not": {"or": [cond("call", "eq", "a"), cond("call", "eq", "b")]}
        }

    def test_double_negation_is_removed(self):
        assert query.parse("NOT -a") == cond("call", "eq", "a")
        assert query.parse("NOT NOT a") == cond("call", "eq", "a")
        assert query.parse("call!=a") == query.parse("-call:a")


class TestParseErrors:
    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("(a", "Unclosed bracket"),
            ("a)", "Unexpected"),
            ("()", "Expected a condition"),
            ("a OR", "Expected a condition"),
            ("OR a", "Expected a condition"),
            ("NOT", "NOT needs"),
            ("AND a", "AND needs something before"),
            ("a AND", "AND needs something after"),
            ("a AND OR b", "AND needs something after"),
            ("(a AND)", "AND needs something after"),
            ("bogus:1", "Unknown field 'bogus'"),
            ("band>20m", "can't be used with 'gt'"),
            ("review~ok", "can't be used with 'contains'"),
            ("freq:abc", "isn't a number"),
            ("dxcc:1.5", "isn't a whole number"),
            ("date:2024-13-01", "isn't a date"),
            ("time_on>yesterday", "isn't a date or time"),
            ("review:maybe", "must be one of"),
            ("freq:14..", "isn't a number"),
            ('call:"abc', "needs a value"),
            ("-", "needs a value"),
            (",", "Can't understand"),
        ],
    )
    def test_messages(self, text, message):
        with pytest.raises(QueryError, match=message):
            query.parse(text)

    def test_too_long(self):
        with pytest.raises(QueryError, match="too long"):
            query.parse("a " * 3000)

    def test_too_many_parts(self):
        with pytest.raises(QueryError, match="at most 200 parts"):
            query.parse(" ".join(f"c{n}" for n in range(201)))

    def test_too_deep(self):
        with pytest.raises(QueryError, match="nest"):
            query.parse("(" * 30 + "a" + ")" * 30)

    def test_very_deep_brackets_dont_exhaust_the_stack(self):
        with pytest.raises(QueryError):
            query.parse("(" * 2000 + "a" + ")" * 2000)

    def test_very_many_nots(self):
        with pytest.raises(QueryError):
            query.parse("NOT " * 900 + "a")

    def test_not_text(self):
        with pytest.raises(QueryError, match="text"):
            query.parse(None)


class TestValidate:
    def test_single_item_in_is_eq(self):
        assert query.validate(cond("call", "in", ["A"])) == cond(
            "call", "eq", "A"
        )

    def test_glob_without_wildcard_is_eq(self):
        assert query.validate(cond("call", "glob", "A")) == cond(
            "call", "eq", "A"
        )

    def test_eq_on_list_field_is_has(self):
        assert query.validate(cond("pota_ref", "eq", "US-0001")) == cond(
            "pota_ref", "has", "US-0001"
        )

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (14.074, "14.074"),
            ("14.0740", "14.074"),
            (14, "14"),
            ("1E+2", "100"),
        ],
    )
    def test_decimals_are_normalised(self, value, expected):
        assert query.validate(cond("freq", "gt", value))["value"] == expected

    @pytest.mark.parametrize(
        ("value", "expected"), [(223, 223), ("223", 223), (5.0, 5)]
    )
    def test_whole_numbers(self, value, expected):
        assert query.validate(cond("dxcc", "eq", value))["value"] == expected

    def test_aliases_are_resolved(self):
        assert (
            query.validate(cond("grid", "eq", "IO91"))["field"] == "gridsquare"
        )

    def test_or_with_match_all_matches_all(self):
        assert query.validate({"or": [cond("call", "eq", "A"), MATCH_ALL]}) == (
            MATCH_ALL
        )

    def test_match_all_in_and_is_dropped(self):
        assert query.validate(
            {"and": [cond("call", "eq", "A"), MATCH_ALL]}
        ) == (cond("call", "eq", "A"))

    def test_not_of_match_all_is_rejected(self):
        with pytest.raises(QueryError, match="empty filter"):
            query.validate({"not": MATCH_ALL})

    def test_empty_or_is_rejected(self):
        with pytest.raises(QueryError, match="at least one"):
            query.validate({"or": []})

    def test_ui_tree(self):
        tree = {
            "and": [
                {"field": "freq", "op": "gte", "value": 14},
                {"field": "freq", "op": "lte", "value": 14.35},
                {"field": "mode", "op": "in", "value": ["SSB", "CW"]},
                {"not": {"field": "gridsquare", "op": "empty", "value": True}},
            ]
        }
        assert query.validate(tree) == {
            "and": [
                cond("freq", "gte", "14"),
                cond("freq", "lte", "14.35"),
                cond("mode", "in", ["SSB", "CW"]),
                {"not": cond("gridsquare", "empty", True)},
            ]
        }

    def test_does_not_change_its_input(self):
        tree = {"and": [cond("freq", "gte", 14)]}
        query.validate(tree)
        assert tree == {"and": [cond("freq", "gte", 14)]}

    @pytest.mark.parametrize(
        "node",
        [
            "band:20m",
            ["and"],
            None,
            {"and": "a"},
            {"and": [], "or": []},
            {"field": "call", "op": "eq"},
            {"field": "call", "op": "eq", "value": "A", "extra": 1},
            {"field": 1, "op": "eq", "value": "A"},
            {"field": "call", "op": ["eq"], "value": "A"},
            {"field": "call", "op": "regex", "value": "A"},
            {"field": "call", "op": "eq", "value": 1},
            {"field": "call", "op": "eq", "value": None},
            {"field": "freq", "op": "eq", "value": True},
            {"field": "freq", "op": "eq", "value": "nan"},
            {"field": "freq", "op": "eq", "value": [1]},
            {"field": "call", "op": "in", "value": []},
            {"field": "call", "op": "in", "value": "A"},
            {"field": "call", "op": "in", "value": ["A"] * 101},
            {"field": "call", "op": "empty", "value": "yes"},
            {"field": "call", "op": "glob", "value": "a *"},
            {"field": "call", "op": "eq", "value": "x" * 201},
            {"field": "review", "op": "eq", "value": "OK"},
        ],
    )
    def test_rejects(self, node):
        with pytest.raises(QueryError):
            query.validate(node)

    @pytest.mark.parametrize(
        "field",
        [
            "owner__password",
            "owner",
            "owner_id",
            "call__regex",
            "time_on__date",
            "id",
            "uuid",
            "source_record",
            "extra.__class__",
            "extra.A-B",
            "extra.A__B",
            "extra.",
            "extra.1A",
            "extra.A__REGEX",
        ],
    )
    def test_only_listed_fields_can_be_reached(self, field):
        with pytest.raises(QueryError):
            query.validate(cond(field, "eq", "1"))

    def test_too_deep(self):
        node = cond("call", "eq", "A")
        for _ in range(12):
            node = {"and": [node, cond("call", "eq", "B")]}
            node = {"or": [node, cond("call", "eq", "C")]}
        with pytest.raises(QueryError, match="nest"):
            query.validate(node)

    def test_too_many_nodes(self):
        node = {"and": [cond("call", "eq", str(n)) for n in range(200)]}
        with pytest.raises(QueryError, match="parts"):
            query.validate(node)


class TestFormat:
    @pytest.mark.parametrize(
        "text",
        [
            "band:20m mode:SSB,CW pota_ref:US-0001 -call:K1* date>=2024-01-01",
            "sota_ref:G/LD-* OR wwff_ref:GFF-*",
            'name~"john smith" -contest_id:CQ-WW-CW',
            "has:gridsquare missing:extra.QSLMSG",
            "call:a (call:b OR call:c)",
            "NOT (call:a OR call:b)",
            "NOT (call:a call:b) call:c",
            "call:a call:b OR call:c",
            "freq>=14 freq<=14.35",
            'call:"OR" call:"-x" call:"a*" call:"a..b" call:"" call:","',
        ],
    )
    def test_reads_back_as_written(self, text):
        assert query.format_filter(query.parse(text)) == text

    def test_match_all_is_empty(self):
        assert query.format_filter(MATCH_ALL) == ""

    def test_ranges_are_spelt_out(self):
        assert query.format_filter(query.parse("freq:14..14.35")) == (
            "freq>=14 freq<=14.35"
        )

    def test_aliases_are_spelt_out(self):
        assert query.format_filter(query.parse("pota:US-0001")) == (
            "pota_ref:US-0001"
        )

    def test_rejects_invalid_trees(self):
        with pytest.raises(QueryError):
            query.format_filter(cond("bogus", "eq", "x"))


# Trees for the round trip property, covering each kind of field


_text = st.text(max_size=12)
_glob = st.from_regex(
    r"[A-Za-z0-9/\-]{0,4}[*?][A-Za-z0-9/*?\-]{0,4}", fullmatch=True
)
_decimal = st.decimals(
    min_value=-(10**6), max_value=10**6, allow_nan=False, allow_infinity=False
).map(str)
_int = st.integers(min_value=-(10**6), max_value=10**6)
_datetime = st.datetimes(timezones=st.just(UTC)).map(lambda d: d.isoformat())
_date = st.dates().map(lambda d: d.isoformat())
_empty = st.booleans()


def _conditions(fields, ops):
    return st.builds(cond, st.sampled_from(fields), st.just(ops[0]), ops[1])


def _with_in(values):
    return st.lists(values, min_size=1, max_size=4)


TEXT_FIELDS = ["call", "name", "mode", "extra.QSLMSG"]
LIST_FIELDS = ["pota_ref", "my_pota_ref"]

conditions = st.one_of(
    *(
        _conditions(TEXT_FIELDS, (op, _text))
        for op in ("eq", "contains", "startswith")
    ),
    _conditions(TEXT_FIELDS + LIST_FIELDS, ("glob", _glob)),
    _conditions(TEXT_FIELDS + LIST_FIELDS, ("in", _with_in(_text))),
    _conditions(TEXT_FIELDS + LIST_FIELDS, ("empty", _empty)),
    _conditions(LIST_FIELDS, ("has", _text)),
    *(
        _conditions(["freq", "lat", "tx_pwr"], (op, _decimal))
        for op in ("eq", "gt", "gte", "lt", "lte")
    ),
    _conditions(["freq"], ("in", _with_in(_decimal))),
    *(
        _conditions(["dxcc", "stx", "cqz"], (op, _int))
        for op in ("eq", "gt", "lte")
    ),
    _conditions(["dxcc"], ("in", _with_in(_int))),
    _conditions(["time_on", "time_off"], ("gte", _datetime)),
    _conditions(["time_on"], ("eq", _datetime)),
    _conditions(["date"], ("lt", _date)),
    _conditions(["date"], ("in", _with_in(_date))),
    _conditions(["location", "session"], ("eq", _int)),
    _conditions(["location", "freq", "time_on"], ("empty", _empty)),
    _conditions(
        ["review"],
        ("eq", st.sampled_from(["ok", "needs_review", "reviewed"])),
    ),
)

trees = st.recursive(
    conditions,
    lambda children: st.one_of(
        st.lists(children, max_size=4).map(lambda nodes: {"and": nodes}),
        st.lists(children, min_size=1, max_size=4).map(
            lambda nodes: {"or": nodes}
        ),
        children.map(lambda node: {"not": node}),
    ),
    max_leaves=12,
)


def _validated(tree):
    try:
        return query.validate(tree)
    except QueryError:
        assume(False)


class TestRoundTrip:
    @given(trees)
    def test_parse_reads_back_format(self, tree):
        canonical = _validated(tree)
        assert query.parse(query.format_filter(canonical)) == canonical

    @given(trees)
    def test_validate_is_idempotent(self, tree):
        canonical = _validated(tree)
        assert query.validate(canonical) == canonical

    @given(st.text(max_size=60))
    def test_parse_never_crashes(self, text):
        # Anything typed in either parses or gives a QueryError
        try:
            tree = query.parse(text)
        except QueryError:
            return
        assert query.validate(tree) == tree
        assert query.parse(query.format_filter(tree)) == tree

    @given(_text)
    def test_any_text_value_can_be_searched_for(self, value):
        tree = cond("name", "eq", value)
        assert query.parse(query.format_filter(tree)) == tree


# Running filters against the database

T0 = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def station(user):
    callsign = Callsign.objects.create(call="M0WME")
    CallsignAllocation.objects.create(
        callsign=callsign,
        user=user,
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
    )
    return callsign


@pytest.fixture
def location(user):
    location = Location(owner=user, name="Home")
    location.save()
    return location


@pytest.fixture
def session(user):
    session = LogSession(owner=user, name="CQ WW")
    session.save()
    return session


@pytest.fixture
def qsos(user, other_user, station, location, session):
    """A handful of QSOs for user, by name, and one for other_user"""

    def log(owner, location=None, session=None, **values):
        values.setdefault("station_callsign", "M0WME")
        return services.log_qso(
            owner,
            values,
            accept_warnings=True,
            location=location,
            session=session,
        )

    logged = {
        "k1abc": log(
            user,
            call="K1ABC",
            time_on=T0,
            freq=Decimal("14.074"),
            mode="FT8",
            name="John Smith",
            gridsquare="FN31pr",
            pota_ref="US-0001,US-0002",
            dxcc=291,
            extra={"QSLMSG": "TNX FER QSO"},
        ),
        "m0xyz": log(
            user,
            call="M0XYZ",
            time_on=datetime(2024, 6, 2, 9, 30, tzinfo=UTC),
            freq=Decimal("7.074"),
            mode="FT8",
            pota_ref="US-0001@US-ME",
            sota_ref="G/LD-001",
            dxcc=223,
        ),
        "k1zz": log(
            user,
            call="k1zz",
            time_on=datetime(2024, 1, 15, 23, 59, 59, tzinfo=UTC),
            band="20m",
            mode="SSB",
            location=location,
        ),
        "dl1abc": log(
            user,
            call="DL1ABC",
            time_on=datetime(2023, 12, 31, tzinfo=UTC),
            freq=Decimal("21.2"),
            mode="CW",
            station_callsign="G0XXX",
            wwff_ref="DLFF-0001",
            session=session,
        ),
    }
    log(
        other_user,
        call="K1ABC",
        time_on=T0,
        freq=Decimal("14.074"),
        mode="FT8",
        pota_ref="US-0001",
    )
    return logged


@pytest.fixture
def found(user, qsos):
    """The names of the user's QSOs a filter picks out"""
    names = {qso.pk: name for name, qso in qsos.items()}

    def _found(text):
        matched = QSO.objects.filter(owner=user).filter(
            query.compile_filter(query.parse(text))
        )
        return {names[pk] for pk in matched.values_list("pk", flat=True)}

    return _found


ALL = {"k1abc", "m0xyz", "k1zz", "dl1abc"}


@pytest.mark.django_db
class TestCompile:
    def test_match_all(self, found):
        assert found("") == ALL

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("k1abc", {"k1abc"}),
            ("call:K1ZZ", {"k1zz"}),
            ("name:john*", {"k1abc"}),
            ("name:JOHN", set()),
            ("name~smith", {"k1abc"}),
            ("name^JOHN", {"k1abc"}),
            ("call:K1*", {"k1abc", "k1zz"}),
            ("call:?0XYZ", {"m0xyz"}),
            ("call:K1", set()),
            ("call:k1abc,dl1abc", {"k1abc", "dl1abc"}),
            ("mode:ft8", {"k1abc", "m0xyz"}),
            ("grid:fn31PR", {"k1abc"}),
        ],
    )
    def test_text(self, found, text, expected):
        assert found(text) == expected

    def test_wildcards_match_regex_characters_literally(self, found):
        assert found("sota:G/LD-00?") == {"m0xyz"}
        assert found("call:K1.BC") == set()
        assert found('call:"K1*"') == set()

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("freq>14", {"k1abc", "dl1abc"}),
            ("freq:14.074", {"k1abc"}),
            ("freq:14.0740", {"k1abc"}),
            ("freq<=7.074", {"m0xyz"}),
            ("freq:7..14.1", {"m0xyz", "k1abc"}),
            ("freq:7.074,21.2", {"m0xyz", "dl1abc"}),
            ("dxcc:291", {"k1abc"}),
            ("dxcc>=223", {"k1abc", "m0xyz"}),
        ],
    )
    def test_numbers(self, found, text, expected):
        assert found(text) == expected

    def test_not_includes_missing_numbers(self, found):
        # Like Django's exclude(), NOT of a comparison includes QSOs with no
        # value at all, k1zz has no frequency
        assert found("-freq>14") == {"m0xyz", "k1zz"}
        assert found("missing:freq") == {"k1zz"}
        assert found("has:freq -freq>14") == {"m0xyz"}

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("date:2024-06-01", {"k1abc"}),
            ("date>=2024-06-01", {"k1abc", "m0xyz"}),
            ("date:2024-01-01..2024-01-31", {"k1zz"}),
            ("date<2024-01-01", {"dl1abc"}),
            ("date:2024-01-15,2023-12-31", {"k1zz", "dl1abc"}),
            # A date compared with a time is midnight UTC on that date
            ("time_on>=2024-06-01", {"k1abc", "m0xyz"}),
            ("time_on<2024-01-16", {"k1zz", "dl1abc"}),
            ("time_on<2024-01-15T23:59:59Z", {"dl1abc"}),
            ("time_on:2024-06-01T12:00:00Z", {"k1abc"}),
            ("time_on:2024-06-01T13:00:00+01:00", {"k1abc"}),
            # Without a timezone a time is UTC
            ("time_on>2024-06-02T09:00:00", {"m0xyz"}),
        ],
    )
    def test_dates_and_times(self, found, text, expected):
        assert found(text) == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("pota:US-0001", {"k1abc", "m0xyz"}),
            ("pota:us-0002", {"k1abc"}),
            ("pota:US-000", set()),
            ("pota:US-00011", set()),
            ("pota:US-0002,US-0003", {"k1abc"}),
            ("pota:US-*", {"k1abc", "m0xyz"}),
            ("pota~0002", {"k1abc"}),
            ('pota:"US-0001@US-ME"', {"m0xyz"}),
            ("missing:pota", {"k1zz", "dl1abc"}),
        ],
    )
    def test_lists(self, found, text, expected):
        assert found(text) == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("has:gridsquare", {"k1abc"}),
            ("missing:gridsquare", ALL - {"k1abc"}),
            ("has:dxcc", {"k1abc", "m0xyz"}),
            ("missing:sota", ALL - {"m0xyz"}),
            ("has:extra.QSLMSG", {"k1abc"}),
            ("missing:extra.QSLMSG", ALL - {"k1abc"}),
            ("extra.qslmsg~fer", {"k1abc"}),
            ("extra.QSLMSG:tnx*", {"k1abc"}),
            ('extra.QSLMSG:"tnx fer qso"', {"k1abc"}),
            ("extra.OTHER:x", set()),
        ],
    )
    def test_empty_and_extra(self, found, text, expected):
        assert found(text) == expected

    def test_location_session_and_review(self, found, location, session, qsos):
        assert found(f"location:{location.pk}") == {"k1zz"}
        assert found("has:location") == {"k1zz"}
        assert found(f"session:{session.pk}") == {"dl1abc"}
        assert found(f"session:{session.pk + 1000}") == set()
        # G0XXX isn't held by the user, so that QSO was flagged
        assert qsos["dl1abc"].review_status == "needs_review"
        assert found("review:needs_review") == {"dl1abc"}
        assert found("review:ok,reviewed") == ALL - {"dl1abc"}

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("mode:FT8 freq>10", {"k1abc"}),
            ("mode:FT8 OR mode:CW", {"k1abc", "m0xyz", "dl1abc"}),
            ("mode:FT8 -pota:US-0002", {"m0xyz"}),
            ("NOT (mode:FT8 OR mode:CW)", {"k1zz"}),
            ("(call:K1* OR call:M0*) date>=2024-06-01", {"k1abc", "m0xyz"}),
            ("call!=K1ABC", ALL - {"k1abc"}),
        ],
    )
    def test_combinations(self, found, text, expected):
        assert found(text) == expected


@pytest.mark.django_db
class TestOwnerScoping:
    @pytest.mark.parametrize(
        "text",
        [
            "",
            "K1ABC",
            "pota:US-0001",
            "-call:NOBODY",
            "has:freq OR missing:freq",
        ],
    )
    def test_logbooks_only_show_the_owners_qsos(
        self, user, other_user, qsos, text
    ):
        logbook = Logbook(owner=user, name="Filtered")
        logbook.set_query(text)
        logbook.save()
        others = QSO.objects.filter(owner=other_user)
        assert others.exists()
        assert not logbook.qsos().filter(pk__in=others).exists()
        assert set(logbook.qsos().values_list("owner", flat=True)) <= {user.pk}
