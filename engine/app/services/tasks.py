"""Fetch-task queue helpers."""
from __future__ import annotations

from typing import Any

import psycopg

from .. import db
from ..config import Config

PRIORITY = {"feed": 10, "local_board": 12, "hn": 15, "ats_board": 20, "ats_board_discovered": 25,
            "search": 30, "ai_discovery": 40, "page": 60}


def enqueue(
    conn: psycopg.Connection,
    run_id: int,
    kind: str,
    source: str,
    label: str,
    url: str,
    params: dict[str, Any] | None = None,
    priority: int = 50,
) -> bool:
    row = db.fetch_one(
        "INSERT INTO fetch_tasks (run_id, kind, source, label, url, params, priority)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING id",
        (run_id, kind, source, label[:300], url, db.jsonb(params or {}), priority),
        conn,
    )
    return row is not None


def count_tasks(conn: psycopg.Connection, run_id: int, kind: str | None = None) -> int:
    if kind:
        return int(db.fetch_value("SELECT count(*) AS n FROM fetch_tasks WHERE run_id = %s AND kind = %s", (run_id, kind), conn))
    return int(db.fetch_value("SELECT count(*) AS n FROM fetch_tasks WHERE run_id = %s", (run_id,), conn))


def enqueue_within_caps(
    conn: psycopg.Connection,
    cfg: Config,
    run_id: int,
    kind: str,
    source: str,
    label: str,
    url: str,
    params: dict[str, Any] | None = None,
    priority: int = 50,
) -> bool:
    if count_tasks(conn, run_id) >= int(cfg.get("pipeline.max_tasks_per_run", 400)):
        return False
    cap_key = {"page": "pipeline.max_pages_per_run", "ats_board": "pipeline.max_boards_per_run"}.get(kind)
    if cap_key and count_tasks(conn, run_id, kind) >= int(cfg.get(cap_key, 50)):
        return False
    return enqueue(conn, run_id, kind, source, label, url, params, priority)


def page_source(discovered_via: str | None) -> str:
    """Page tasks keep the name of the board that produced the link, else the generic "web".

    Used both when a link is first stored and when a still-pending link is re-queued on a later
    run, so a posting parsed from merojob is attributed to merojob either way.
    """
    via = discovered_via or ""
    return via.split(":", 1)[1] if via.startswith("local_board:") else "web"


def board_url(provider: str, slug: str) -> str:
    return f"ats://{provider}/{slug}"
