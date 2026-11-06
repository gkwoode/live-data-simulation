"""Translate OData v4 $filter, $orderby and $select into parameterised SQLite SQL.

Supports the subset that BI tools generate when they fold queries: comparisons, and/or/not,
in, arithmetic, string functions (contains, startswith, endswith, tolower, toupper, trim, length,
indexof, substring, concat), date parts (year, month, day, hour, minute, second, date), now(),
round/floor/ceiling, and typed or untyped date/datetime literals.

Timestamps are stored as fixed-width UTC strings ("2026-01-31T12:00:00.000Z"), so datetime
literals are normalised to the same shape and compared as text.
"""

from __future__ import annotations

import re
from typing import Any

from .util import iso, parse_iso, utcnow


class ODataError(ValueError):
    pass


_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<string>'(?:[^']|'')*')
  | (?P<datetime>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)
  | (?P<date>\d{4}-\d{2}-\d{2})
  | (?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?[mMdDfFlL]?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<punct>[(),])
  | (?P<minus>-)
""",
    re.VERBOSE,
)
_COMPARISON = {"eq": "=", "ne": "<>", "gt": ">", "ge": ">=", "lt": "<", "le": "<="}
_ADDITIVE = {"add": "+", "sub": "-"}
_MULTIPLICATIVE = {"mul": "*", "div": "/", "divby": "/", "mod": "%"}
_TYPED_LITERALS = {"datetime", "datetimeoffset", "date"}
_DATE_PARTS = {"year": (1, 4), "month": (6, 2), "day": (9, 2), "hour": (12, 2), "minute": (15, 2), "second": (18, 2)}


def _tokenize(text: str) -> list[tuple[str, str, int]]:
    tokens, pos = [], 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match:
            raise ODataError(f"Unexpected character {text[pos]!r} at position {pos}")
        kind = match.lastgroup
        if kind != "ws":
            tokens.append((kind, match.group(), pos))
        pos = match.end()
    return tokens


class _Parser:
    def __init__(self, text: str, types: dict[str, str]):
        self.text = text
        self.tokens = _tokenize(text)
        self.pos = 0
        self.types = types

    # -- token helpers -----------------------------------------------------------------------

    def peek(self, offset: int = 0) -> tuple[str, str, int] | None:
        i = self.pos + offset
        return self.tokens[i] if i < len(self.tokens) else None

    def next(self) -> tuple[str, str, int]:
        token = self.peek()
        if token is None:
            raise ODataError("Unexpected end of expression")
        self.pos += 1
        return token

    def keyword(self, *words: str) -> str | None:
        token = self.peek()
        if token and token[0] == "name" and token[1] in words:
            self.pos += 1
            return token[1]
        return None

    def expect(self, value: str) -> None:
        token = self.next()
        if token[1] != value:
            raise ODataError(f"Expected {value!r} at position {token[2]} but found {token[1]!r}")

    # -- grammar -----------------------------------------------------------------------------

    def parse(self) -> tuple:
        node = self.or_expr()
        if self.peek() is not None:
            token = self.peek()
            raise ODataError(f"Unexpected {token[1]!r} at position {token[2]}")
        return node

    def or_expr(self):
        node = self.and_expr()
        while self.keyword("or"):
            node = ("bool", "OR", node, self.and_expr())
        return node

    def and_expr(self):
        node = self.not_expr()
        while self.keyword("and"):
            node = ("bool", "AND", node, self.not_expr())
        return node

    def not_expr(self):
        if self.keyword("not"):
            return ("not", self.not_expr())
        return self.comparison()

    def comparison(self):
        left = self.additive()
        op = self.keyword(*_COMPARISON)
        if op:
            return ("cmp", op, left, self.additive())
        if self.keyword("in"):
            self.expect("(")
            items = [self.additive()]
            while self.peek() and self.peek()[1] == ",":
                self.next()
                items.append(self.additive())
            self.expect(")")
            return ("in", left, items)
        return left

    def additive(self):
        node = self.multiplicative()
        while True:
            op = self.keyword(*_ADDITIVE)
            if not op:
                return node
            node = ("arith", op, node, self.multiplicative())

    def multiplicative(self):
        node = self.unary()
        while True:
            op = self.keyword(*_MULTIPLICATIVE)
            if not op:
                return node
            node = ("arith", op, node, self.unary())

    def unary(self):
        token = self.peek()
        if token and token[0] == "minus":
            self.next()
            operand = self.unary()
            if operand[0] == "lit" and operand[1] in ("int", "float"):
                return ("lit", operand[1], -operand[2])
            return ("neg", operand)
        return self.primary()

    def primary(self):
        kind, value, pos = self.next()
        if kind == "punct" and value == "(":
            node = self.or_expr()
            self.expect(")")
            return node
        if kind == "string":
            return ("lit", "string", value[1:-1].replace("''", "'"))
        if kind == "datetime":
            return ("lit", "datetime", value)
        if kind == "date":
            return ("lit", "date", value)
        if kind == "number":
            text = value.rstrip("mMdDfFlL")
            if re.fullmatch(r"\d+", text):
                return ("lit", "int", int(text))
            return ("lit", "float", float(text))
        if kind == "name":
            nxt = self.peek()
            if value in _TYPED_LITERALS and nxt and nxt[0] == "string" and nxt[2] == pos + len(value):
                self.next()
                inner = nxt[1][1:-1]
                return ("lit", "date" if value == "date" else "datetime", inner)
            if value == "true" or value == "false":
                return ("lit", "bool", value == "true")
            if value == "null":
                return ("lit", "null", None)
            if nxt and nxt[1] == "(":
                self.next()
                args = []
                if self.peek() and self.peek()[1] != ")":
                    args.append(self.or_expr())
                    while self.peek() and self.peek()[1] == ",":
                        self.next()
                        args.append(self.or_expr())
                self.expect(")")
                return ("func", value.lower(), args)
            if value not in self.types:
                raise ODataError(f"Unknown property {value!r}. Valid properties: {', '.join(self.types)}")
            return ("field", value)
        raise ODataError(f"Unexpected {value!r} at position {pos}")


# -- SQL generation ---------------------------------------------------------------------------

_Sql = tuple[str, list[Any], str | None]  # sql, params, type


def _literal_param(node: tuple, target: str | None) -> Any:
    kind, value = node[1], node[2]
    if kind in ("datetime", "date"):
        try:
            if target == "date":
                return value[:10]
            return iso(parse_iso(value))
        except ValueError as exc:
            raise ODataError(f"Invalid date/time literal {value!r}") from exc
    if kind == "bool":
        return 1 if value else 0
    return value


class _Compiler:
    def __init__(self, types: dict[str, str]):
        self.types = types

    def compile(self, node: tuple, target: str | None = None) -> _Sql:
        tag = node[0]
        if tag == "lit":
            if node[1] == "null":
                return "NULL", [], "null"
            literal_type = {
                "string": "string",
                "int": "int",
                "float": "float",
                "bool": "bool",
                "datetime": "timestamp",
                "date": "date",
            }[node[1]]
            return "?", [_literal_param(node, target)], literal_type
        if tag == "field":
            return f'"{node[1]}"', [], self.types[node[1]]
        if tag == "bool":
            left, right = self.compile(node[2]), self.compile(node[3])
            return f"({self._predicate(left)} {node[1]} {self._predicate(right)})", left[1] + right[1], "bool"
        if tag == "not":
            inner = self.compile(node[1])
            return f"(NOT {self._predicate(inner)})", inner[1], "bool"
        if tag == "cmp":
            return self._comparison(node[1], node[2], node[3])
        if tag == "in":
            left = self.compile(node[1])
            items = [self.compile(item, left[2]) for item in node[2]]
            params = list(left[1])
            for item in items:
                params.extend(item[1])
            return f"({left[0]} IN ({', '.join(i[0] for i in items)}))", params, "bool"
        if tag == "arith":
            left, right = self.compile(node[2]), self.compile(node[3])
            op = node[1]
            if op == "divby":
                sql = f"(CAST({left[0]} AS REAL) / {right[0]})"
            else:
                sql = f"({left[0]} {(_ADDITIVE | _MULTIPLICATIVE)[op]} {right[0]})"
            result = "float" if "float" in (left[2], right[2]) or op == "divby" else "int"
            return sql, left[1] + right[1], result
        if tag == "neg":
            inner = self.compile(node[1])
            return f"(-{inner[0]})", inner[1], inner[2]
        if tag == "func":
            return self._function(node[1], node[2])
        raise ODataError(f"Unsupported expression {tag}")

    def _predicate(self, compiled: _Sql) -> str:
        sql, _params, kind = compiled
        if kind not in ("bool", None):
            raise ODataError("Expected a boolean expression (for example: amount gt 100)")
        return sql

    def _comparison(self, op: str, left_node: tuple, right_node: tuple) -> _Sql:
        if right_node[0] == "lit" and right_node[1] == "null" or left_node[0] == "lit" and left_node[1] == "null":
            other = left_node if right_node[0] == "lit" and right_node[1] == "null" else right_node
            compiled = self.compile(other)
            if op not in ("eq", "ne"):
                return "(0)", [], "bool"
            return f"({compiled[0]} IS {'NOT ' if op == 'ne' else ''}NULL)", compiled[1], "bool"
        # compile the non-literal side first so literals can adopt its type (datetimes, dates)
        if left_node[0] == "lit" and right_node[0] != "lit":
            right = self.compile(right_node)
            left = self.compile(left_node, right[2])
        else:
            left = self.compile(left_node)
            right = self.compile(right_node, left[2])
        return f"({left[0]} {_COMPARISON[op]} {right[0]})", left[1] + right[1], "bool"

    def _function(self, name: str, args: list[tuple]) -> _Sql:
        compiled = [self.compile(a) for a in args]

        def need(count: int | tuple[int, ...]) -> None:
            allowed = count if isinstance(count, tuple) else (count,)
            if len(compiled) not in allowed:
                raise ODataError(f"{name}() takes {' or '.join(map(str, allowed))} argument(s)")

        if name in ("contains", "startswith", "endswith", "indexof", "concat"):
            need(2)
            (a, pa, _), (b, pb, _) = compiled
            if name == "contains":
                return f"(instr({a}, {b}) > 0)", pa + pb, "bool"
            if name == "startswith":
                return f"(instr({a}, {b}) = 1)", pa + pb, "bool"
            if name == "endswith":
                return f"(substr({a}, -length({b})) = {b})", pa + pb + pb, "bool"
            if name == "indexof":
                return f"(instr({a}, {b}) - 1)", pa + pb, "int"
            return f"({a} || {b})", pa + pb, "string"
        if name in ("tolower", "toupper", "trim", "length"):
            need(1)
            a, pa, _ = compiled[0]
            fn = {"tolower": "lower", "toupper": "upper", "trim": "trim", "length": "length"}[name]
            return f"{fn}({a})", pa, "int" if name == "length" else "string"
        if name == "substring":
            need((2, 3))
            a, pa, _ = compiled[0]
            b, pb, _ = compiled[1]
            if len(compiled) == 3:
                c, pc, _ = compiled[2]
                return f"substr({a}, ({b}) + 1, {c})", pa + pb + pc, "string"
            return f"substr({a}, ({b}) + 1)", pa + pb, "string"
        if name in _DATE_PARTS:
            need(1)
            a, pa, _ = compiled[0]
            start, length = _DATE_PARTS[name]
            return f"CAST(substr({a}, {start}, {length}) AS INTEGER)", pa, "int"
        if name == "date":
            need(1)
            a, pa, _ = compiled[0]
            return f"substr({a}, 1, 10)", pa, "date"
        if name == "now":
            need(0)
            return "?", [iso(utcnow())], "timestamp"
        if name in ("round", "floor", "ceiling"):
            need(1)
            a, pa, _ = compiled[0]
            if name == "round":
                return f"round({a})", pa, "float"
            truncated = f"CAST({a} AS INTEGER)"  # the argument appears four times below, so repeat its params
            if name == "floor":
                return f"(CASE WHEN {a} < {truncated} THEN {truncated} - 1 ELSE {truncated} END)", pa * 4, "int"
            return f"(CASE WHEN {a} > {truncated} THEN {truncated} + 1 ELSE {truncated} END)", pa * 4, "int"
        raise ODataError(f"Unsupported function {name}()")


def compile_filter(text: str, types: dict[str, str]) -> tuple[str, list[Any]]:
    """Return (sql, params) for a $filter expression; types maps property name -> field type."""
    node = _Parser(text, types).parse()
    compiler = _Compiler(types)
    sql, params, kind = compiler.compile(node)
    if kind not in ("bool", None):
        raise ODataError("$filter must be a boolean expression, for example: amount gt 100")
    return sql, params


def parse_orderby(text: str | None, types: dict[str, str]) -> str:
    if not text:
        return ""
    parts = []
    for item in text.split(","):
        words = item.strip().split()
        if not words or len(words) > 2:
            raise ODataError(f"Invalid $orderby item {item!r}")
        name = words[0]
        if name not in types:
            raise ODataError(f"Unknown property {name!r} in $orderby")
        direction = words[1].lower() if len(words) == 2 else "asc"
        if direction not in ("asc", "desc"):
            raise ODataError(f"Invalid sort direction {words[1]!r}")
        parts.append(f'"{name}" {direction.upper()}')
    return ", ".join(parts)


def parse_select(text: str | None, types: dict[str, str]) -> list[str] | None:
    if not text or text.strip() == "*":
        return None
    names = [n.strip() for n in text.split(",") if n.strip()]
    unknown = [n for n in names if n not in types]
    if unknown:
        raise ODataError(f"Unknown property in $select: {', '.join(unknown)}")
    return list(dict.fromkeys(names))
