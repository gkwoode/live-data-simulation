"""Command-line entry point: ``python -m livesim`` (or ``livesim`` when installed)."""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .config import Settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="livesim", description="Live data simulator for data professionals.")
    parser.add_argument("--host", help="Interface to bind (default 127.0.0.1; use 0.0.0.0 for LAN/Docker)")
    parser.add_argument("--port", type=int, help="HTTP port (default 8000)")
    parser.add_argument("--data-dir", help="Folder for the SQLite store, landing zone and state (default ./data)")
    parser.add_argument("--seed", type=int, help="Seed for the reference data (default 42)")
    parser.add_argument(
        "--history-days", type=float, help="History to backfill into empty streams at start (default 2, 0 disables)"
    )
    parser.add_argument("--max-rows", type=int, help="Rows kept per stream in the SQLite store (default 200000)")
    parser.add_argument("--tcp-port", type=int, help="TCP JSON-lines firehose port (default 9999, 0 disables)")
    parser.add_argument("--api-key", help="Require this key on /api, /odata and /ws")
    parser.add_argument("--public-url", help="Public base URL (e.g. a tunnel) used in connection snippets")
    parser.add_argument("--no-autostart", action="store_true", help="Start with every stream paused")
    parser.add_argument("--env-file", default=".env", help="Optional .env file with LIVESIM_* settings")
    parser.add_argument("--log-level", choices=["critical", "error", "warning", "info", "debug"])
    parser.add_argument("--version", action="version", version=f"livesim {__version__}")
    args = parser.parse_args(argv)

    settings = Settings.load(
        args.env_file,
        host=args.host,
        port=args.port,
        data_dir=args.data_dir,
        seed=args.seed,
        history_days=args.history_days,
        max_rows=args.max_rows,
        tcp_port=args.tcp_port,
        api_key=args.api_key,
        public_url=args.public_url,
        autostart=False if args.no_autostart else None,
        log_level=args.log_level,
    )
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    try:
        import uvicorn
    except ImportError:
        sys.exit("uvicorn is not installed. Run: pip install -r requirements.txt")
    from .app import create_app

    shown_host = "localhost" if settings.host in ("0.0.0.0", "::", "127.0.0.1") else settings.host
    base = settings.public_url.rstrip("/") or f"http://{shown_host}:{settings.port}"
    lines = [f"LiveSim {__version__}", f"Dashboard     {base}/", f"REST docs     {base}/docs", f"OData feed    {base}/odata"]
    if settings.tcp_port:
        lines.append(f"TCP firehose  {shown_host}:{settings.tcp_port}")
    lines.append(f"SQLite store  {settings.db_path.resolve()}")
    print("\n" + "\n".join(f"  {line}" for line in lines) + "\n", flush=True)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level=settings.log_level)


if __name__ == "__main__":
    main()
