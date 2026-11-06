"""Structured access and application logs from a microservice platform, with incidents."""

from __future__ import annotations

import math

from ..reference import CATALOG
from ..schema import F
from ..util import DailyPattern, Weighted, hex_id, iso, make_id
from .base import Generator

# service: traffic weight, endpoints as (method, route, median latency ms, error rate, share)
SERVICES = {
    "api-gateway": (
        30,
        [
            ("GET", "/api/v1/products", 38, 0.004, 30),
            ("GET", "/api/v1/products/{id}", 26, 0.006, 30),
            ("POST", "/api/v1/cart", 64, 0.008, 15),
            ("GET", "/api/v1/recommendations", 120, 0.01, 10),
        ],
    ),
    "auth-service": (
        12,
        [
            ("POST", "/auth/login", 140, 0.07, 45),
            ("POST", "/auth/token/refresh", 22, 0.01, 45),
            ("POST", "/auth/logout", 15, 0.002, 10),
        ],
    ),
    "orders-service": (
        14,
        [("POST", "/orders", 185, 0.012, 30), ("GET", "/orders/{id}", 41, 0.005, 45), ("GET", "/orders", 76, 0.004, 25)],
    ),
    "payments-service": (
        9,
        [
            ("POST", "/payments/authorize", 340, 0.03, 50),
            ("POST", "/payments/capture", 210, 0.012, 40),
            ("POST", "/payments/refund", 260, 0.015, 10),
        ],
    ),
    "inventory-service": (15, [("GET", "/inventory/{sku}", 14, 0.003, 70), ("PUT", "/inventory/{sku}", 47, 0.006, 30)]),
    "search-service": (12, [("GET", "/search", 92, 0.005, 100)]),
    "notification-service": (8, [("POST", "/notify/email", 135, 0.02, 70), ("POST", "/notify/sms", 205, 0.03, 30)]),
}
DEPENDENCIES = {
    "api-gateway": "orders-service",
    "auth-service": "user-db",
    "orders-service": "inventory-service",
    "payments-service": "card-processor",
    "inventory-service": "inventory-db",
    "search-service": "search-index",
    "notification-service": "smtp-relay",
}
REGIONS = ("us-east", "eu-west", "ap-south")
CLIENT_ERRORS = Weighted((400, 404, 409, 422, 429), (35, 30, 10, 15, 10))
SERVER_ERRORS = Weighted((500, 502, 503, 504), (45, 15, 25, 15))
SEARCH_TERMS = sorted({noun.lower() for *_, nouns in CATALOG for noun in nouns})


