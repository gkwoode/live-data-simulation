"""The $filter translation is checked by running the generated SQL against real SQLite rows."""

import sqlite3

import pytest

from livesim.odata_filter import ODataError, compile_filter, parse_orderby, parse_select

TYPES = {"name": "string", "amount": "float", "qty": "int", "flag": "bool", "event_time": "timestamp", "day": "date"}
ROWS = [
    ("Alpha", 10.5, 1, 1, "2026-01-01T08:00:00.000Z", "2026-01-01"),
    ("beta", 99.0, 5, 0, "2026-02-15T12:30:00.000Z", "2026-02-15"),
    ("Gamma O'Neil", 150.25, 12, 1, "2026-03-31T23:59:59.999Z", "2026-03-31"),
    ("delta", None, None, None, None, None),
]


@pytest.fixture(scope="module")
def db():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE t (name TEXT, amount REAL, qty INTEGER, flag INTEGER, event_time TEXT, day TEXT)")
    con.executemany("INSERT INTO t VALUES (?, ?, ?, ?, ?, ?)", ROWS)
    return con


def names(db, text):
    sql, params = compile_filter(text, TYPES)
    return sorted(r[0] for r in db.execute(f"SELECT name FROM t WHERE {sql}", params))


@pytest.mark.parametrize(
    "expression, expected",
    [
        ("amount gt 50", ["Gamma O'Neil", "beta"]),
        ("amount ge 10.5 and qty lt 5", ["Alpha"]),
        ("qty eq 5 or name eq 'Alpha'", ["Alpha", "beta"]),
        ("not (amount gt 50)", ["Alpha"]),
        ("flag eq true", ["Alpha", "Gamma O'Neil"]),
        ("flag", ["Alpha", "Gamma O'Neil"]),
        ("amount eq null", ["delta"]),
        ("amount ne null and flag eq false", ["beta"]),
        ("contains(name, 'O''Neil')", ["Gamma O'Neil"]),
        ("startswith(name, 'Al') or endswith(name, 'eta')", ["Alpha", "beta"]),
        ("tolower(name) eq 'alpha'", ["Alpha"]),
        ("length(name) eq 4", ["beta"]),
        ("indexof(name, 'mm') eq 2", ["Gamma O'Neil"]),
        ("substring(name, 1, 3) eq 'lph'", ["Alpha"]),
        ("name in ('Alpha', 'beta', 'nope')", ["Alpha", "beta"]),
        ("qty add 1 gt 5", ["Gamma O'Neil", "beta"]),
        ("qty mul 2 le 2", ["Alpha"]),
        ("qty mod 5 eq 0", ["beta"]),
        ("amount sub -10 gt 100", ["Gamma O'Neil", "beta"]),
        ("year(event_time) eq 2026 and month(event_time) eq 2", ["beta"]),
        ("hour(event_time) ge 12", ["Gamma O'Neil", "beta"]),
        ("event_time ge 2026-02-01T00:00:00Z", ["Gamma O'Neil", "beta"]),
        ("event_time lt 2026-02-15T13:30:00+01:00", ["Alpha"]),
        ("event_time ge datetimeoffset'2026-03-31T23:00:00Z'", ["Gamma O'Neil"]),
        ("event_time ge 2026-03-01", ["Gamma O'Neil"]),
        ("day eq 2026-01-01", ["Alpha"]),
        ("date(event_time) eq 2026-02-15", ["beta"]),
        ("floor(amount) eq 150 and ceiling(amount) eq 151", ["Gamma O'Neil"]),
        ("round(amount) eq 99", ["beta"]),
        ("event_time lt now()", ["Alpha", "Gamma O'Neil", "beta"]),
    ],
)
def test_filter_semantics(db, expression, expected):
    assert names(db, expression) == expected


@pytest.mark.parametrize(
    "expression",
    [
        "missing eq 1",
        "amount gt",
        "amount",
        "contains(name)",
        "name eq 'unterminated",
        "amount gt 1 extra",
        "unknownfn(name)",
        "amount $ 3",
    ],
)
def test_invalid_filters_raise(expression):
    with pytest.raises(ODataError):
        compile_filter(expression, TYPES)


def test_orderby_and_select():
    assert parse_orderby("amount desc, name", TYPES) == '"amount" DESC, "name" ASC'
    assert parse_select("name,amount,name", TYPES) == ["name", "amount"]
    assert parse_select("*", TYPES) is None
    with pytest.raises(ODataError):
        parse_orderby("amount sideways", TYPES)
    with pytest.raises(ODataError):
        parse_select("name,nope", TYPES)


def test_values_are_parameterised_not_inlined():
    sql, params = compile_filter("name eq 'x''); DROP TABLE t; --'", TYPES)
    assert "DROP" not in sql and params == ["x'); DROP TABLE t; --"]
