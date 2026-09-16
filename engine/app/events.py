"""Append-only audit log. Every discovery, decision, notification and error lands here."""
from __future__ import annotations

import logging
from typing import Any

import psycopg

from . import db

log = logging.getLogger("jobs")

_LEVEL_TO_LOGGING = {"debug": logging.DEBUG, "info": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR}


def log_event(
    type_: str,
    message: str,
    *,
    level: str = "info",
    run_id: int | None = None,
    opportunity_id: int | None = None,
    data: dict[str, Any] | None = None,
    conn: psycopg.Connection | None = None,
) -> None:
    log.log(_LEVEL_TO_LOGGING.get(level, logging.INFO), "[%s] run=%s opp=%s %s", type_, run_id, opportunity_id, message)
    sql = (
        "INSERT INTO events (level, type, run_id, opportunity_id, message, data)"
        " VALUES (%s, %s, %s, %s, %s, %s)"
    )
    params = (level, type_, run_id, opportunity_id, message[:4000], db.jsonb(data or {}))
    if conn is not None:
        db.execute(sql, params, conn)
        return
    try:
        db.execute(sql, params)
    except Exception:  # the audit log must never take the pipeline down
        log.exception("Failed to write event %s", type_)
