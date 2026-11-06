"""Base class for stream generators."""

from __future__ import annotations

import random
from datetime import datetime
from typing import Any, ClassVar

from ..reference import ReferenceData
from ..schema import Field
from ..util import DailyPattern


class Generator:
    """One simulated event stream.

    Subclasses declare schema and metadata as class attributes and implement :meth:`generate`.
    ``generate`` receives the event timestamp (live "now", or a historical instant during a
    backfill) and returns one or more records. Generators keep state between calls (sessions,
    prices, machine health, table contents), so calls must arrive in timestamp order.
    """

    name: ClassVar[str]
    title: ClassVar[str]
    category: ClassVar[str]
    description: ClassVar[str]
    fields: ClassVar[list[Field]]
    # Business key of the record. Used as the Kafka message key, so events for the same entity
    # stay ordered within a partition.
    key_fields: ClassVar[tuple[str, ...]]
    default_rate: ClassVar[float] = 1.0  # generate() calls (events) per second
    history_per_day: ClassVar[int] = 5000  # events per simulated day in a default backfill
    pattern: ClassVar[DailyPattern | None] = None  # time-of-day profile that modulates the rate
    # Headline metric for the dashboard: (label, field or None for a record count, "sum" | "avg").
    metric: ClassVar[tuple[str, str | None, str]] = ("Records per second", None, "sum")
    related: ClassVar[tuple[str, ...]] = ()  # reference tables this stream joins to
    use_cases: ClassVar[dict[str, list[str]]] = {}  # practice ideas keyed by persona

    def __init__(self, ref: ReferenceData, rng: random.Random):
        self.ref = ref
        self.rng = rng

    def activity(self, ts: datetime) -> float:
        """Relative traffic level at ``ts`` (weekly mean 1.0)."""
        return self.pattern.factor(ts) if self.pattern else 1.0

    def generate(self, ts: datetime) -> list[dict[str, Any]]:
        raise NotImplementedError

    @classmethod
    def describe(cls) -> dict[str, Any]:
        label, field, agg = cls.metric
        return {
            "name": cls.name,
            "title": cls.title,
            "category": cls.category,
            "description": cls.description,
            "key_fields": list(cls.key_fields),
            "default_rate": cls.default_rate,
            "history_per_day": cls.history_per_day,
            "has_pattern": cls.pattern is not None,
            "metric": {"label": label, "field": field, "agg": agg},
            "related": list(cls.related),
            "use_cases": cls.use_cases,
        }
