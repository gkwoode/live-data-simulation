"""OData v4 feed: the native connector for Power BI, Excel, Tableau and other BI tools."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote
from xml.sax.saxutils import escape

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from .engine import Engine
from .odata_filter import ODataError, compile_filter, parse_orderby, parse_select
from .schema import EDM_TYPES, SYSTEM_FIELDS, Field

JSON_TYPE = "application/json;odata.metadata=minimal;odata.streaming=true;IEEE754Compatible=false;charset=utf-8"
HEADERS = {"OData-Version": "4.0"}
NAMESPACE = "LiveSim"
_PATH = re.compile(r"(?P<set>[A-Za-z_][A-Za-z0-9_]*)(?:\((?P<key>[^)]*)\))?(?P<count>/\$count)?")
_UNSUPPORTED = ("$expand", "$apply", "$search", "$compute")


def _type_name(entity_set: str) -> str:
    return "".join(part.capitalize() for part in entity_set.split("_"))


def _error(status: int, message: str, code: str = "BadRequest") -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=HEADERS)


class EntitySet:
    def __init__(self, name: str, fields: list[Field], key: str, kind: str, title: str):
        self.name, self.fields, self.key, self.kind, self.title = name, fields, key, kind, title
        self.types = {f.name: f.type for f in fields}


def build_router(engine: Engine) -> APIRouter:
    router = APIRouter(include_in_schema=False)
    settings = engine.settings
    sets: dict[str, EntitySet] = {}
    for name, rt in engine.streams.items():
        sets[name] = EntitySet(name, rt.fields + list(SYSTEM_FIELDS), "_seq", "stream", rt.cls.title)
    for name, table in engine.ref.tables.items():
        sets[name] = EntitySet(name, table.fields, table.key, "reference", table.title)

    def root(request: Request) -> str:
        base = settings.public_url.rstrip("/") if settings.public_url else str(request.base_url).rstrip("/")
        return f"{base}/odata"

    def respond(payload: dict[str, Any]) -> Response:
        return Response(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), media_type=JSON_TYPE, headers=HEADERS)

    def service_document(request: Request) -> Response:
        return respond(
            {
                "@odata.context": f"{root(request)}/$metadata",
                "value": [{"name": s.name, "kind": "EntitySet", "url": s.name, "title": s.title} for s in sets.values()],
            }
        )

    def metadata() -> Response:
        parts = [
            '<?xml version="1.0" encoding="utf-8"?>',
            '<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">',
            "<edmx:DataServices>",
            f'<Schema Namespace="{NAMESPACE}" xmlns="http://docs.oasis-open.org/odata/ns/edm">',
        ]
        for s in sets.values():
            parts.append(f'<EntityType Name="{_type_name(s.name)}"><Key><PropertyRef Name="{s.key}"/></Key>')
            for f in s.fields:
                nullable = ' Nullable="false"' if f.name == s.key else ""
                precision = ' Precision="3"' if f.type == "timestamp" else ""
                parts.append(f'<Property Name="{escape(f.name)}" Type="{EDM_TYPES[f.type]}"{nullable}{precision}/>')
            parts.append("</EntityType>")
        parts.append('<EntityContainer Name="Container">')
        for s in sets.values():
            parts.append(f'<EntitySet Name="{s.name}" EntityType="{NAMESPACE}.{_type_name(s.name)}"/>')
        parts.append("</EntityContainer></Schema></edmx:DataServices></edmx:Edmx>")
        return Response("".join(parts), media_type="application/xml", headers=HEADERS)

    def where_clause(s: EntitySet, params: dict[str, str]) -> tuple[str, list[Any]]:
        text = params.get("$filter")
        return compile_filter(text, s.types) if text else ("", [])

    def key_value(s: EntitySet, raw: str) -> Any:
        raw = raw.strip()
        if "=" in raw:  # (key=value) form
            raw = raw.split("=", 1)[1].strip()
        if s.types[s.key] == "int":
            return int(raw)
        if raw.startswith("'") and raw.endswith("'"):
            return raw[1:-1].replace("''", "'")
        return raw

    def collection(request: Request, s: EntitySet) -> Response:
        q = dict(request.query_params)
        for option in _UNSUPPORTED:
            if option in q:
                return _error(501, f"{option} is not supported by this service", "NotImplemented")
        select = parse_select(q.get("$select"), s.types)
        columns = select or [f.name for f in s.fields]
        fetch = columns if s.key in columns else [*columns, s.key]
        where, params = where_clause(s, q)
        orderby = parse_orderby(q.get("$orderby"), s.types)
        try:
            top = int(q["$top"]) if "$top" in q else None
            skip = int(q.get("$skip", 0))
        except ValueError:
            raise ODataError("$top and $skip must be integers") from None
        if (top is not None and top < 0) or skip < 0:
            raise ODataError("$top and $skip must not be negative")
        page_size = max(1, settings.odata_page_size)
        limit = page_size if top is None else min(page_size, top)

        clauses, args = ([where], list(params)) if where else ([], [])
        token = q.get("$skiptoken")
        if orderby:
            order = f'{orderby}, "{s.key}" ASC'
            offset = skip
        else:
            order = f'"{s.key}" ASC'
            offset = 0 if token is not None else skip
            if token is not None:
                clauses.append(f'"{s.key}" > ?')
                args.append(key_value(s, token))
        rows = engine.store.query(
            s.name, fetch, where=" AND ".join(clauses), params=args, order=order, limit=limit + 1, offset=offset
        )
        more = len(rows) > limit
        rows = rows[:limit]
        remaining = None if top is None else top - len(rows)
        if remaining is not None and remaining <= 0:
            more = False

        context = f"{root(request)}/$metadata#{s.name}" + (f"({','.join(select)})" if select else "")
        payload: dict[str, Any] = {"@odata.context": context}
        if q.get("$count", "").lower() == "true":
            payload["@odata.count"] = engine.store.count(s.name, where, params)
        payload["value"] = [{c: row[c] for c in columns} for row in rows] if fetch is not columns else rows
        if more and rows:
            nxt = {k: v for k, v in q.items() if k not in ("$skip", "$skiptoken", "$top")}
            if orderby:
                nxt["$skip"] = str(skip + len(rows))
            else:
                nxt["$skiptoken"] = str(rows[-1][s.key])
            if remaining is not None:
                nxt["$top"] = str(remaining)
            query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in nxt.items())
            payload["@odata.nextLink"] = f"{root(request)}/{s.name}?{query}"
        return respond(payload)

    def count(request: Request, s: EntitySet) -> Response:
        where, params = where_clause(s, dict(request.query_params))
        return PlainTextResponse(str(engine.store.count(s.name, where, params)), headers=HEADERS)

    def entity(request: Request, s: EntitySet, raw_key: str) -> Response:
        try:
            value = key_value(s, raw_key)
        except ValueError:
            raise ODataError(f"Invalid key {raw_key!r}") from None
        rows = engine.store.query(s.name, [f.name for f in s.fields], where=f'"{s.key}" = ?', params=[value], limit=1)
        if not rows:
            return _error(404, f"No {s.name} entity with key {raw_key}", "NotFound")
        return respond({"@odata.context": f"{root(request)}/$metadata#{s.name}/$entity", **rows[0]})

    @router.get("/odata")
    def odata_root(request: Request):
        return service_document(request)

    @router.get("/odata/{path:path}")
    def odata_path(path: str, request: Request):
        if path in ("", "/"):
            return service_document(request)
        if path == "$metadata":
            return metadata()
        match = _PATH.fullmatch(path.rstrip("/"))
        if not match or match["set"] not in sets:
            return _error(404, f"Resource not found: {path}. See {root(request)} for the entity sets.", "NotFound")
        s = sets[match["set"]]
        try:
            if match["count"]:
                return count(request, s)
            if match["key"] is not None:
                return entity(request, s, match["key"])
            return collection(request, s)
        except ODataError as exc:
            return _error(400, str(exc))

    return router
