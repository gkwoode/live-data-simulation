"""FastAPI application factory: wires the engine, APIs, static dashboard and security guard."""

from __future__ import annotations

import base64
import binascii
import hmac
import ipaddress
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from . import api, odata, streaming
from .config import Settings
from .engine import Engine

log = logging.getLogger("livesim")
STATIC_DIR = Path(__file__).parent / "static"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PROTECTED_PREFIXES = ("/api/", "/odata", "/ws")


class RevalidatingStaticFiles(StaticFiles):
    """Static files that browsers must revalidate (cheap 304s), so upgrades never serve a stale UI."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


class GuardMiddleware:
    """Protects a server that usually runs on a developer's machine.

    * Host allowlist - blocks DNS-rebinding attacks from malicious web pages.
    * Same-origin check for state-changing requests - a website cannot start backfills or point
      sinks at other hosts through your browser (CSRF), while cross-origin reads stay allowed.
    * Optional API key (X-API-Key header, ?api_key=, Bearer token or HTTP Basic password).
    """

    def __init__(self, app, settings: Settings):
        self.app = app
        self.api_key = settings.api_key
        self.bind_all = settings.host in ("0.0.0.0", "::", "")
        hosts = {"localhost", "127.0.0.1", "::1", "testserver", "host.docker.internal"}
        hosts.update(h.strip().lower() for h in settings.allowed_hosts.split(",") if h.strip())
        if settings.public_url:
            hosts.add((urlsplit(settings.public_url).hostname or "").lower())
        if settings.host not in ("0.0.0.0", "::", ""):
            hosts.add(settings.host.lower())
        self.hosts = hosts
        self.any_host = "*" in hosts

    def _host_ok(self, host_header: str) -> bool:
        if self.any_host:
            return True
        host = urlsplit(f"//{host_header}").hostname or ""
        host = host.lower()
        if host in self.hosts or host.endswith(".localhost"):
            return True
        if self.bind_all:  # LAN or Docker access by IP address (DNS rebinding needs a domain name)
            try:
                ipaddress.ip_address(host)
                return True
            except ValueError:
                return False
        return False

    def _key_ok(self, headers: dict[str, str], query: str) -> bool:
        candidates = [headers.get("x-api-key", "")]
        candidates += parse_qs(query).get("api_key", [])
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            candidates.append(auth[7:].strip())
        elif auth.lower().startswith("basic "):
            try:
                candidates.append(base64.b64decode(auth[6:]).decode("utf-8").split(":", 1)[-1])
            except (binascii.Error, UnicodeDecodeError):
                pass
        return any(c and hmac.compare_digest(c.encode(), self.api_key.encode()) for c in candidates)

    async def _reject(self, scope, send, status: int, message: str, basic: bool = False) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        headers = [(b"content-type", b"application/json")]
        if basic:
            headers.append((b"www-authenticate", b'Basic realm="LiveSim"'))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": json.dumps({"detail": message}).encode()})

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        host = headers.get("host", "")
        if not self._host_ok(host):
            return await self._reject(scope, send, 403, f"Host {host!r} is not allowed. Add it to LIVESIM_ALLOWED_HOSTS.")
        method = scope.get("method", "GET")
        if scope["type"] == "http" and method not in SAFE_METHODS:
            origin = headers.get("origin")
            if origin is not None and (origin == "null" or (urlsplit(origin).netloc or "").lower() != host.lower()):
                return await self._reject(scope, send, 403, "Cross-origin requests cannot change the simulator.")
        path = scope.get("path", "")
        if (
            self.api_key
            and method != "OPTIONS"
            and path.startswith(PROTECTED_PREFIXES)
            and path != "/api/v1/health"
            and not self._key_ok(headers, scope.get("query_string", b"").decode("latin-1"))
        ):
            return await self._reject(
                scope, send, 401, "API key required (X-API-Key header, api_key parameter or Basic auth).", basic=True
            )
        return await self.app(scope, receive, send)


DESCRIPTION = """
LiveSim generates realistic live data for ten domains and serves it through every interface a data
professional needs: REST (JSON, CSV, NDJSON with cursor pagination), OData v4 for BI tools,
Server-Sent Events, WebSockets, a TCP firehose, a SQLite store, and sinks for any SQL database,
Kafka, files and webhooks.

* Dashboard: [/](/) - OData service: [/odata](/odata) - Connection snippets: `GET /api/v1/connections?stream=orders`
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    engine = Engine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await engine.start()
        tcp_server = None
        if settings.tcp_port:
            try:
                tcp_server = await streaming.start_tcp_server(engine, settings.host, settings.tcp_port)
            except OSError as exc:
                log.warning("TCP firehose disabled (port %s unavailable): %s", settings.tcp_port, exc)
        app.state.tcp_server = tcp_server
        try:
            yield
        finally:
            if tcp_server is not None:
                tcp_server.close()
            await engine.stop()

    app = FastAPI(
        title="LiveSim API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "Streams", "description": "Inspect and control the simulated streams"},
            {"name": "Data", "description": "Read stored records in JSON, CSV or NDJSON"},
            {"name": "Live streams", "description": "Push delivery (also: WebSocket /ws and the TCP firehose)"},
            {"name": "Reference data", "description": "Dimension tables"},
            {"name": "SQL Lab", "description": "Read-only SQL over the live store"},
            {"name": "Sinks", "description": "Destinations: SQLite, files, SQL databases, Kafka, webhooks"},
            {"name": "Connections", "description": "Ready-to-paste integration snippets"},
            {"name": "System", "description": "Health and totals"},
        ],
    )
    app.state.engine = engine
    app.state.settings = settings
    app.include_router(api.build_router(engine))
    app.include_router(streaming.build_router(engine))
    app.include_router(odata.build_router(engine))
    app.mount("/static", RevalidatingStaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

    app.add_middleware(GuardMiddleware, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "HEAD", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["OData-Version"],
    )
    return app
