"""Opt-in data-quality problems for practising cleaning, validation and robust pipelines.

Every corruption keeps values type-valid (a float stays a float), so strict consumers such as
the SQL sinks and the OData feed keep working while the data itself becomes messy. Keys,
timestamps' format, system fields and ML labels are never touched.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Any

from .schema import Field
from .util import iso, parse_iso

ISSUES: dict[str, str] = {
    "missing": "Null values in non-key fields",
    "duplicate": "Exact duplicates re-delivered a few seconds later (same business key, new _seq)",
    "late": "Late arrivals: event_time 1-60 minutes in the past",
    "future": "Clock skew: event_time up to 2 hours in the future",
    "outlier": "Extreme numeric values (x10 to x100, or negated)",
    "sentinel": "Placeholder values such as -999, -1, 0 or 99999 in numeric fields",
    "inconsistent": "Inconsistent text: casing changes, stray whitespace, underscores",
}

_TEXT_VARIANTS = (
    str.upper,
    str.lower,
    str.title,
    lambda s: f"  {s}",
    lambda s: f"{s} ",
    lambda s: s.replace(" ", "_"),
)


@dataclass
class ChaosConfig:
    enabled: bool = False
    rate: float = 0.05  # share of records that get one issue
    issues: list[str] = field(default_factory=lambda: list(ISSUES))

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ChaosConfig":
        data = data or {}
        issues = [i for i in data.get("issues", list(ISSUES)) if i in ISSUES]
        rate = min(max(float(data.get("rate", 0.05)), 0.0), 1.0)
        return cls(enabled=bool(data.get("enabled", False)), rate=rate, issues=issues)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Chaos:
    def __init__(self, fields: list[Field], rng: random.Random):
        self.rng = rng
        self.nullable = [f.name for f in fields if f.role in ("fk", "dimension", "measure", "text", "attribute")]
        self.numeric = [f for f in fields if f.role == "measure" and f.type in ("int", "float")]
        self.strings = [f.name for f in fields if f.role in ("dimension", "fk") and f.type == "string"]
        self.has_time = any(f.name == "event_time" for f in fields)
        self.pending: list[tuple[int, dict[str, Any]]] = []  # duplicates waiting to be re-delivered
        self.tick = 0
        self.affected = 0

    def apply(self, records: list[dict[str, Any]], cfg: ChaosConfig) -> list[dict[str, Any]]:
        """Return the records to emit for this tick, possibly corrupted, plus released duplicates."""
        self.tick += 1
        out: list[dict[str, Any]] = []
        if self.pending:
            ready = [r for t, r in self.pending if t <= self.tick]
            if ready:
                self.pending = [(t, r) for t, r in self.pending if t > self.tick]
                out.extend(ready)
        if not cfg.enabled or not cfg.issues or cfg.rate <= 0:
            out.extend(records)
            return out
        rng = self.rng
        for record in records:
            out.append(record)
            if rng.random() >= cfg.rate:
                continue
            issue = rng.choice(cfg.issues)
            self.affected += 1
            if issue == "duplicate":
                self.pending.append((self.tick + rng.randint(1, 20), dict(record)))
            else:
                self._mutate(record, issue)
        return out

    def drain(self) -> list[dict[str, Any]]:
        ready = [r for _, r in self.pending]
        self.pending = []
        return ready

    def _mutate(self, record: dict[str, Any], issue: str) -> None:
        rng = self.rng
        if issue == "missing" and self.nullable:
            for name in rng.sample(self.nullable, min(len(self.nullable), rng.choice((1, 1, 2)))):
                record[name] = None
        elif issue in ("late", "future") and self.has_time and record.get("event_time"):
            shift = timedelta(seconds=rng.uniform(60, 3600)) if issue == "late" else -timedelta(seconds=rng.uniform(300, 7200))
            record["event_time"] = iso(parse_iso(record["event_time"]) - shift)
        elif issue in ("outlier", "sentinel") and self.numeric:
            f = rng.choice(self.numeric)
            value = record.get(f.name)
            if issue == "outlier":
                if value is None:
                    return
                new = -value if (value and rng.random() < 0.3) else (value or 1) * rng.choice((10, 25, 100))
            else:
                new = rng.choice((-999, -1, 0, 99999))
            record[f.name] = int(new) if f.type == "int" else float(round(new, 4))
        elif issue == "inconsistent" and self.strings:
            name = rng.choice(self.strings)
            value = record.get(name)
            if not value:
                return
            for variant in rng.sample(_TEXT_VARIANTS, len(_TEXT_VARIANTS)):
                changed = variant(value)
                if changed != value:
                    record[name] = changed
                    return
