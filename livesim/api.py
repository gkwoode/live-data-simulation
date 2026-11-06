"""REST API: stream control, record queries (JSON / CSV / NDJSON), schemas, SQL Lab and sinks."""

from __future__ import annotations

import asyncio
import csv
import io
import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from .chaos import ISSUES
from .connections import build_connections
from .engine import MAX_RATE, Engine
from .schema import POWERBI_TYPES, SYSTEM_FIELDS, json_schema, spark_ddl, sql_ddl
from .sinks import SinkError
from .util import iso, parse_iso


class ChaosPatch(BaseModel):
    enabled: bool | None = None
    rate: float | None = Field(None, ge=0, le=1, description="Share of records that receive one data-quality issue")
    issues: list[str] | None = Field(None, description=f"Any of: {', '.join(ISSUES)}")


class StreamPatch(BaseModel):
    running: bool | None = None
    rate: float | None = Field(None, gt=0, le=MAX_RATE, description="Events per second (before the time-of-day pattern)")
    seasonality: bool | None = Field(None, description="Follow a realistic time-of-day / day-of-week traffic pattern")
    chaos: ChaosPatch | None = None


class BackfillRequest(BaseModel):
    days: float = Field(7, gt=0, le=365, description="How far back to generate history")
    events: int | None = Field(
        None, ge=1, le=2_000_000, description="Number of events; defaults to the stream's typical daily volume x days"
    )


class SqlRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=20_000)
    limit: int = Field(1000, ge=1, le=10_000)


class LakeConfig(BaseModel):
    enabled: bool
    format: Literal["jsonl", "csv", "parquet"] = "jsonl"
    roll_seconds: int = Field(60, ge=5, le=3600)
    retention_hours: float = Field(24, ge=0.1, le=24 * 90)
    streams: list[str] | None = None


class SqlConfig(BaseModel):
    enabled: bool
    url: str = ""
    db_schema: str | None = Field(None, alias="schema")
    streams: list[str] | None = None
    copy_existing: bool = Field(True, description="Also copy rows already in the local store when enabling")

    model_config = {"populate_by_name": True}


class KafkaConfig(BaseModel):
    enabled: bool
    bootstrap_servers: str = ""
    topic_prefix: str = "livesim."
    single_topic: str | None = None
    config: dict[str, str | int | float | bool] = Field(
        default_factory=dict, description="Extra librdkafka settings, e.g. SASL for Event Hubs"
    )
    streams: list[str] | None = None


class WebhookConfig(BaseModel):
    url: str
    format: Literal["array", "powerbi", "ndjson", "envelope"] = "array"
    fields: list[str] | None = None
    batch_size: int = Field(100, ge=1, le=10_000)
    interval_sec: float = Field(2.0, ge=0.2, le=300)
    headers: dict[str, str] = Field(default_factory=dict)


