"""The simulation engine: runs every stream, stamps records and fans them out to hub and sinks."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import time
import zlib
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from . import __version__
from .chaos import Chaos, ChaosConfig
from .config import Settings
from .generators import GENERATORS, Generator
from .hub import Hub
from .reference import ReferenceData
from .sinks import SinkManager
from .store import Store
from .util import Weighted, iso, poisson, utcnow

log = logging.getLogger("livesim.engine")

TICK_SECONDS = 0.25
MAX_RATE = 2000.0  # events per second per stream
STATS_WINDOW = 120  # seconds of per-second history kept for charts


@dataclass
class StreamConfig:
    running: bool = True
    rate: float = 1.0
    seasonality: bool = True
    chaos: ChaosConfig = field(default_factory=ChaosConfig)

    def to_dict(self) -> dict[str, Any]:
        return {"running": self.running, "rate": self.rate, "seasonality": self.seasonality, "chaos": self.chaos.to_dict()}

    def apply(self, patch: dict[str, Any]) -> None:
        if patch.get("running") is not None:
            self.running = bool(patch["running"])
        if patch.get("rate") is not None:
            rate = float(patch["rate"])
            if not 0 < rate <= MAX_RATE:
                raise ValueError(f"rate must be between 0 and {MAX_RATE:g} events per second")
            self.rate = rate
        if patch.get("seasonality") is not None:
            self.seasonality = bool(patch["seasonality"])
        if patch.get("chaos") is not None:
            merged = {**self.chaos.to_dict(), **{k: v for k, v in patch["chaos"].items() if v is not None}}
            self.chaos = ChaosConfig.from_dict(merged)


def _seed(*parts: Any) -> int:
    return zlib.crc32(":".join(map(str, parts)).encode())


class StreamRuntime:
    def __init__(self, engine: "Engine", cls: type[Generator]):
        self.engine = engine
        self.cls = cls
        self.name = cls.name
        self.fields = list(cls.fields)
        boot = time.time_ns()
        self.generator = cls(engine.ref, random.Random(_seed(engine.settings.seed, cls.name, boot)))
        self.rng = random.Random(_seed("arrivals", cls.name, boot))
        self.chaos = Chaos(self.fields, random.Random(_seed("chaos", cls.name, boot)))
        self.config = StreamConfig(running=engine.settings.autostart, rate=cls.default_rate)
        self.seq = 0
        self.emitted = 0  # records emitted since start
        self.last_emit_at: str | None = None
        self.recent: deque[dict] = deque(maxlen=200)
        self.seconds: deque[list] = deque(maxlen=STATS_WINDOW + 15)  # [epoch_second, records, metric_sum, metric_n]
        self.backfill: dict[str, Any] | None = None
        self.task: asyncio.Task | None = None
        _label, self.metric_field, self.metric_agg = cls.metric

    # -- record plumbing -----------------------------------------------------------------

    def stamp(self, records: list[dict], ingested_at: str) -> None:
        for record in records:
            self.seq += 1
            record["_seq"] = self.seq
            record["_ingested_at"] = ingested_at

    def _account(self, records: list[dict], now: datetime) -> None:
        second = int(now.timestamp())
        if not self.seconds or self.seconds[-1][0] != second:
            self.seconds.append([second, 0, 0.0, 0])
        bucket = self.seconds[-1]
        bucket[1] += len(records)
        if self.metric_field:
            for record in records:
                value = record.get(self.metric_field)
                if value is not None:
                    bucket[2] += float(value)
                    bucket[3] += 1
        else:
            bucket[2] += len(records)
            bucket[3] += len(records)

    def emit(self, records: list[dict], now: datetime) -> None:
        if not records:
            return
        self.stamp(records, iso(now))
        self.emitted += len(records)
        self.last_emit_at = records[-1]["_ingested_at"]
        self.recent.extend(records)
        self._account(records, now)
        self.engine.hub.publish(self.name, records)
        self.engine.sinks.submit(self.name, records, live=True)

    async def run(self) -> None:
        last = utcnow()
        loop = asyncio.get_running_loop()
        next_tick = loop.time()
        while True:
            next_tick += TICK_SECONDS
            await asyncio.sleep(max(0.0, next_tick - loop.time()))
            now = utcnow()
            window = min((now - last).total_seconds(), 5.0)
            last = now
            try:
                if not self.config.running:
                    # still release pending duplicates so chaos never holds records forever
                    if self.chaos.pending:
                        self.emit(self.chaos.drain(), now)
                    continue
                activity = self.generator.activity(now) if self.config.seasonality else 1.0
                count = min(poisson(self.rng, self.config.rate * activity * window), int(MAX_RATE * 2 * window) + 1)
                records: list[dict] = []
                if count:
                    offsets = sorted(self.rng.random() * window for _ in range(count))
                    for offset in offsets:
                        records.extend(self.generator.generate(now - timedelta(seconds=window - offset)))
                self.emit(self.chaos.apply(records, self.config.chaos), now)
            except Exception:  # a generator bug must never kill the stream loop
                log.exception("Stream %s failed during a tick", self.name)
                await asyncio.sleep(1)

    # -- reporting -----------------------------------------------------------------------

    def series(self) -> dict[str, Any]:
        """Per-second history for the last STATS_WINDOW complete seconds.

        ``records`` holds raw counts. Low-rate Poisson counts are spiky, so charts use
        ``records_avg`` (5-second trailing mean) and ``metric``: a 5-second trailing mean of
        per-second sums, or a 10-second rolling window for averaged metrics (so sparse streams
        don't leave gaps). Seconds with no data in the window are null for averages.
        """
        current = int(time.time())
        lead = 10
        buckets = {b[0]: b for b in self.seconds}
        counts, sums, ns = [], [], []
        for second in range(current - STATS_WINDOW - lead, current):
            bucket = buckets.get(second)
            counts.append(bucket[1] if bucket else 0)
            sums.append(bucket[2] if bucket else 0.0)
            ns.append(bucket[3] if bucket else 0)
        span = range(lead, len(counts))
        records_avg = [round(sum(counts[i - 4 : i + 1]) / 5, 3) for i in span]
        if self.metric_agg == "avg":
            metric = []
            for i in span:
                n = sum(ns[i - 9 : i + 1])
                metric.append(round(sum(sums[i - 9 : i + 1]) / n, 4) if n else None)
            window = "10-second rolling average"
        else:
            metric = [round(sum(sums[i - 4 : i + 1]) / 5, 4) for i in span]
            window = "5-second moving average"
        return {
            "start": current - STATS_WINDOW,
            "records": counts[lead:],
            "records_avg": records_avg,
            "metric": metric,
            "metric_window": window,
        }

    def stats(self) -> dict[str, Any]:
        series = self.series()
        recent = series["records"][-10:]
        activity = self.generator.activity(utcnow()) if self.config.seasonality else 1.0
        return {
            "records_per_sec": round(sum(recent) / len(recent), 2),
            "effective_rate": round(self.config.rate * activity, 3) if self.config.running else 0.0,
            "emitted": self.emitted,
            "stored": self.engine.store.row_counts.get(self.name, 0),
            "last_seq": self.seq,
            "last_emit_at": self.last_emit_at,
            "chaos_affected": self.chaos.affected,
            "backfill": self.backfill,
        }

    def summary(self, with_series: bool = True) -> dict[str, Any]:
        out = {**self.cls.describe(), "config": self.config.to_dict(), "stats": self.stats()}
        if with_series:
            out["series"] = self.series()
        return out


class Engine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.ref = ReferenceData(settings.seed)
        self.store = Store(settings.db_path)
        self.hub = Hub()
        self.streams: dict[str, StreamRuntime] = {cls.name: StreamRuntime(self, cls) for cls in GENERATORS}
        self.sinks = SinkManager(
            settings,
            self.store,
            {name: rt.fields for name, rt in self.streams.items()},
            self.ref.tables,
            {name: rt.cls.key_fields for name, rt in self.streams.items()},
            lambda: {name: rt.seq for name, rt in self.streams.items()},
        )
        self.state_path = settings.data_dir / "state.json"
        self.started_at = utcnow()
        self._tasks: list[asyncio.Task] = []
        self._backfill_tasks: dict[str, asyncio.Task] = {}

    # -- lifecycle -------------------------------------------------------------------------

    async def start(self) -> None:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        fingerprint = f"{__version__}:{self.settings.seed}"
        await asyncio.to_thread(self.store.open, {n: rt.fields for n, rt in self.streams.items()}, self.ref.tables, fingerprint)
        state = self._load_state()
        saved_streams = state.get("streams", {})
        for name, rt in self.streams.items():
            saved = saved_streams.get(name, {})
            rt.seq = max(self.store.max_seq(name), int(saved.get("last_seq", 0)))
            if saved:
                try:
                    rt.config.apply(saved)
                except ValueError as exc:
                    log.warning("Ignoring saved config for %s: %s", name, exc)
        await self.sinks.start(state.get("sinks"))
        # Decide which tables need history before live records start arriving.
        empty = [name for name in self.streams if self.store.row_counts.get(name, 0) == 0]
        for rt in self.streams.values():
            rt.task = asyncio.create_task(rt.run(), name=f"stream:{rt.name}")
        self._tasks.append(asyncio.create_task(self._maintenance(), name="maintenance"))
        if self.settings.history_days > 0 and empty:
            self._tasks.append(asyncio.create_task(self._initial_history(empty), name="initial-history"))
        log.info("LiveSim engine started with %d streams; store at %s", len(self.streams), self.settings.db_path.resolve())

    async def stop(self) -> None:
        for task in [*self._backfill_tasks.values(), *self._tasks, *(rt.task for rt in self.streams.values() if rt.task)]:
            task.cancel()
        await asyncio.gather(*self._backfill_tasks.values(), *self._tasks, return_exceptions=True)
        await asyncio.gather(*(rt.task for rt in self.streams.values() if rt.task), return_exceptions=True)
        await self.sinks.stop()
        self._save_state()
        await asyncio.to_thread(self.store.close)

    async def _initial_history(self, names: list[str]) -> None:
        await asyncio.sleep(0.5)
        for name in names:
            rt = self.streams[name]
            events = int(rt.cls.history_per_day * self.settings.history_days)
            try:
                await self.backfill(name, self.settings.history_days, events, wait=True)
            except Exception:
                log.exception("Initial history for %s failed", name)
        log.info("Initial history ready for %d streams", len(names))

    async def _maintenance(self) -> None:
        ticks = 0
        while True:
            await asyncio.sleep(5)
            ticks += 1
            try:
                self._save_state()
                if ticks % 6 == 0:  # every 30 seconds
                    for name in self.streams:
                        deleted = await asyncio.to_thread(self.store.enforce_retention, name, self.settings.max_rows)
                        if deleted:
                            log.debug("Retention removed %d rows from %s", deleted, name)
            except Exception:
                log.exception("Maintenance failed")

    # -- state -------------------------------------------------------------------------------

    def _load_state(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            log.warning("Ignoring unreadable %s: %s", self.state_path, exc)
            return {}

    def _save_state(self) -> None:
        state = {
            "version": 1,
            "streams": {name: {**rt.config.to_dict(), "last_seq": rt.seq} for name, rt in self.streams.items()},
            "sinks": self.sinks.saved_config(),
        }
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    # -- control -----------------------------------------------------------------------------

    def runtime(self, name: str) -> StreamRuntime:
        try:
            return self.streams[name]
        except KeyError:
            raise KeyError(f"Unknown stream {name!r}. Available: {', '.join(self.streams)}") from None

    def update_stream(self, name: str, patch: dict[str, Any]) -> StreamRuntime:
        rt = self.runtime(name)
        rt.config.apply(patch)
        self._save_state()
        return rt

    def set_all_running(self, running: bool) -> None:
        for rt in self.streams.values():
            rt.config.running = running
        self._save_state()

    async def reset_stream(self, name: str) -> int:
        self.runtime(name)
        await self.sinks.store_sink.flush()
        return await asyncio.to_thread(self.store.truncate, name)

    async def backfill(self, name: str, days: float, events: int | None = None, wait: bool = False) -> dict[str, Any]:
        rt = self.runtime(name)
        if rt.backfill and rt.backfill.get("running"):
            raise RuntimeError(f"A backfill for {name} is already running")
        events = int(events or rt.cls.history_per_day * days)
        rt.backfill = {
            "running": True,
            "days": days,
            "events": events,
            "done": 0,
            "records": 0,
            "started_at": iso(utcnow()),
            "finished_at": None,
        }
        task = asyncio.create_task(self._run_backfill(rt, days, events), name=f"backfill:{name}")
        self._backfill_tasks[name] = task
        if wait:
            await task
        return rt.backfill

    async def _run_backfill(self, rt: StreamRuntime, days: float, events: int) -> None:
        end = utcnow()
        start = end - timedelta(days=days)
        salt = time.time_ns()
        generator = rt.cls(self.ref, random.Random(_seed("backfill", rt.name, salt)))
        chaos = Chaos(rt.fields, random.Random(_seed("backfill-chaos", rt.name, salt)))
        chaos_config = rt.config.chaos
        stamps = await asyncio.to_thread(_sample_times, generator, start, end, events, rt.config.seasonality, salt)

        def produce(chunk: list[datetime]) -> list[dict]:
            records: list[dict] = []
            for ts in chunk:
                records.extend(generator.generate(ts))
            return chaos.apply(records, chaos_config)

        try:
            for i in range(0, len(stamps), 2000):
                chunk = stamps[i : i + 2000]
                records = await asyncio.to_thread(produce, chunk)
                rt.stamp(records, iso(utcnow()))
                self.sinks.submit(rt.name, records, live=False)
                rt.backfill["done"] += len(chunk)
                rt.backfill["records"] += len(records)
                while self.sinks.store_sink.buffered > 100_000:  # backpressure
                    await asyncio.sleep(0.2)
            leftovers = chaos.drain()
            if leftovers:
                rt.stamp(leftovers, iso(utcnow()))
                self.sinks.submit(rt.name, leftovers, live=False)
        finally:
            rt.backfill["running"] = False
            rt.backfill["finished_at"] = iso(utcnow())
            self._backfill_tasks.pop(rt.name, None)

    # -- reporting ---------------------------------------------------------------------------

    def overview(self) -> dict[str, Any]:
        stats = [rt.stats() for rt in self.streams.values()]
        return {
            "version": __version__,
            "started_at": iso(self.started_at),
            "uptime_seconds": int((utcnow() - self.started_at).total_seconds()),
            "streams_total": len(self.streams),
            "streams_running": sum(1 for rt in self.streams.values() if rt.config.running),
            "records_per_sec": round(sum(s["records_per_sec"] for s in stats), 2),
            "emitted": sum(s["emitted"] for s in stats),
            "stored": sum(s["stored"] for s in stats),
            "db_size_bytes": self.store.size_bytes(),
            "subscribers": self.hub.counts(),
        }


def _sample_times(generator: Generator, start: datetime, end: datetime, n: int, seasonal: bool, salt: int) -> list[datetime]:
    """Spread n timestamps over [start, end), following the stream's daily/weekly pattern."""
    rng = random.Random(_seed("times", generator.name, salt))
    total = (end - start).total_seconds()
    hours = max(1, math.ceil(total / 3600))
    weights = []
    for h in range(hours):
        span = min(3600.0, total - h * 3600)
        level = generator.activity(start + timedelta(seconds=h * 3600 + span / 2)) if seasonal else 1.0
        weights.append(max(level, 1e-6) * span / 3600)
    picker = Weighted(range(hours), weights)
    stamps = []
    for _ in range(n):
        h = picker.pick(rng)
        offset = min(h * 3600 + rng.random() * 3600, total - 1e-3)
        stamps.append(start + timedelta(seconds=offset))
    stamps.sort()
    return stamps
