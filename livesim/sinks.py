"""Outbound sinks: the SQLite store, a file landing zone, any SQL database, Kafka and webhooks.

Every sink buffers records in memory and writes them in batches from a worker thread, so a
slow or unavailable destination never stalls the simulation. Failures are retried with
backoff (buffers are bounded; overflow is counted as dropped) and surfaced on the Sinks page.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import shutil
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from . import __version__
from .schema import SYSTEM_FIELDS, Field, TableDef
from .store import Store
from .util import UTC, iso, parse_iso, utcnow

log = logging.getLogger("livesim.sinks")
SECRET_MASK = "********"


class SinkError(RuntimeError):
    pass


class PartialWrite(Exception):
    def __init__(self, sent: int, remaining: dict[str, list[dict]], cause: Exception):
        super().__init__(str(cause))
        self.sent, self.remaining, self.cause = sent, remaining, cause


class BaseSink:
    kind = "base"
    interval = 1.0
    max_buffer = 200_000
    retry = True
    has_maintenance = False

    def __init__(self, streams: list[str] | None = None):
        self.streams = set(streams) if streams else None
        self.buffer: dict[str, list[dict]] = defaultdict(list)
        self.buffered = 0
        self.written = 0
        self.dropped = 0
        self.errors = 0
        self.failures = 0
        self.last_error: str | None = None
        self.last_error_at: str | None = None
        self.last_error_mono = 0.0
        self.last_write_at: str | None = None
        self.task: asyncio.Task | None = None

    @property
    def healthy(self) -> bool:
        """No failing writes right now and no error reported in the last minute."""
        return self.failures == 0 and (self.last_error is None or time.monotonic() - self.last_error_mono > 60)

    def wants(self, stream: str) -> bool:
        return self.streams is None or stream in self.streams

    def submit(self, stream: str, records: list[dict]) -> None:
        if self.buffered + len(records) > self.max_buffer:
            self.dropped += len(records)
            return
        self.buffer[stream].extend(records)
        self.buffered += len(records)

    def _take(self) -> dict[str, list[dict]]:
        batch, self.buffer, self.buffered = self.buffer, defaultdict(list), 0
        return dict(batch)

    def _requeue(self, batch: dict[str, list[dict]]) -> None:
        count = sum(len(v) for v in batch.values())
        if not self.retry or self.buffered + count > self.max_buffer:
            self.dropped += count
            return
        for stream, records in batch.items():
            self.buffer[stream] = records + self.buffer[stream]
        self.buffered += count

    def write(self, batch: dict[str, list[dict]]) -> int:  # runs in a worker thread
        raise NotImplementedError

    def maintain(self) -> None:  # periodic housekeeping, runs in a worker thread
        pass

    def close(self) -> None:
        pass

    def _record_error(self, exc: BaseException) -> None:
        self.failures += 1
        self.errors += 1
        self.last_error = f"{type(exc).__name__}: {exc}"[:600]
        self.last_error_at = iso(utcnow())
        self.last_error_mono = time.monotonic()
        if self.failures in (1, 10) or self.failures % 100 == 0:
            log.warning("%s sink error (%d in a row): %s", self.kind, self.failures, self.last_error)

    async def flush(self) -> None:
        if self.buffered:
            batch = self._take()
            try:
                self.written += await asyncio.to_thread(self.write, batch)
                self.failures = 0
                self.last_write_at = iso(utcnow())
            except PartialWrite as partial:
                self.written += partial.sent
                self._record_error(partial.cause)
                self._requeue(partial.remaining)
            except Exception as exc:
                self._record_error(exc)
                self._requeue(batch)
        if self.has_maintenance:
            try:
                await asyncio.to_thread(self.maintain)
            except Exception as exc:
                self._record_error(exc)

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.interval * min(2 ** min(self.failures, 5), 30))
            await self.flush()

    def start(self) -> None:
        self.task = asyncio.create_task(self.run(), name=f"sink:{self.kind}")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        try:
            await self.flush()
        except Exception as exc:  # final flush is best effort
            log.warning("Final flush of %s sink failed: %s", self.kind, exc)
        await asyncio.to_thread(self.close)

    def status(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "running": self.task is not None and not self.task.done(),
            "healthy": self.healthy,
            "written": self.written,
            "buffered": self.buffered,
            "dropped": self.dropped,
            "errors": self.errors,
            "last_error": self.last_error,
            "last_error_at": self.last_error_at,
            "last_write_at": self.last_write_at,
        }


# -- built-in SQLite store ---------------------------------------------------------------------


class StoreSink(BaseSink):
    kind = "sqlite"
    interval = 0.5
    max_buffer = 600_000

    def __init__(self, store: Store):
        super().__init__()
        self.store = store

    def write(self, batch):
        return self.store.insert_many(batch)

    def status(self):
        out = super().status()
        out.update(path=str(self.store.path.resolve()), size_bytes=self.store.size_bytes())
        return out


# -- file landing zone -----------------------------------------------------------------------


@dataclass
class _Part:
    tmp: Path
    final: Path
    opened: datetime
    rows: int = 0
    pending: list = field(default_factory=list)


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


class LakeSink(BaseSink):
    """Writes Hive-style partitions: <root>/<stream>/date=YYYY-MM-DD/hour=HH/part-*.{jsonl,csv,parquet}.

    Files are written under an ``_inprogress-`` name (ignored by Spark, Databricks Auto Loader
    and most file sources) and renamed to their final name when they roll.
    """

    kind = "lake"
    interval = 1.0
    has_maintenance = True

    def __init__(
        self,
        root: Path,
        fields: dict[str, list[Field]],
        fmt: str = "jsonl",
        roll_seconds: int = 60,
        retention_hours: float = 24.0,
        streams: list[str] | None = None,
    ):
        super().__init__(streams)
        if fmt not in ("jsonl", "csv", "parquet"):
            raise SinkError("Landing zone format must be jsonl, csv or parquet")
        if fmt == "parquet":
            try:
                import pyarrow  # noqa: F401
            except ImportError as exc:
                raise SinkError("Parquet output needs pyarrow: pip install pyarrow") from exc
        self.root = Path(root)
        self.fmt = fmt
        self.roll_seconds = max(5, int(roll_seconds))
        self.retention_hours = float(retention_hours)
        self.columns = {name: list(f) + list(SYSTEM_FIELDS) for name, f in fields.items()}
        self.parts: dict[str, _Part] = {}
        self.files_written = 0
        self._counter = 0
        self._last_cleanup = 0.0

    def _open_part(self, stream: str, now: datetime) -> _Part:
        folder = self.root / stream / f"date={now:%Y-%m-%d}" / f"hour={now:%H}"
        folder.mkdir(parents=True, exist_ok=True)
        self._counter += 1
        name = f"part-{now:%Y%m%dT%H%M%S}-{self._counter:05d}.{self.fmt}"
        part = _Part(tmp=folder / f"_inprogress-{name}", final=folder / name, opened=now)
        if self.fmt == "csv":
            with open(part.tmp, "w", encoding="utf-8", newline="") as fh:
                csv.writer(fh).writerow([f.name for f in self.columns[stream]])
        return part

    def write(self, batch):
        now = utcnow()
        written = 0
        for stream, records in batch.items():
            if not records:
                continue
            part = self.parts.get(stream)
            if part is not None and part.opened.strftime("%Y%m%d%H") != now.strftime("%Y%m%d%H"):
                self._finalize(stream, part)
                part = None
            if part is None:
                part = self.parts[stream] = self._open_part(stream, now)
            if self.fmt == "jsonl":
                with open(part.tmp, "a", encoding="utf-8", newline="\n") as fh:
                    fh.write("".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in records))
            elif self.fmt == "csv":
                names = [f.name for f in self.columns[stream]]
                with open(part.tmp, "a", encoding="utf-8", newline="") as fh:
                    csv.writer(fh).writerows([[_csv_value(r.get(n)) for n in names] for r in records])
            else:
                part.pending.extend(records)
            part.rows += len(records)
            written += len(records)
        return written

    def _write_parquet(self, stream: str, part: _Part) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        arrow_types = {
            "string": pa.string(),
            "int": pa.int64(),
            "float": pa.float64(),
            "bool": pa.bool_(),
            "timestamp": pa.timestamp("ms", tz="UTC"),
            "date": pa.date32(),
        }
        arrays = {}
        for f in self.columns[stream]:
            values = [r.get(f.name) for r in part.pending]
            if f.type == "timestamp":
                values = [parse_iso(v) if v else None for v in values]
            elif f.type == "date":
                values = [date.fromisoformat(v) if v else None for v in values]
            arrays[f.name] = pa.array(values, type=arrow_types[f.type])
        pq.write_table(pa.table(arrays), part.tmp, compression="snappy")

    def _finalize(self, stream: str, part: _Part) -> None:
        if self.fmt == "parquet":
            if not part.pending:
                return
            self._write_parquet(stream, part)
            part.pending = []
        if part.tmp.exists():
            os.replace(part.tmp, part.final)
            self.files_written += 1

    def maintain(self):
        now = utcnow()
        for stream, part in list(self.parts.items()):
            if (now - part.opened).total_seconds() >= self.roll_seconds or part.opened.hour != now.hour:
                self._finalize(stream, part)
                del self.parts[stream]
        if time.monotonic() - self._last_cleanup > 300:
            self._last_cleanup = time.monotonic()
            self._cleanup(now)

    def _cleanup(self, now: datetime) -> None:
        if self.retention_hours <= 0 or not self.root.exists():
            return
        cutoff = now - timedelta(hours=self.retention_hours)
        for stream_dir in self.root.iterdir():
            if not stream_dir.is_dir():
                continue
            for day_dir in stream_dir.glob("date=*"):
                for hour_dir in day_dir.glob("hour=*"):
                    try:
                        start = datetime.strptime(day_dir.name[5:] + hour_dir.name[5:], "%Y-%m-%d%H").replace(tzinfo=UTC)
                    except ValueError:
                        continue
                    if start + timedelta(hours=1) < cutoff:
                        shutil.rmtree(hour_dir, ignore_errors=True)
                if day_dir.exists() and not any(day_dir.iterdir()):
                    day_dir.rmdir()

    def close(self):
        for stream, part in list(self.parts.items()):
            self._finalize(stream, part)
        self.parts.clear()

    def status(self):
        out = super().status()
        out.update(path=str(self.root.resolve()), format=self.fmt, files_written=self.files_written, open_files=len(self.parts))
        return out


# -- any SQL database via SQLAlchemy ---------------------------------------------------------


def mask_url(url: str) -> str:
    try:
        from sqlalchemy.engine import make_url

        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return url.split("@")[-1] if "@" in url else url


class SqlSink(BaseSink):
    """Writes every stream (and the reference tables, once) into any SQLAlchemy-supported database."""

    kind = "sql"
    interval = 1.0

    def __init__(
        self,
        url: str,
        schema: str | None,
        fields: dict[str, list[Field]],
        reference: dict[str, TableDef],
        streams: list[str] | None = None,
    ):
        super().__init__(streams)
        try:
            import sqlalchemy as sa
        except ImportError as exc:
            raise SinkError("The SQL sink needs SQLAlchemy plus a driver, e.g. pip install sqlalchemy psycopg[binary]") from exc
        self.sa = sa
        self.url = url
        kwargs: dict[str, Any] = {"pool_pre_ping": True}
        if url.startswith("mssql+pyodbc"):
            kwargs["fast_executemany"] = True
        try:
            self.engine = sa.create_engine(url, **kwargs)
        except Exception as exc:
            raise SinkError(f"Invalid database URL or missing driver: {exc}") from exc
        self.dialect = self.engine.dialect.name
        self.schema = schema or None
        self.metadata = sa.MetaData(schema=self.schema)
        self.fields = {name: list(f) + list(SYSTEM_FIELDS) for name, f in fields.items() if self.wants(name)}
        self.stream_tables = {name: self._table(name, cols, None) for name, cols in self.fields.items()}
        self.reference = {t.name: (self._table(t.name, t.fields, t.key), t) for t in reference.values()}
        self.ready = False
        self.closed = False
        self.copy_state: dict[str, Any] | None = None

    def _type(self, f: Field):
        sa = self.sa
        if f.type == "string":
            return sa.UnicodeText() if f.role == "text" else sa.Unicode(400)
        if f.type == "int":
            return sa.BigInteger()
        if f.type == "float":
            return sa.Float(precision=53)
        if f.type == "bool":
            return sa.Boolean()
        if f.type == "timestamp":
            return sa.DateTime(timezone=self.dialect == "postgresql")
        return sa.Date()

    def _table(self, name: str, fields: list[Field], key: str | None):
        sa = self.sa
        columns = [sa.Column(f.name, self._type(f), primary_key=f.name == key, nullable=f.name != key) for f in fields]
        table = sa.Table(name, self.metadata, *columns)
        if key is None:
            sa.Index(f"ix_{name}_seq", table.c["_seq"])
        return table

    def _convert(self, fields: list[Field], records: list[dict]) -> list[dict]:
        aware = self.dialect == "postgresql"
        ts_cols = [f.name for f in fields if f.type == "timestamp"]
        date_cols = [f.name for f in fields if f.type == "date"]
        names = [f.name for f in fields]
        out = []
        for record in records:
            row = {n: record.get(n) for n in names}
            for c in ts_cols:
                if row[c]:
                    dt = parse_iso(row[c])
                    row[c] = dt if aware else dt.replace(tzinfo=None)
            for c in date_cols:
                if row[c]:
                    row[c] = date.fromisoformat(row[c])
            out.append(row)
        return out

    def initialize(self) -> None:
        """Connect, create missing tables and load the reference tables into empty ones."""
        sa = self.sa
        with self.engine.begin() as conn:
            if self.schema and self.dialect == "postgresql":
                conn.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"'))
        self.metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            for table, definition in self.reference.values():
                if conn.execute(sa.select(sa.func.count()).select_from(table)).scalar() == 0:
                    conn.execute(table.insert(), self._convert(definition.fields, definition.rows))
        self.ready = True

    def write(self, batch):
        if not self.ready:
            self.initialize()
        written = 0
        with self.engine.begin() as conn:
            for stream, records in batch.items():
                if records:
                    conn.execute(self.stream_tables[stream].insert(), self._convert(self.fields[stream], records))
                    written += len(records)
        return written

    def copy_history(self, store: Store, upto: dict[str, int]) -> None:
        """Copy rows that existed in the local store before this sink was enabled."""
        if not self.ready:
            self.initialize()
        streams = [s for s in upto if self.wants(s)]
        self.copy_state = {"running": True, "copied": 0, "total": sum(store.row_counts.get(s, 0) for s in streams)}
        try:
            for stream in streams:
                fields = self.fields[stream]
                names = [f.name for f in fields]
                table = self.stream_tables[stream]
                for chunk in store.iter_rows(stream, names, where="_seq <= ?", params=(upto[stream],), order="_seq", chunk=5000):
                    if self.closed:
                        return
                    with self.engine.begin() as conn:
                        conn.execute(table.insert(), self._convert(fields, [dict(zip(names, row, strict=True)) for row in chunk]))
                    self.copy_state["copied"] += len(chunk)
        finally:
            self.copy_state["running"] = False

    def close(self):
        self.closed = True
        self.engine.dispose()

    def status(self):
        out = super().status()
        out.update(url=mask_url(self.url), dialect=self.dialect, schema=self.schema, ready=self.ready, copy=self.copy_state)
        return out


# -- Kafka (and Kafka-compatible services: Redpanda, Event Hubs, Confluent Cloud, Fabric) ----


_kafka_log = logging.getLogger("livesim.kafka")


class KafkaSink(BaseSink):
    kind = "kafka"
    interval = 0.5
    retry = False  # librdkafka already retries delivery internally

    def __init__(
        self,
        bootstrap: str,
        topic_prefix: str,
        single_topic: str | None,
        extra: dict[str, Any],
        key_fields: dict[str, tuple[str, ...]],
        streams: list[str] | None = None,
    ):
        super().__init__(streams)
        try:
            from confluent_kafka import Producer
        except ImportError as exc:
            raise SinkError("The Kafka sink needs confluent-kafka: pip install confluent-kafka") from exc
        if not bootstrap:
            raise SinkError("Enter the bootstrap servers, e.g. localhost:19092")
        self.bootstrap = bootstrap
        self.topic_prefix = topic_prefix
        self.single_topic = single_topic or None
        self.key_fields = key_fields
        self.delivered = 0
        self.delivery_failures = 0
        conf: dict[str, Any] = {
            "bootstrap.servers": bootstrap,
            "client.id": f"livesim-{__version__}",
            "linger.ms": 50,
            "message.timeout.ms": 60_000,
            "error_cb": self._on_error,
        }
        conf.update(extra or {})
        try:
            self.producer = Producer(conf, logger=_kafka_log)
        except Exception as exc:
            raise SinkError(f"Invalid Kafka configuration: {exc}") from exc

    def check(self, timeout: float = 8.0) -> None:
        try:
            self.producer.list_topics(timeout=timeout)
        except Exception as exc:
            raise SinkError(f"Cannot reach Kafka at {self.bootstrap}: {exc}") from exc

    def _on_error(self, err) -> None:
        self.errors += 1
        self.last_error = f"KafkaError: {err}"
        self.last_error_at = iso(utcnow())
        self.last_error_mono = time.monotonic()

    def _on_delivery(self, err, _msg) -> None:
        if err is not None:
            self.delivery_failures += 1
            self._on_error(err)
        else:
            self.delivered += 1

    def topic_for(self, stream: str) -> str:
        return self.single_topic or f"{self.topic_prefix}{stream}"

    def write(self, batch):
        produced = 0
        for stream, records in batch.items():
            topic = self.topic_for(stream)
            keys = self.key_fields.get(stream, ())
            for record in records:
                key = "|".join("" if record.get(k) is None else str(record.get(k)) for k in keys)
                value = json.dumps({**record, "_stream": stream}, ensure_ascii=False, separators=(",", ":")).encode()
                while True:
                    try:
                        self.producer.produce(
                            topic,
                            value=value,
                            key=key.encode() if key else None,
                            headers={"stream": stream},
                            on_delivery=self._on_delivery,
                        )
                        break
                    except BufferError:  # local queue full: wait for deliveries to drain it
                        self.producer.poll(0.5)
                produced += 1
            self.producer.poll(0)
        self.producer.flush(5)
        return produced

    def close(self):
        self.producer.flush(5)

    def status(self):
        out = super().status()
        out.update(
            bootstrap_servers=self.bootstrap,
            topic_pattern=self.single_topic or f"{self.topic_prefix}<stream>",
            delivered=self.delivered,
            delivery_failures=self.delivery_failures,
            pending=len(self.producer),
        )
        return out


# -- HTTP webhooks (Power BI push datasets, Azure Functions, n8n, Zapier, ...) ----------------


class WebhookSink(BaseSink):
    kind = "webhook"
    max_buffer = 20_000

    def __init__(
        self,
        stream: str,
        url: str,
        fmt: str = "array",
        fields: list[str] | None = None,
        batch_size: int = 100,
        interval_sec: float = 2.0,
        headers: dict[str, str] | None = None,
    ):
        super().__init__([stream])
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise SinkError("The webhook URL must start with http:// or https://")
        if fmt not in ("array", "powerbi", "ndjson", "envelope"):
            raise SinkError("Webhook format must be array, powerbi, ndjson or envelope")
        self.stream, self.url, self.fmt = stream, url, fmt
        self.fields = fields or None
        self.batch_size = max(1, int(batch_size))
        self.interval = max(0.2, float(interval_sec))
        self.headers = dict(headers or {})
        self.last_status: int | None = None

    def _body(self, records: list[dict]) -> tuple[bytes, str]:
        rows = [{k: r.get(k) for k in self.fields} for r in records] if self.fields else records
        if self.fmt == "powerbi":  # Power BI streaming datasets have no boolean type
            rows = [{k: int(v) if isinstance(v, bool) else v for k, v in row.items()} for row in rows]
        if self.fmt == "ndjson":
            return ("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)).encode(), "application/x-ndjson"
        if self.fmt == "envelope":
            return json.dumps(
                {"stream": self.stream, "count": len(rows), "records": rows}, ensure_ascii=False
            ).encode(), "application/json"
        return json.dumps(rows, ensure_ascii=False).encode(), "application/json"

    def post(self, records: list[dict]) -> int:
        body, content_type = self._body(records)
        request = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={"Content-Type": content_type, "User-Agent": f"LiveSim/{__version__}", **self.headers},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                self.last_status = response.status
        except urllib.error.HTTPError as exc:
            self.last_status = exc.code
            detail = exc.read(300).decode("utf-8", errors="replace")
            raise SinkError(f"HTTP {exc.code} from webhook: {detail}") from exc
        return self.last_status

    def write(self, batch):
        records = batch.get(self.stream, [])
        sent = 0
        for start in range(0, len(records), self.batch_size):
            try:
                self.post(records[start : start + self.batch_size])
            except Exception as exc:
                raise PartialWrite(sent, {self.stream: records[start:]}, exc) from exc
            sent += len(records[start : start + self.batch_size])
        return sent

    def status(self):
        out = super().status()
        out.update(
            url=self.url,
            format=self.fmt,
            fields=self.fields,
            batch_size=self.batch_size,
            interval_sec=self.interval,
            last_status=self.last_status,
        )
        return out


# -- manager -----------------------------------------------------------------------------------


def _mask_secrets(config: dict[str, Any]) -> dict[str, Any]:
    return {
        k: (SECRET_MASK if any(s in k.lower() for s in ("password", "secret", "token", "key")) and v else v)
        for k, v in config.items()
    }


class SinkManager:
    """Owns every sink and lets the API reconfigure them while the simulation runs."""

    def __init__(
        self,
        settings,
        store: Store,
        fields: dict[str, list[Field]],
        reference: dict[str, TableDef],
        key_fields: dict[str, tuple[str, ...]],
        current_seqs: Callable[[], dict[str, int]],
    ):
        self.settings = settings
        self.store = store
        self.fields = fields
        self.reference = reference
        self.key_fields = key_fields
        self.current_seqs = current_seqs
        self.store_sink = StoreSink(store)
        self.lake: LakeSink | None = None
        self.sql: SqlSink | None = None
        self.kafka: KafkaSink | None = None
        self.webhooks: dict[str, WebhookSink] = {}
        s = settings
        self.config: dict[str, Any] = {
            "lake": {
                "enabled": s.lake_enabled,
                "format": s.lake_format,
                "roll_seconds": s.lake_roll_seconds,
                "retention_hours": s.lake_retention_hours,
                "streams": None,
            },
            "sql": {"enabled": bool(s.sql_url), "url": s.sql_url, "schema": s.sql_schema or None, "streams": None},
            "kafka": {
                "enabled": bool(s.kafka_bootstrap),
                "bootstrap_servers": s.kafka_bootstrap,
                "topic_prefix": s.kafka_topic_prefix,
                "single_topic": s.kafka_topic or None,
                "config": dict(s.kafka_config),
                "streams": None,
            },
            "webhooks": {},
        }
        self.startup_errors: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._copy_task: asyncio.Task | None = None

    # -- lifecycle -------------------------------------------------------------------------

    async def start(self, saved: dict[str, Any] | None) -> None:
        self.store_sink.start()
        saved = saved or {}
        for key in ("lake", "sql", "kafka"):
            config = {**self.config[key], **saved.get(key, {})}
            if not config.get("enabled"):
                self.config[key] = config
                continue
            try:
                await getattr(self, f"configure_{key}")(config, copy_existing=False)
            except Exception as exc:
                self.config[key] = {**config, "enabled": False}
                self.startup_errors[key] = str(exc)
                log.warning("Could not start %s sink: %s", key, exc)
        for stream, config in saved.get("webhooks", {}).items():
            try:
                await self.set_webhook(stream, config)
            except Exception as exc:
                self.startup_errors[f"webhook:{stream}"] = str(exc)

    async def stop(self) -> None:
        if self._copy_task:
            self._copy_task.cancel()
        for sink in [self.lake, self.sql, self.kafka, *self.webhooks.values(), self.store_sink]:
            if sink is not None:
                await sink.stop()

    def submit(self, stream: str, records: list[dict], live: bool = True) -> None:
        self.store_sink.submit(stream, records)
        for sink in (self.lake, self.sql, self.kafka):
            if sink is not None and sink.wants(stream):
                sink.submit(stream, records)
        if live and stream in self.webhooks:
            self.webhooks[stream].submit(stream, records)

    def _streams(self, value: Any) -> list[str] | None:
        if not value:
            return None
        unknown = [s for s in value if s not in self.fields]
        if unknown:
            raise SinkError(f"Unknown stream(s): {', '.join(unknown)}")
        return list(value)

    async def _swap(self, attr: str, new_sink: BaseSink | None) -> None:
        async with self._lock:
            old = getattr(self, attr)
            setattr(self, attr, new_sink)
            if new_sink is not None:
                new_sink.start()
        if old is not None:
            await old.stop()

    # -- configuration -----------------------------------------------------------------------

    async def configure_lake(self, config: dict[str, Any], copy_existing: bool = False) -> None:
        config = {**self.config["lake"], **config}
        sink = None
        if config["enabled"]:
            sink = LakeSink(
                self.settings.lake_dir,
                self.fields,
                config["format"],
                config["roll_seconds"],
                config["retention_hours"],
                self._streams(config.get("streams")),
            )
        await self._swap("lake", sink)
        self.config["lake"] = config
        self.startup_errors.pop("lake", None)

    async def configure_sql(self, config: dict[str, Any], copy_existing: bool = True) -> None:
        previous = self.config["sql"]
        config = {**previous, **config}
        if config.get("url") == mask_url(previous.get("url") or "") and previous.get("url"):
            config["url"] = previous["url"]  # the UI echoes the masked URL back unchanged
        sink = None
        if config["enabled"]:
            if not config.get("url"):
                raise SinkError("Enter a SQLAlchemy database URL")
            sink = SqlSink(config["url"], config.get("schema"), self.fields, self.reference, self._streams(config.get("streams")))
            try:
                await asyncio.to_thread(sink.initialize)
            except Exception as exc:
                await asyncio.to_thread(sink.close)
                raise SinkError(f"Could not connect or create tables: {exc}") from exc
        upto = self.current_seqs()
        await self._swap("sql", sink)
        self.config["sql"] = config
        self.startup_errors.pop("sql", None)
        if sink is not None and copy_existing:
            self._copy_task = asyncio.create_task(self._copy_history(sink, upto))

    async def _copy_history(self, sink: SqlSink, upto: dict[str, int]) -> None:
        await asyncio.sleep(1.5)  # let the store flush records that were still buffered
        try:
            await asyncio.to_thread(sink.copy_history, self.store, upto)
        except Exception as exc:
            sink._record_error(exc)

    async def configure_kafka(self, config: dict[str, Any], copy_existing: bool = False) -> None:
        previous = self.config["kafka"]
        config = {**previous, **config}
        extra = dict(config.get("config") or {})
        for key, value in extra.items():  # keep stored secrets the UI only saw masked
            if value == SECRET_MASK:
                extra[key] = (previous.get("config") or {}).get(key, "")
        config["config"] = extra
        sink = None
        if config["enabled"]:
            sink = KafkaSink(
                config.get("bootstrap_servers", ""),
                config.get("topic_prefix") or "livesim.",
                config.get("single_topic"),
                extra,
                self.key_fields,
                self._streams(config.get("streams")),
            )
            await asyncio.to_thread(sink.check)
        await self._swap("kafka", sink)
        self.config["kafka"] = config
        self.startup_errors.pop("kafka", None)

    async def set_webhook(self, stream: str, config: dict[str, Any]) -> None:
        if stream not in self.fields:
            raise SinkError(f"Unknown stream {stream!r}")
        if config.get("fields"):
            unknown = [f for f in config["fields"] if f not in {x.name for x in self.fields[stream]} | {"_seq", "_ingested_at"}]
            if unknown:
                raise SinkError(f"Unknown field(s) for {stream}: {', '.join(unknown)}")
        sink = WebhookSink(
            stream,
            config["url"],
            config.get("format", "array"),
            config.get("fields"),
            config.get("batch_size", 100),
            config.get("interval_sec", 2.0),
            config.get("headers"),
        )
        async with self._lock:
            old = self.webhooks.get(stream)
            self.webhooks[stream] = sink
            sink.start()
        if old is not None:
            await old.stop()
        self.config["webhooks"][stream] = {
            k: config.get(k) for k in ("url", "format", "fields", "batch_size", "interval_sec", "headers")
        }

    async def remove_webhook(self, stream: str) -> bool:
        async with self._lock:
            sink = self.webhooks.pop(stream, None)
        self.config["webhooks"].pop(stream, None)
        if sink is not None:
            await sink.stop()
        return sink is not None

    # -- reporting ---------------------------------------------------------------------------

    def saved_config(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.config))

    def status(self) -> dict[str, Any]:
        lake_cfg = dict(self.config["lake"])
        sql_cfg = {
            **self.config["sql"],
            "url": mask_url(self.config["sql"].get("url") or "") if self.config["sql"].get("url") else "",
        }
        kafka_cfg = {**self.config["kafka"], "config": _mask_secrets(self.config["kafka"].get("config") or {})}
        return {
            "sqlite": self.store_sink.status(),
            "lake": {
                "config": lake_cfg,
                "status": self.lake.status() if self.lake else None,
                "path": str(self.settings.lake_dir.resolve()),
                "error": self.startup_errors.get("lake"),
            },
            "sql": {
                "config": sql_cfg,
                "status": self.sql.status() if self.sql else None,
                "error": self.startup_errors.get("sql"),
            },
            "kafka": {
                "config": kafka_cfg,
                "status": self.kafka.status() if self.kafka else None,
                "error": self.startup_errors.get("kafka"),
            },
            "webhooks": {s: {"config": self.config["webhooks"].get(s), "status": w.status()} for s, w in self.webhooks.items()},
        }
