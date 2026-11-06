"""Real-time push outputs: Server-Sent Events, WebSockets and a raw TCP JSON-lines firehose."""

from __future__ import annotations

import asyncio
import json
import logging

import anyio
import anyio.abc
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from .engine import Engine
from .hub import Subscription

log = logging.getLogger("livesim.streaming")
KEEPALIVE_SECONDS = 15
MAX_BATCH = 500


def _parse_streams(engine: Engine, value: str | None) -> list[str] | None:
    if not value:
        return None
    names = [s.strip() for s in value.split(",") if s.strip()]
    unknown = [n for n in names if n not in engine.streams]
    if unknown:
        raise HTTPException(404, f"Unknown stream(s): {', '.join(unknown)}. Available: {', '.join(engine.streams)}")
    return names or None


def _drain(sub: Subscription, first: tuple[str, int, str]) -> list[tuple[str, int, str]]:
    batch = [first]
    while len(batch) < MAX_BATCH:
        try:
            batch.append(sub.queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return batch


def build_router(engine: Engine) -> APIRouter:
    router = APIRouter()

    def sse_response(streams: list[str] | None) -> StreamingResponse:
        sub = engine.hub.subscribe(streams, kind="sse")

        async def events():
            try:
                yield "retry: 3000\n\n"
                while True:
                    try:
                        first = await asyncio.wait_for(sub.queue.get(), timeout=KEEPALIVE_SECONDS)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    yield "".join(f"id: {seq}\ndata: {payload}\n\n" for _stream, seq, payload in _drain(sub, first))
            finally:
                engine.hub.unsubscribe(sub)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    @router.get("/api/v1/sse", tags=["Live streams"], summary="Server-Sent Events for several streams")
    async def sse_multi(streams: str | None = Query(None, description="Comma-separated stream names; omit for every stream")):
        """Each event's `data` is one JSON record including a `_stream` field. Reconnecting clients
        should catch up gaps with `/records?after_seq=` (SSE delivers live data only)."""
        return sse_response(_parse_streams(engine, streams))

    @router.get("/api/v1/streams/{name}/sse", tags=["Live streams"], summary="Server-Sent Events for one stream")
    async def sse_one(name: str):
        return sse_response(_parse_streams(engine, name))

    async def websocket_session(websocket: WebSocket, streams: list[str] | None) -> None:
        await websocket.accept()
        sub = engine.hub.subscribe(streams, kind="websocket")

        async def sender(group: anyio.abc.TaskGroup) -> None:
            try:
                while True:
                    first = await sub.queue.get()
                    for _stream, _seq, payload in _drain(sub, first):
                        await websocket.send_text(payload)
            except (WebSocketDisconnect, RuntimeError, OSError):
                group.cancel_scope.cancel()

        async def receiver(group: anyio.abc.TaskGroup) -> None:
            # Clients may send {"subscribe": ["orders", "rides"]} (or [] for everything) at any time.
            try:
                while True:
                    message = await websocket.receive_text()
                    try:
                        wanted = json.loads(message).get("subscribe")
                    except (ValueError, AttributeError):
                        continue
                    if isinstance(wanted, list):
                        valid = {s for s in wanted if s in engine.streams}
                        sub.streams = valid or None
            except (WebSocketDisconnect, RuntimeError):
                group.cancel_scope.cancel()  # client went away: stop sending too

        try:
            # An anyio task group (rather than raw asyncio tasks) cooperates with the server's cancel scopes.
            async with anyio.create_task_group() as group:
                group.start_soon(sender, group)
                group.start_soon(receiver, group)
        finally:
            engine.hub.unsubscribe(sub)

    @router.websocket("/ws")
    async def ws_multi(websocket: WebSocket, streams: str | None = None):
        names = [s for s in (streams or "").split(",") if s in engine.streams] or None
        await websocket_session(websocket, names)

    @router.websocket("/ws/{name}")
    async def ws_one(websocket: WebSocket, name: str):
        if name not in engine.streams:
            await websocket.close(code=1008)
            return
        await websocket_session(websocket, [name])

    return router


async def start_tcp_server(engine: Engine, host: str, port: int) -> asyncio.base_events.Server:
    """Newline-delimited JSON firehose of every stream (Spark's socket source, netcat, Python).

    A client may optionally send a line such as ``orders,rides`` to receive only those streams.
    """

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        sub = engine.hub.subscribe(None, kind="tcp", maxsize=50_000)

        async def sender():
            while True:
                first = await sub.queue.get()
                writer.write(("\n".join(p for _s, _q, p in _drain(sub, first)) + "\n").encode("utf-8"))
                await writer.drain()

        async def receiver():
            while True:
                line = await reader.readline()
                if not line:
                    return  # client closed the connection
                names = {s.strip() for s in line.decode("utf-8", "ignore").split(",") if s.strip() in engine.streams}
                sub.streams = names or None

        tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            engine.hub.unsubscribe(sub)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    server = await asyncio.start_server(handle, host, port)
    log.info("TCP firehose listening on %s:%d", host, port)
    return server
