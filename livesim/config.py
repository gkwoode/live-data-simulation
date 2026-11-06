"""Settings from environment variables (prefix LIVESIM_), an optional .env file and CLI flags."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

ENV_PREFIX = "LIVESIM_"


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = Path("data")
    seed: int = 42
    autostart: bool = True  # start every stream at its default rate on first launch
    history_days: float = 2.0  # backfill this much history into empty stream tables on start (0 disables)
    max_rows: int = 200_000  # per-stream retention in the built-in SQLite store
    tcp_port: int = 9999  # newline-delimited JSON firehose for Spark / netcat; 0 disables
    public_url: str = ""  # e.g. a tunnel URL; used in generated connection snippets
    api_key: str = ""  # when set, required on /api, /odata and /ws
    allowed_hosts: str = ""  # extra Host headers to accept (comma separated, '*' accepts any)
    odata_page_size: int = 5000
    log_level: str = "info"
    # Sink defaults. All of these can also be changed at runtime from the Sinks page.
    lake_enabled: bool = False
    lake_format: str = "jsonl"
    lake_roll_seconds: int = 60
    lake_retention_hours: float = 24.0
    sql_url: str = ""
    sql_schema: str = ""
    kafka_bootstrap: str = ""
    kafka_topic_prefix: str = "livesim."
    kafka_topic: str = ""  # single topic for every stream (e.g. an Event Hub); empty = one topic per stream
    kafka_config: dict[str, Any] = field(default_factory=dict)  # extra librdkafka settings as JSON

    @property
    def db_path(self) -> Path:
        return self.data_dir / "livesim.db"

    @property
    def lake_dir(self) -> Path:
        return self.data_dir / "lake"

    @classmethod
    def load(cls, env_file: str | Path = ".env", **overrides: Any) -> "Settings":
        values: dict[str, str] = {}
        path = Path(env_file)
        if path.is_file():
            values.update(_parse_env_file(path))
        values.update({k: v for k, v in os.environ.items() if k.startswith(ENV_PREFIX)})
        settings = cls()
        for f in fields(cls):
            raw = values.get(ENV_PREFIX + f.name.upper())
            if raw is not None and raw != "":
                setattr(settings, f.name, _coerce(f.name, raw, getattr(settings, f.name)))
        for key, value in overrides.items():
            if value is not None:
                setattr(settings, key, value)
        settings.data_dir = Path(settings.data_dir)
        settings.lake_format = settings.lake_format.lower()
        return settings


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def _coerce(name: str, raw: str, default: Any) -> Any:
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    if isinstance(default, Path):
        return Path(raw)
    if isinstance(default, dict):
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError(f"{ENV_PREFIX}{name.upper()} must be a JSON object")
        return value
    return raw
