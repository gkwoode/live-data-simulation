"""SQLite store: the queryable copy of every stream and reference table.

The database runs in WAL mode so external tools (Power BI via ODBC, DBeaver, pandas, DuckDB)
can read it while the simulator writes. One connection, guarded by a lock, does all writes;
every read opens its own read-only connection.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Iterator, Sequence

from .schema import SQLITE_TYPES, SYSTEM_FIELDS, Field, TableDef

log = logging.getLogger("livesim.store")

_SQLITE_RECURSIVE = getattr(sqlite3, "SQLITE_RECURSIVE", 33)
_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, _SQLITE_RECURSIVE}
_ALLOWED_PRAGMAS = {"table_info", "table_xinfo", "table_list", "index_list", "index_info"}


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sql_lab_authorizer(action, arg1, arg2, db_name, trigger):
    if action in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and arg1 in _ALLOWED_PRAGMAS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self.tables: dict[str, list[Field]] = {}  # every table -> its columns in order
        self.stream_tables: set[str] = set()
        self.row_counts: dict[str, int] = {}
        self._insert_sql: dict[str, str] = {}

    # -- lifecycle ---------------------------------------------------------------------------

    def open(self, streams: dict[str, list[Field]], reference: dict[str, TableDef], fingerprint: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("CREATE TABLE IF NOT EXISTS _livesim_meta (key TEXT PRIMARY KEY, value TEXT)")
        self._conn = conn
        for name, fields in streams.items():
            self._ensure_stream_table(name, list(fields) + list(SYSTEM_FIELDS))
        row = conn.execute("SELECT value FROM _livesim_meta WHERE key = 'reference_fingerprint'").fetchone()
        reload = row is None or row[0] != fingerprint
        for table in reference.values():
            self._ensure_reference_table(table, reload)
        conn.execute("INSERT OR REPLACE INTO _livesim_meta VALUES ('reference_fingerprint', ?)", (fingerprint,))

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                finally:
                    self._conn.close()
                    self._conn = None

    def _columns_of(self, name: str) -> list[str]:
        return [r[1] for r in self._conn.execute(f"PRAGMA table_info({quote(name)})")]

    def _ensure_stream_table(self, name: str, fields: list[Field]) -> None:
        conn = self._conn
        expected = [f.name for f in fields]
        existing = self._columns_of(name)
        if existing and existing != expected:
            log.warning("Schema of stream table %s changed; recreating it", name)
            conn.execute(f"DROP TABLE {quote(name)}")
        cols = []
        for f in fields:
            if f.name == "_seq":
                cols.append(f"{quote(f.name)} INTEGER PRIMARY KEY")
            elif f.name == "_ingested_at":
                cols.append(f"{quote(f.name)} TEXT NOT NULL")
            else:
                cols.append(f"{quote(f.name)} {SQLITE_TYPES[f.type]}")
        conn.execute(f"CREATE TABLE IF NOT EXISTS {quote(name)} ({', '.join(cols)})")
        if "event_time" in expected:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {quote('ix_' + name + '_event_time')} ON {quote(name)} (event_time)")
        self._register(name, fields)
        self.stream_tables.add(name)

    def _ensure_reference_table(self, table: TableDef, reload: bool) -> None:
        conn = self._conn
        expected = [f.name for f in table.fields]
        existing = self._columns_of(table.name)
        if existing and existing == expected and not reload:
            self._register(table.name, table.fields)
            return
        cols = [f"{quote(f.name)} {SQLITE_TYPES[f.type]}{' PRIMARY KEY' if f.name == table.key else ''}" for f in table.fields]
        conn.execute("BEGIN")
        try:
            conn.execute(f"DROP TABLE IF EXISTS {quote(table.name)}")
            conn.execute(f"CREATE TABLE {quote(table.name)} ({', '.join(cols)})")
            placeholders = ", ".join("?" for _ in expected)
            conn.executemany(
                f"INSERT INTO {quote(table.name)} VALUES ({placeholders})",
                [tuple(row[c] for c in expected) for row in table.rows],
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        self._register(table.name, table.fields)

    def _register(self, name: str, fields: list[Field]) -> None:
        self.tables[name] = list(fields)
        names = [f.name for f in fields]
        self._insert_sql[name] = (
            f"INSERT OR IGNORE INTO {quote(name)} ({', '.join(quote(n) for n in names)}) VALUES ({', '.join('?' for _ in names)})"
        )
        self.row_counts[name] = self._conn.execute(f"SELECT COUNT(*) FROM {quote(name)}").fetchone()[0]

    # -- writes ------------------------------------------------------------------------------

    def insert_many(self, batches: dict[str, list[dict[str, Any]]]) -> int:
        total = 0
        with self._lock:
            conn = self._conn
            conn.execute("BEGIN")
            try:
                for table, records in batches.items():
                    if not records:
                        continue
                    names = [f.name for f in self.tables[table]]
                    cur = conn.executemany(self._insert_sql[table], [tuple(r.get(n) for n in names) for r in records])
                    inserted = max(cur.rowcount, 0)
                    self.row_counts[table] += inserted
                    total += inserted
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return total

    def enforce_retention(self, table: str, max_rows: int) -> int:
        with self._lock:
            conn = self._conn
            row = conn.execute(f"SELECT _seq FROM {quote(table)} ORDER BY _seq DESC LIMIT 1 OFFSET ?", (max_rows,)).fetchone()
            if row is None:
                return 0
            deleted = conn.execute(f"DELETE FROM {quote(table)} WHERE _seq <= ?", (row[0],)).rowcount
            self.row_counts[table] = max(0, self.row_counts[table] - deleted)
            return deleted

    def truncate(self, table: str) -> int:
        with self._lock:
            deleted = self._conn.execute(f"DELETE FROM {quote(table)}").rowcount
            self.row_counts[table] = 0
            return deleted

    # -- reads -------------------------------------------------------------------------------

    def reader(self) -> sqlite3.Connection:
        uri = self.path.resolve().as_uri() + "?mode=ro"
        return sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=30)

    def max_seq(self, table: str) -> int:
        with closing(self.reader()) as conn:
            return conn.execute(f"SELECT COALESCE(MAX(_seq), 0) FROM {quote(table)}").fetchone()[0]

    def _bool_positions(self, table: str, columns: Sequence[str]) -> list[int]:
        types = {f.name: f.type for f in self.tables[table]}
        return [i for i, c in enumerate(columns) if types.get(c) == "bool"]

    def iter_rows(
        self,
        table: str,
        columns: Sequence[str],
        where: str = "",
        params: Sequence[Any] = (),
        order: str = "",
        limit: int | None = None,
        offset: int = 0,
        chunk: int = 2000,
    ) -> Iterator[list[tuple]]:
        """Yield result rows in chunks; booleans are converted back from SQLite's 0/1."""
        sql = f"SELECT {', '.join(quote(c) for c in columns)} FROM {quote(table)}"
        if where:
            sql += f" WHERE {where}"
        if order:
            sql += f" ORDER BY {order}"
        if limit is not None:
            sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
        bools = self._bool_positions(table, columns)
        with closing(self.reader()) as conn:
            cur = conn.execute(sql, tuple(params))
            while True:
                rows = cur.fetchmany(chunk)
                if not rows:
                    return
                if bools:
                    converted = []
                    for row in rows:
                        row = list(row)
                        for i in bools:
                            if row[i] is not None:
                                row[i] = bool(row[i])
                        converted.append(tuple(row))
                    rows = converted
                yield rows

    def query(self, table: str, columns: Sequence[str], **kwargs: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for chunk in self.iter_rows(table, columns, **kwargs):
            out.extend(dict(zip(columns, row, strict=True)) for row in chunk)
        return out

    def count(self, table: str, where: str = "", params: Sequence[Any] = ()) -> int:
        if not where:
            return self.row_counts.get(table, 0)
        with closing(self.reader()) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {quote(table)} WHERE {where}", tuple(params)).fetchone()[0]

    def size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.path) + suffix)
            if p.exists():
                total += p.stat().st_size
        return total

    def run_readonly_sql(self, sql: str, max_rows: int = 1000, timeout_s: float = 8.0) -> dict[str, Any]:
        """Execute one read-only statement for SQL Lab, sandboxed by an authorizer and a time limit."""
        started = time.perf_counter()
        deadline = time.monotonic() + timeout_s
        with closing(self.reader()) as conn:
            conn.set_authorizer(_sql_lab_authorizer)
            conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 20_000)
            try:
                cur = conn.execute(sql)
            except sqlite3.OperationalError as exc:
                if "interrupted" in str(exc):
                    raise TimeoutError(f"Query exceeded the {timeout_s:.0f}s time limit") from exc
                raise
            columns = [d[0] for d in cur.description] if cur.description else []
            try:
                rows = cur.fetchmany(max_rows + 1)
            except sqlite3.OperationalError as exc:
                if "interrupted" in str(exc):
                    raise TimeoutError(f"Query exceeded the {timeout_s:.0f}s time limit") from exc
                raise
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        clean = [[v.hex() if isinstance(v, bytes) else v for v in row] for row in rows]
        return {
            "columns": columns,
            "rows": clean,
            "row_count": len(clean),
            "truncated": truncated,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