def _csv_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def build_router(engine: Engine) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    store = engine.store

    def runtime(name: str):
        try:
            return engine.runtime(name)
        except KeyError as exc:
            raise HTTPException(404, str(exc.args[0])) from None

    def all_columns(rt) -> list[str]:
        return [f.name for f in rt.fields] + [f.name for f in SYSTEM_FIELDS]

    def records_query(rt, after_seq, since, until, order, fields):
        columns = all_columns(rt)
        if fields:
            wanted = [f.strip() for f in fields.split(",") if f.strip()]
            unknown = [f for f in wanted if f not in columns]
            if unknown:
                raise HTTPException(400, f"Unknown field(s): {', '.join(unknown)}")
            columns = wanted if "_seq" in wanted else [*wanted, "_seq"]
        clauses, params = [], []
        if after_seq is not None:
            clauses.append('"_seq" > ?')
            params.append(after_seq)
        try:
            if since:
                clauses.append('"event_time" >= ?')
                params.append(iso(parse_iso(since)))
            if until:
                clauses.append('"event_time" < ?')
                params.append(iso(parse_iso(until)))
        except ValueError:
            raise HTTPException(400, "since/until must be ISO-8601 dates or datetimes, e.g. 2026-01-31T00:00:00Z") from None
        direction = order or ("asc" if after_seq is not None else "desc")
        return columns, " AND ".join(clauses), params, f'"_seq" {direction.upper()}', direction

    # -- overview & control ------------------------------------------------------------------

    @router.get("/health", tags=["System"])
    def health():
        return {"status": "ok"}

    @router.get("/overview", tags=["System"], summary="Totals for the whole simulator")
    def overview():
        return engine.overview()

    @router.get("/streams", tags=["Streams"], summary="Every stream with configuration and live statistics")
    def list_streams(series: bool = True):
        return [rt.summary(with_series=series) for rt in engine.streams.values()]

    @router.get("/streams/{name}", tags=["Streams"], summary="One stream: metadata, fields, configuration and statistics")
    def get_stream(name: str):
        rt = runtime(name)
        return {**rt.summary(), "fields": [f.as_dict() for f in rt.fields + list(SYSTEM_FIELDS)], "chaos_issues": ISSUES}

    @router.patch("/streams/{name}", tags=["Streams"], summary="Change rate, pause/resume, seasonality or data-quality chaos")
    def patch_stream(name: str, patch: StreamPatch):
        runtime(name)
        try:
            rt = engine.update_stream(name, patch.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return rt.summary(with_series=False)

    @router.post("/streams/{name}/start", tags=["Streams"])
    def start_stream(name: str):
        runtime(name)
        return engine.update_stream(name, {"running": True}).summary(with_series=False)

    @router.post("/streams/{name}/stop", tags=["Streams"])
    def stop_stream(name: str):
        runtime(name)
        return engine.update_stream(name, {"running": False}).summary(with_series=False)

    @router.post("/control/{action}", tags=["Streams"], summary="start-all or stop-all")
    def control(action: Literal["start-all", "stop-all"]):
        engine.set_all_running(action == "start-all")
        return engine.overview()

    @router.post("/streams/{name}/backfill", tags=["Streams"], summary="Generate historical data in the background")
    async def backfill(name: str, request: BackfillRequest):
        runtime(name)
        try:
            return await engine.backfill(name, request.days, request.events)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.delete("/streams/{name}/records", tags=["Streams"], summary="Delete stored records (the stream keeps running)")
    async def reset(name: str):
        runtime(name)
        return {"deleted": await engine.reset_stream(name)}

    # -- data access -------------------------------------------------------------------------

    @router.get("/streams/{name}/latest", tags=["Data"], summary="Most recent records from memory (no database round trip)")
    def latest(name: str, n: int = Query(50, ge=1, le=200)):
        rt = runtime(name)
        return list(rt.recent)[-n:]

    @router.get("/streams/{name}/records", tags=["Data"], summary="Query stored records as JSON (cursor-based incremental loads)")
    def records(
        name: str,
        limit: int = Query(1000, ge=1, le=50_000),
        after_seq: int | None = Query(None, ge=0, description="Return records with _seq greater than this cursor (ascending)"),
        since: str | None = Query(None, description="event_time >= this ISO-8601 instant"),
        until: str | None = Query(None, description="event_time < this ISO-8601 instant"),
        order: Literal["asc", "desc"] | None = Query(
            None, description="Default: asc with a cursor, otherwise desc (newest first)"
        ),
        fields: str | None = Query(None, description="Comma-separated subset of fields"),
        envelope: bool = Query(True, description="false returns a bare JSON array"),
    ):
        """Incremental pattern: start with `after_seq=0`, then pass `next_after_seq` back until `has_more` is false."""
        rt = runtime(name)
        columns, where, params, order_sql, direction = records_query(rt, after_seq, since, until, order, fields)
        rows = store.query(name, columns, where=where, params=params, order=order_sql, limit=limit)
        if not envelope:
            return Response(json.dumps(rows, ensure_ascii=False), media_type="application/json")
        seqs = [r["_seq"] for r in rows]
        payload = {
            "stream": name,
            "count": len(rows),
            "order": direction,
            "next_after_seq": max(seqs + [after_seq or 0]),
            "has_more": len(rows) == limit,
            "records": rows,
        }
        return Response(json.dumps(payload, ensure_ascii=False), media_type="application/json")

    def stream_rows(name, columns, where, params, order_sql, limit, fmt):
        def generate():
            if fmt == "csv":
                buffer = io.StringIO()
                writer = csv.writer(buffer)
                writer.writerow(columns)
                yield buffer.getvalue()
                for chunk in store.iter_rows(name, columns, where=where, params=params, order=order_sql, limit=limit):
                    buffer.seek(0)
                    buffer.truncate(0)
                    writer.writerows([[_csv_cell(v) for v in row] for row in chunk])
                    yield buffer.getvalue()
            else:
                for chunk in store.iter_rows(name, columns, where=where, params=params, order=order_sql, limit=limit):
                    yield "".join(json.dumps(dict(zip(columns, row, strict=True)), ensure_ascii=False) + "\n" for row in chunk)

        media = "text/csv; charset=utf-8" if fmt == "csv" else "application/x-ndjson"
        return StreamingResponse(
            generate(), media_type=media, headers={"Content-Disposition": f'inline; filename="{name}.{fmt}"'}
        )

    @router.get(
        "/streams/{name}/records.csv", tags=["Data"], summary="Stored records as CSV (Power BI / Excel 'From Web', pandas)"
    )
    def records_csv(
        name: str,
        limit: int = Query(10_000, ge=1, le=1_000_000),
        after_seq: int | None = Query(None, ge=0),
        since: str | None = None,
        until: str | None = None,
        order: Literal["asc", "desc"] | None = None,
        fields: str | None = None,
    ):
        rt = runtime(name)
        columns, where, params, order_sql, _ = records_query(rt, after_seq, since, until, order, fields)
        return stream_rows(name, columns, where, params, order_sql, limit, "csv")

    @router.get("/streams/{name}/records.ndjson", tags=["Data"], summary="Stored records as newline-delimited JSON")
    def records_ndjson(
        name: str,
        limit: int = Query(10_000, ge=1, le=1_000_000),
        after_seq: int | None = Query(None, ge=0),
        since: str | None = None,
        until: str | None = None,
        order: Literal["asc", "desc"] | None = None,
        fields: str | None = None,
    ):
        rt = runtime(name)
        columns, where, params, order_sql, _ = records_query(rt, after_seq, since, until, order, fields)
        return stream_rows(name, columns, where, params, order_sql, limit, "ndjson")

    @router.get(
        "/streams/{name}/schema", tags=["Data"], summary="Schema as fields, JSON Schema, Spark DDL, SQL DDL and Power BI types"
    )
    def schema(name: str):
        rt = runtime(name)
        fields = rt.fields + list(SYSTEM_FIELDS)
        return {
            "stream": name,
            "fields": [f.as_dict() for f in fields],
            "key_fields": list(rt.cls.key_fields),
            "json_schema": json_schema(name, rt.cls.title, fields),
            "spark_ddl": spark_ddl(fields),
            "postgres_ddl": sql_ddl(name, fields, "postgres"),
            "sqlserver_ddl": sql_ddl(name, fields, "sqlserver"),
            "powerbi_streaming_fields": [{"name": f.name, "type": POWERBI_TYPES[f.type]} for f in fields],
        }

    # -- reference tables --------------------------------------------------------------------

    def reference_table(table: str):
        try:
            return engine.ref.tables[table]
        except KeyError:
            raise HTTPException(404, f"Unknown reference table {table!r}. Available: {', '.join(engine.ref.tables)}") from None

    @router.get("/reference", tags=["Reference data"], summary="Dimension tables for star-schema modelling")
    def reference_list():
        return [
            {
                "name": t.name,
                "title": t.title,
                "description": t.description,
                "key": t.key,
                "rows": len(t.rows),
                "fields": [f.as_dict() for f in t.fields],
            }
            for t in engine.ref.tables.values()
        ]

    @router.get("/reference/{table}.csv", tags=["Reference data"])
    def reference_csv(table: str):
        t = reference_table(table)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        names = [f.name for f in t.fields]
        writer.writerow(names)
        writer.writerows([[_csv_cell(row[n]) for n in names] for row in t.rows])
        return Response(buffer.getvalue(), media_type="text/csv; charset=utf-8")

    @router.get("/reference/{table}", tags=["Reference data"])
    def reference_json(table: str, envelope: bool = True):
        t = reference_table(table)
        if not envelope:
            return Response(json.dumps(t.rows, ensure_ascii=False), media_type="application/json")
        return Response(
            json.dumps({"table": t.name, "count": len(t.rows), "records": t.rows}, ensure_ascii=False),
            media_type="application/json",
        )

    # -- SQL Lab -------------------------------------------------------------------------------

    @router.get("/sql/tables", tags=["SQL Lab"], summary="Tables and columns available to SQL Lab")
    def sql_tables():
        out = []
        for name, fields in store.tables.items():
            kind = "stream" if name in store.stream_tables else "reference"
            out.append(
                {
                    "name": name,
                    "kind": kind,
                    "rows": store.row_counts.get(name, 0),
                    "columns": [{"name": f.name, "type": f.type} for f in fields],
                }
            )
        return sorted(out, key=lambda t: (t["kind"] != "stream", t["name"]))

    @router.post("/sql", tags=["SQL Lab"], summary="Run one read-only SQLite query against the live store")
    def run_sql(request: SqlRequest):
        try:
            return store.run_readonly_sql(request.sql, request.limit)
        except TimeoutError as exc:
            raise HTTPException(408, str(exc)) from None
        except Exception as exc:  # sqlite3 errors carry a useful message for the user
            message = str(exc)
            if "not authorized" in message:
                message = "Only read-only SELECT queries are allowed in SQL Lab."
            raise HTTPException(400, message) from None

    # -- sinks ---------------------------------------------------------------------------------

    @router.get("/sinks", tags=["Sinks"], summary="Status of every destination")
    def sinks_status():
        return {**engine.sinks.status(), "subscribers": engine.hub.counts(), "tcp_port": engine.settings.tcp_port}

    async def configure(kind: str, config: dict[str, Any], **kwargs):
        try:
            await getattr(engine.sinks, f"configure_{kind}")(config, **kwargs)
        except SinkError as exc:
            raise HTTPException(400, str(exc)) from None
        engine._save_state()
        return engine.sinks.status()[kind]

    @router.put("/sinks/lake", tags=["Sinks"], summary="Configure the file landing zone")
    async def put_lake(config: LakeConfig):
        return await configure("lake", config.model_dump())

    @router.put("/sinks/sql", tags=["Sinks"], summary="Configure the SQL database sink (any SQLAlchemy URL)")
    async def put_sql(config: SqlConfig):
        data = config.model_dump()
        copy_existing = data.pop("copy_existing")
        data["schema"] = data.pop("db_schema")
        return await configure("sql", data, copy_existing=copy_existing)

    @router.put("/sinks/kafka", tags=["Sinks"], summary="Configure the Kafka sink")
    async def put_kafka(config: KafkaConfig):
        return await configure("kafka", config.model_dump())

    @router.put("/sinks/webhooks/{name}", tags=["Sinks"], summary="Push a stream to an HTTP endpoint (e.g. a Power BI push URL)")
    async def put_webhook(name: str, config: WebhookConfig):
        runtime(name)
        try:
            await engine.sinks.set_webhook(name, config.model_dump())
        except SinkError as exc:
            raise HTTPException(400, str(exc)) from None
        engine._save_state()
        return engine.sinks.status()["webhooks"][name]

    @router.delete("/sinks/webhooks/{name}", tags=["Sinks"])
    async def delete_webhook(name: str):
        runtime(name)
        removed = await engine.sinks.remove_webhook(name)
        engine._save_state()
        return {"removed": removed}

    @router.post("/sinks/webhooks/{name}/test", tags=["Sinks"], summary="Send one recent record to the configured webhook now")
    async def test_webhook(name: str):
        rt = runtime(name)
        sink = engine.sinks.webhooks.get(name)
        if sink is None:
            raise HTTPException(404, f"No webhook configured for {name}")
        if not rt.recent:
            raise HTTPException(409, f"{name} has not produced any records yet")
        try:
            status = await asyncio.to_thread(sink.post, [rt.recent[-1]])
        except Exception as exc:
            raise HTTPException(502, f"Webhook call failed: {exc}") from None
        return {"status": status}

    # -- connection hub ------------------------------------------------------------------------

    @router.get(
        "/connections", tags=["Connections"], summary="Ready-to-paste connection details for BI tools, code and databases"
    )
    def connections(request: Request, stream: str = Query("orders")):
        rt = runtime(stream)
        base = engine.settings.public_url.rstrip("/") if engine.settings.public_url else str(request.base_url).rstrip("/")
        return build_connections(engine, rt, base)

    @router.get("/chaos/issues", tags=["Streams"], summary="Data-quality issues the chaos engine can inject")
    def chaos_issues():
        return ISSUES

    return router
