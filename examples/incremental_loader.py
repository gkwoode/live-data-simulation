"""Incremental, resumable load of a LiveSim stream into a local SQLite "warehouse" table.

The classic ingestion pattern: remember a high-water mark (the _seq cursor), fetch only newer
records, upsert them, repeat. Safe to stop and restart at any time. Standard library only.

    python examples/incremental_loader.py --stream orders --once
    python examples/incremental_loader.py --stream iot_telemetry --base http://localhost:8000 --interval 10
"""

import argparse
import json
import sqlite3
import time
import urllib.parse
import urllib.request


def fetch(base: str, stream: str, cursor: int, limit: int, api_key: str | None) -> dict:
    query = urllib.parse.urlencode({"after_seq": cursor, "limit": limit})
    request = urllib.request.Request(f"{base}/api/v1/streams/{stream}/records?{query}")
    if api_key:
        request.add_header("X-API-Key", api_key)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--stream", default="orders")
    parser.add_argument("--target", default="warehouse.db", help="SQLite file to load into")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--interval", type=float, default=5, help="seconds to wait once caught up")
    parser.add_argument("--api-key")
    parser.add_argument("--once", action="store_true", help="stop after catching up")
    args = parser.parse_args()

    db = sqlite3.connect(args.target)
    db.execute("CREATE TABLE IF NOT EXISTS _cursors (stream TEXT PRIMARY KEY, last_seq INTEGER)")
    row = db.execute("SELECT last_seq FROM _cursors WHERE stream = ?", (args.stream,)).fetchone()
    cursor = row[0] if row else 0
    print(f"Resuming {args.stream} from _seq > {cursor}")

    columns: list[str] | None = None
    while True:
        page = fetch(args.base.rstrip("/"), args.stream, cursor, args.limit, args.api_key)
        records = page["records"]
        if records:
            if columns is None:
                columns = list(records[0])
                column_list = ", ".join(f'"{c}"' for c in columns)
                db.execute(f'CREATE TABLE IF NOT EXISTS "{args.stream}" ({column_list}, PRIMARY KEY ("_seq"))')
            placeholders = ", ".join("?" for _ in columns)
            with db:  # one transaction: rows and cursor move together, so a crash never skips or doubles data
                db.executemany(
                    f'INSERT OR REPLACE INTO "{args.stream}" VALUES ({placeholders})',
                    [tuple(r[c] for c in columns) for r in records],
                )
                cursor = page["next_after_seq"]
                db.execute("INSERT OR REPLACE INTO _cursors VALUES (?, ?)", (args.stream, cursor))
            total = db.execute(f'SELECT COUNT(*) FROM "{args.stream}"').fetchone()[0]
            print(f"+{len(records):>6} rows  cursor={cursor}  table now {total} rows")
        if not page["has_more"]:
            if args.once:
                print(f"Up to date at _seq {cursor}.")
                break
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
