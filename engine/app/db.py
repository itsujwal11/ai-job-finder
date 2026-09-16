"""PostgreSQL access: connection pool, tiny query helpers and migrations."""
from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .config import MIGRATIONS_DIR

log = logging.getLogger(__name__)

_pool: ConnectionPool | None = None
_dumps = partial(json.dumps, default=str, ensure_ascii=False)


def jsonb(value: Any) -> Jsonb:
    return Jsonb(value, dumps=_dumps)


def init_pool(dsn: str, max_size: int = 10) -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            dsn,
            min_size=1,
            max_size=max_size,
            kwargs={"row_factory": dict_row},
            open=True,
        )
        _pool.wait(timeout=60)
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def get_pool() -> ConnectionPool:
    if _pool is None:
        raise RuntimeError("Database pool not initialised")
    return _pool


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """Yield a pooled connection; commits on success, rolls back on error."""
    with get_pool().connection() as conn:
        yield conn


def _run(sql: str, params: Any, conn: psycopg.Connection | None, handler):
    if conn is not None:
        return handler(conn.execute(sql, params))
    with connection() as own:
        return handler(own.execute(sql, params))


def fetch_all(sql: str, params: Any = None, conn: psycopg.Connection | None = None) -> list[dict[str, Any]]:
    return _run(sql, params, conn, lambda cur: cur.fetchall())


def fetch_one(sql: str, params: Any = None, conn: psycopg.Connection | None = None) -> dict[str, Any] | None:
    return _run(sql, params, conn, lambda cur: cur.fetchone())


def fetch_value(sql: str, params: Any = None, conn: psycopg.Connection | None = None) -> Any:
    row = fetch_one(sql, params, conn)
    return next(iter(row.values())) if row else None


def execute(sql: str, params: Any = None, conn: psycopg.Connection | None = None) -> int:
    return _run(sql, params, conn, lambda cur: cur.rowcount)


def run_migrations() -> list[str]:
    """Apply db/migrations/*.sql in filename order, once each."""
    applied_now: list[str] = []
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(724501)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            log.info("Applying migration %s", path.name)
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,))
            applied_now.append(path.name)
    return applied_now
