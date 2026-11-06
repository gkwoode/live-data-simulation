"""Small helpers shared by the generators, engine and API layers."""

from __future__ import annotations

import math
import random
import re
import unicodedata
from bisect import bisect_right
from datetime import datetime, timezone
from itertools import accumulate
from typing import Any, Sequence

UTC = timezone.utc
_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    """Canonical timestamp format used everywhere: UTC, millisecond precision, 'Z' suffix.

    A fixed-width format means timestamps also sort and compare correctly as plain strings,
    which is what the SQLite store and the OData filter translation rely on.
    """
    dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 date or datetime (with or without offset) into an aware UTC datetime."""
    text = value.strip().replace("Z", "+00:00").replace("z", "+00:00")
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def base36(n: int) -> str:
    if n == 0:
        return "0"
    out = []
    while n:
        n, r = divmod(n, 36)
        out.append(_B36[r])
    return "".join(reversed(out))


def make_id(prefix: str, ts: datetime, rng: random.Random) -> str:
    """Compact, time-sortable, collision-resistant business id such as ``ORD-MHK2Q3B1F00A7Z``."""
    ms = int(ts.timestamp() * 1000)
    return f"{prefix}-{base36(ms)}{base36(rng.getrandbits(24)).rjust(5, '0')}"


def hex_id(rng: random.Random, bits: int = 128) -> str:
    return f"{rng.getrandbits(bits):0{bits // 4}x}"


def poisson(rng: random.Random, lam: float) -> int:
    """Number of arrivals in an interval for a Poisson process with mean ``lam``."""
    if lam <= 0:
        return 0
    if lam > 60:  # normal approximation is accurate (and much faster) for large means
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    threshold, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= threshold:
            return k
        k += 1


class Weighted:
    """Fast repeated weighted sampling over a fixed population."""

    __slots__ = ("items", "cum", "total")

    def __init__(self, items: Sequence[Any], weights: Sequence[float]):
        if len(items) != len(weights) or not items:
            raise ValueError("items and weights must be non-empty and the same length")
        self.items = list(items)
        self.cum = list(accumulate(weights))
        self.total = self.cum[-1]

    def pick(self, rng: random.Random) -> Any:
        return self.items[bisect_right(self.cum, rng.random() * self.total)]


def zipf_weights(n: int, s: float = 1.07) -> list[float]:
    """Long-tail popularity weights: a few items are very popular, most are rare."""
    return [1.0 / (i + 1) ** s for i in range(n)]


def clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", ascii_text.lower())


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


class DailyPattern:
    """Smooth time-of-day x day-of-week activity profile, normalised to a weekly mean of 1.

    ``peaks`` is a list of ``(hour, width_hours, height)`` gaussian bumps on a 24h circle on top
    of a constant ``base`` level. Patterns use the machine's local time so that traffic follows
    the day of whoever is running the simulator.
    """

    def __init__(
        self,
        peaks: Sequence[tuple[float, float, float]],
        base: float = 0.25,
        weekdays: Sequence[float] = (1, 1, 1, 1, 1, 1, 1),
    ):
        self.peaks = tuple(peaks)
        self.base = base
        self.weekdays = tuple(weekdays)
        grid = [self.shape(q / 4) for q in range(96)]
        self._norm = (sum(grid) / len(grid)) * (sum(self.weekdays) / 7)

    def shape(self, hour: float) -> float:
        value = self.base
        for center, width, height in self.peaks:
            d = abs(hour - center) % 24
            d = min(d, 24 - d)
            value += height * math.exp(-0.5 * (d / width) ** 2)
        return value

    def factor(self, ts: datetime) -> float:
        local = ts.astimezone()
        hour = local.hour + local.minute / 60
        return self.shape(hour) * self.weekdays[local.weekday()] / self._norm


def local_hour(ts: datetime) -> float:
    local = ts.astimezone()
    return local.hour + local.minute / 60 + local.second / 3600