class AppLogsGenerator(Generator):
    name = "app_logs"
    title = "Application logs"
    category = "Observability & DevOps"
    description = (
        "Structured request logs from seven microservices: latency, status codes, levels, trace ids and free-text messages. "
        "Bad deploys periodically cause latency spikes, error bursts and dependency timeouts that later get fixed or rolled back."
    )
    default_rate = 6.0
    history_per_day = 25000
    key_fields = ("trace_id",)
    pattern = DailyPattern([(11.0, 3.0, 0.8), (20.0, 2.5, 0.9)], base=0.3, weekdays=(1.0, 1.0, 1.0, 1.0, 1.0, 0.9, 0.85))
    metric = ("Average latency (ms)", "latency_ms", "avg")
    related = ("dim_customer", "dim_date")
    fields = [
        F("log_id", "string", "id"),
        F("event_time", "timestamp", "time"),
        F("service", "string", "dimension"),
        F("version", "string", "dimension", "Deployed version; changes when a release, fix or rollback ships"),
        F("host", "string", "dimension", "Pod name"),
        F("region", "string", "dimension"),
        F("level", "string", "dimension", "DEBUG | INFO | WARN | ERROR"),
        F("http_method", "string", "dimension"),
        F("endpoint", "string", "dimension", "Route template, e.g. /orders/{id}"),
        F("status_code", "int", "dimension"),
        F("latency_ms", "int", "measure"),
        F("bytes_sent", "int", "measure"),
        F("trace_id", "string", "attribute", "W3C-style 32 hex character trace id"),
        F("user_id", "string", "fk", "-> dim_customer (null for anonymous traffic)"),
        F("client_ip", "string", "attribute", "Documentation-range IP address"),
        F("message", "string", "text", "Human-readable log line to parse"),
    ]
    use_cases = {
        "analyst": [
            "Build an SRE dashboard: request volume, error rate and p95 latency per service and version.",
            "Correlate incidents with version changes to find the bad deploy.",
            "Report SLO attainment (for example 99.5% of requests under 500 ms).",
        ],
        "engineer": [
            "Parse the free-text message field with regular expressions into structured columns.",
            "Stream logs to Kafka or files and build alerting on 1-minute error-rate windows.",
            "Model a medallion pipeline: raw logs -> parsed -> service health aggregates.",
        ],
        "scientist": [
            "Detect latency anomalies with seasonal baselines or isolation forests.",
            "Cluster log messages (TF-IDF + k-means) to discover error families.",
            "Forecast traffic per service for capacity planning.",
        ],
    }

    def __init__(self, ref, rng):
        super().__init__(ref, rng)
        names = list(SERVICES)
        self.pick_service = Weighted(names, [SERVICES[s][0] for s in names])
        self.pick_endpoint = {s: Weighted(SERVICES[s][1], [e[4] for e in SERVICES[s][1]]) for s in names}
        self.pods = {
            s: [(f"{s}-{hex_id(rng, 36)}-{hex_id(rng, 20)}", REGIONS[k % len(REGIONS)]) for k in range(rng.randint(3, 4))]
            for s in names
        }
        self.version = {s: [rng.randint(1, 3), rng.randint(0, 9), rng.randint(0, 9)] for s in names}
        self.incident = None
        self.customers = ref.customers

    def _version(self, service):
        return ".".join(map(str, self.version[service]))

    def _maybe_incident(self):
        rng = self.rng
        if self.incident is None:
            if rng.random() < 1 / 4000:
                service = rng.choice(list(SERVICES))
                self.incident = {
                    "service": service,
                    "kind": rng.choice(("latency", "errors", "dependency_timeout")),
                    "remaining": rng.randint(300, 1200),
                    "previous": list(self.version[service]),
                }
                self.version[service][2] += 1  # the bad deploy
            return
        self.incident["remaining"] -= 1
        if self.incident["remaining"] <= 0:
            service = self.incident["service"]
            if rng.random() < 0.5:
                self.version[service] = self.incident["previous"]  # rollback
            else:
                self.version[service][2] += 1  # hotfix
            self.incident = None

    def _message(self, service, method, route, path, status, latency, user, kind):
        rng = self.rng
        dependency = DEPENDENCIES[service]
        if status >= 500:
            if status == 504 or kind == "dependency_timeout":
                return f"Upstream {dependency} timed out after {latency}ms on {method} {path}"
            if status == 503:
                return f"DB connection pool exhausted (active=50, idle=0, waiting={rng.randint(5, 80)}) on {method} {path}"
            if status == 502:
                return f"Bad gateway from {dependency}: connection reset by peer"
            return rng.choice(
                (
                    f"Unhandled exception in {route}: NullReferenceError: 'customer' was null",
                    f"Unhandled exception in {route}: TimeoutError: lock wait exceeded",
                    f"Unhandled exception in {route}: KeyError: 'price'",
                )
            )
        if status >= 400:
            if status == 401:
                return f"Login failed for user {user or 'unknown'}: invalid credentials"
            if status == 404:
                return f"Resource not found: {path}"
            if status == 429:
                return f"Rate limit exceeded on {route} (limit=100/min)"
            if status == 409:
                return f"Conflict on {path}: resource version mismatch"
            return f"Validation failed on {route}: field '{rng.choice(('quantity', 'email', 'sku', 'amount'))}' is invalid"
        if latency > 1000:
            return f"Slow request: {method} {path} took {latency}ms"
        if service == "auth-service" and route == "/auth/login":
            return f"User {user or 'guest'} logged in (mfa={'yes' if rng.random() < 0.4 else 'no'})"
        if service == "orders-service" and method == "POST":
            return (
                f"Order ORD-{hex_id(rng, 40).upper()} created with {rng.randint(1, 6)} items total={rng.uniform(8, 600):.2f} USD"
            )
        if service == "payments-service" and route == "/payments/authorize":
            return f"Payment authorized amount={rng.uniform(5, 900):.2f} USD issuer_response=00 in {latency}ms"
        if service == "inventory-service" and method == "GET":
            return f"Stock level for {path.rsplit('/', 1)[-1]}: {rng.randint(0, 900)} units"
        if service == "search-service":
            return f"Search q='{rng.choice(SEARCH_TERMS)}' returned {rng.randint(0, 240)} results in {latency}ms"
        if service == "notification-service":
            channel = "email" if route.endswith("email") else "sms"
            template = rng.choice(("order_confirmation", "shipping_update", "password_reset"))
            return f"Queued {channel} notification template={template}"
        return f"{method} {path} {status} {latency}ms"

    def generate(self, ts):
        rng = self.rng
        self._maybe_incident()
        service = self.pick_service.pick(rng)
        method, route, median, error_rate, _share = self.pick_endpoint[service].pick(rng)
        host, region = rng.choice(self.pods[service])
        incident = self.incident if self.incident and self.incident["service"] == service else None
        kind = incident["kind"] if incident else None

        latency = median * rng.lognormvariate(0, 0.45)
        server_error_p = error_rate * 0.3
        if kind == "latency":
            latency *= rng.uniform(4, 10)
        elif kind == "errors":
            server_error_p = rng.uniform(0.2, 0.45)
        elif kind == "dependency_timeout" and rng.random() < 0.3:
            server_error_p = 1.0

        roll = rng.random()
        if roll < server_error_p:
            status = 504 if kind == "dependency_timeout" else SERVER_ERRORS.pick(rng)
            if status == 504:
                latency = rng.uniform(3000, 30000)
        elif roll < server_error_p + error_rate * 0.7:
            status = 401 if route == "/auth/login" else CLIENT_ERRORS.pick(rng)
        elif route == "/auth/logout":
            status = 204
        elif method == "POST" and route in ("/orders", "/api/v1/cart"):
            status = 201
        elif method == "GET" and rng.random() < 0.04:
            status = 304
        else:
            status = 200
        latency_ms = max(1, int(latency))

        if status >= 500:
            level = "ERROR"
        elif status >= 400 or latency_ms > 1000:
            level = "WARN"
        else:
            level = "DEBUG" if rng.random() < 0.03 else "INFO"

        customer = rng.choice(self.customers) if rng.random() < 0.7 else None
        user = customer["customer_id"] if customer else None
        if "{id}" in route:
            path = route.replace("{id}", rng.choice(("P", "ORD-")) + str(rng.randint(10000, 99999)))
        elif "{sku}" in route:
            path = route.replace("{sku}", f"P{rng.randint(1, 420):05d}")
        else:
            path = route
        bytes_sent = 0 if status in (204, 304) else int(math.exp(rng.gauss(7.2 if method == "GET" else 5.5, 0.8)))

        return [
            {
                "log_id": make_id("LOG", ts, rng),
                "event_time": iso(ts),
                "service": service,
                "version": self._version(service),
                "host": host,
                "region": region,
                "level": level,
                "http_method": method,
                "endpoint": route,
                "status_code": status,
                "latency_ms": latency_ms,
                "bytes_sent": bytes_sent,
                "trace_id": hex_id(rng, 128),
                "user_id": user,
                "client_ip": f"{rng.choice(('203.0.113', '198.51.100', '192.0.2'))}.{rng.randint(1, 254)}",
                "message": self._message(service, method, route, path, status, latency_ms, user, kind),
            }
        ]
