"""In-process publish/subscribe hub feeding the real-time outputs (SSE, WebSocket, TCP)."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Iterable


class Subscription:
    __slots__ = ("streams", "queue", "dropped", "kind")

    def __init__(self, streams: set[str] | None, maxsize: int, kind: str):
        self.streams = streams  # None means every stream
        self.queue: asyncio.Queue[tuple[str, int, str]] = asyncio.Queue(maxsize)
        self.dropped = 0
        self.kind = kind

    def wants(self, stream: str) -> bool:
        return self.streams is None or stream in self.streams


class Hub:
    """Fan-out of freshly generated records to live subscribers.

    Each record is serialised to JSON once (with a ``_stream`` field) and shared by every
    subscriber. Slow consumers never block the simulation: when a subscriber's queue is full
    its newest messages are dropped and counted.
    """

    def __init__(self) -> None:
        self.subscribers: set[Subscription] = set()

    def subscribe(self, streams: Iterable[str] | None = None, kind: str = "sse", maxsize: int = 10_000) -> Subscription:
        sub = Subscription(set(streams) if streams else None, maxsize, kind)
        self.subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        self.subscribers.discard(sub)

    def publish(self, stream: str, records: list[dict[str, Any]]) -> None:
        targets = [s for s in self.subscribers if s.wants(stream)]
        if not targets:
            return
        messages = [(stream, r["_seq"], json.dumps({**r, "_stream": stream}, separators=(",", ":"))) for r in records]
        for sub in targets:
            for message in messages:
                try:
                    sub.queue.put_nowait(message)
                except asyncio.QueueFull:
                    sub.dropped += 1

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for sub in self.subscribers:
            out[sub.kind] = out.get(sub.kind, 0) + 1
        return out
