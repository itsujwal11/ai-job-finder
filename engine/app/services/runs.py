"""Run lifecycle: start (with task planning), finish (stats + digest), and n8n error reports."""
from __future__ import annotations

import logging
from typing import Any

from .. import db
from ..config import Config, Env, get_env, load_config
from ..events import log_event
from ..profile import load_profile
from .tasks import PRIORITY, board_url, enqueue

log = logging.getLogger(__name__)


class RunConflict(RuntimeError):
    pass


class RunNotFound(LookupError):
    pass


def get_run(run_id: int) -> dict[str, Any]:
    run = db.fetch_one("SELECT * FROM runs WHERE id = %s", (run_id,))
    if run is None:
        raise RunNotFound(f"Run {run_id} not found")
    return run


def get_running_run(run_id: int) -> dict[str, Any] | None:
    run = get_run(run_id)
    return run if run["status"] == "running" else None


def bump_counter(run_id: int, key: str) -> int:
    row = db.fetch_one(
        "UPDATE runs SET stats = jsonb_set(stats, ARRAY[%s], to_jsonb(COALESCE((stats->>%s)::int, 0) + 1))"
        " WHERE id = %s RETURNING (stats->>%s)::int AS value",
        (key, key, run_id, key),
    )
    return int(row["value"]) if row else 0


def start_run(trigger: str = "manual", n8n_execution_id: str | None = None) -> dict[str, Any]:
    cfg = load_config()  # raises ConfigError
    env = get_env()
    profile = load_profile()  # raises ProfileError - never run without the CV
    with db.connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(724502)")
        conn.execute(
            "UPDATE runs SET status = 'failed', finished_at = now(), error = 'Marked stale: no completion within 6 hours'"
            " WHERE status = 'running' AND started_at < now() - interval '6 hours'"
        )
        active = db.fetch_one("SELECT id, started_at FROM runs WHERE status = 'running' ORDER BY id LIMIT 1", conn=conn)
        if active:
            raise RunConflict(f"Run #{active['id']} is still running (started {active['started_at']:%Y-%m-%d %H:%M} UTC)")
        run = db.fetch_one(
            "INSERT INTO runs (trigger, profile_hash, config_hash, n8n_execution_id) VALUES (%s, %s, %s, %s) RETURNING id",
            (trigger, profile.digest, cfg.digest, n8n_execution_id),
            conn,
        )
        run_id = int(run["id"])
        planned = plan_tasks(conn, run_id, cfg, env)
        log_event("run_started", f"Run #{run_id} started ({trigger})", run_id=run_id, data={"planned": planned}, conn=conn)

    warnings = []
    if not env.ai_available:
        warnings.append("AI API Key is not set: AI analysis is paused, rule-based filtering still runs")
    if not env.search_providers():
        warnings.append("No search API keys set: search-engine discovery skipped")
    if not (env.telegram_enabled or env.email_enabled):
        warnings.append("No notification channel configured (Telegram or email)")
    return {"run_id": run_id, "planned_tasks": planned, "warnings": warnings}


def plan_tasks(conn, run_id: int, cfg: Config, env: Env) -> dict[str, int]:
    planned: dict[str, int] = {}

    def add(kind: str, source: str, label: str, url: str, params: dict | None = None, priority: int | None = None) -> None:
        if enqueue(conn, run_id, kind, source, label, url, params, priority if priority is not None else PRIORITY[kind]):
            planned[kind] = planned.get(kind, 0) + 1

    for name, source in (cfg.get("sources") or {}).items():
        if not source or not source.get("enabled"):
            continue
        if name == "weworkremotely":
            for feed_url in source.get("feeds", []):
                add("feed", name, f"We Work Remotely: {feed_url.rsplit('/', 1)[-1].removesuffix('.rss')}", feed_url)
        elif name == "hackernews":
            for thread in source.get("threads", []):
                add("hn", name, f"Hacker News: {thread.replace('_', ' ')}", f"hn://{thread}", {"thread": thread})
        else:
            params = {k: v for k, v in source.items() if k not in ("enabled", "url")}
            add("feed", name, name.title(), source["url"], params)

    for seed in cfg.get("ats.seed_boards") or []:
        db.execute(
            "INSERT INTO ats_boards (provider, slug, company_name, discovered_via) VALUES (%s, %s, %s, 'seed')"
            " ON CONFLICT (provider, slug) DO NOTHING",
            (seed["provider"], seed["slug"], seed.get("company_name")),
            conn,
        )
    boards = db.fetch_all(
        """
        SELECT provider, slug, company_name FROM ats_boards
        WHERE enabled AND consecutive_failures < %s
        ORDER BY (last_fetched_at IS NULL) DESC, relevant_jobs_seen DESC, last_fetched_at ASC
        LIMIT %s
        """,
        (int(cfg.get("ats.disable_after_failures", 5)), int(cfg.get("pipeline.max_boards_per_run", 80))),
        conn,
    )
    for board in boards:
        add("ats_board", board["provider"], f"{board['provider']}: {board['slug']}", board_url(board["provider"], board["slug"]),
            {"slug": board["slug"], "company_name": board["company_name"]})

    providers = [p for p in (cfg.get("search.providers") or []) if p in env.search_providers()]
    queries = list(cfg.get("search.queries") or [])
    if providers and queries:
        offset = run_id % len(queries)  # rotate queries across runs
        rotated = queries[offset:] + queries[:offset]
        budget = int(cfg.get("search.max_queries_per_run", 12))
        pairs = [(q, p) for q in rotated for p in providers][:budget]
        for query, provider in pairs:
            add("search", provider, f"{provider}: {query}", f"search://{provider}/{query}", {"query": query})

    if env.ai_available and cfg.get("ai.web_discovery.enabled", True):
        add("ai_discovery", "claude", "Claude web search discovery", "ai://discovery")

    pending_pages = db.fetch_all(
        "SELECT url FROM discovered_urls WHERE status = 'pending' AND attempts < 3 ORDER BY first_seen_at LIMIT %s",
        (int(cfg.get("pipeline.max_pages_per_run", 60)),),
        conn,
    )
    for page in pending_pages:
        add("page", "web", page["url"][:120], page["url"])
    return planned


def compute_run_stats(run_id: int) -> dict[str, Any]:
    tasks = db.fetch_all("SELECT kind, status, count(*) AS n FROM fetch_tasks WHERE run_id = %s GROUP BY kind, status", (run_id,))
    discovery = db.fetch_one(
        """
        SELECT count(*) AS discovered_new,
               count(*) FILTER (WHERE pipeline_status = 'duplicate') AS duplicates
        FROM opportunities WHERE first_run_id = %s
        """,
        (run_id,),
    )
    processed = db.fetch_one(
        """
        SELECT count(*) AS processed,
               count(*) FILTER (WHERE pipeline_status = 'filtered_out') AS filtered_out,
               count(*) FILTER (WHERE recommendation = 'blocked') AS blocked,
               count(*) FILTER (WHERE pipeline_status = 'analyzed') AS analyzed,
               count(*) FILTER (WHERE pipeline_status = 'awaiting_ai') AS awaiting_ai,
               count(*) FILTER (WHERE pipeline_status = 'analysis_failed') AS analysis_failed,
               count(*) FILTER (WHERE recommendation = 'auto_apply_candidate') AS auto_apply_candidates,
               count(*) FILTER (WHERE recommendation = 'manual_apply') AS manual_apply,
               count(*) FILTER (WHERE recommendation = 'approval_required') AS approval_required,
               count(*) FILTER (WHERE recommendation = 'ignore') AS ignored
        FROM opportunities WHERE last_processed_run_id = %s
        """,
        (run_id,),
    )
    cost = db.fetch_value("SELECT COALESCE(SUM(cost_usd), 0) AS c FROM ai_usage WHERE run_id = %s", (run_id,))
    by_status: dict[str, int] = {}
    by_kind: dict[str, dict[str, int]] = {}
    for row in tasks:
        by_status[row["status"]] = by_status.get(row["status"], 0) + row["n"]
        by_kind.setdefault(row["kind"], {})[row["status"]] = row["n"]
    return {
        "tasks": by_status,
        "tasks_by_kind": by_kind,
        **(discovery or {}),
        **(processed or {}),
        "ai_cost_usd": round(float(cost or 0), 4),
    }


def finish_run(run_id: int) -> dict[str, Any]:
    from . import notify  # local import avoids a cycle

    run = get_run(run_id)
    if run["status"] != "running":
        return {"run_id": run_id, "status": run["status"], "stats": run["stats"], "notifications": [], "note": "Run already finished"}
    db.execute(
        "UPDATE fetch_tasks SET status = 'skipped', error = 'Run finished before this task executed', finished_at = now()"
        " WHERE run_id = %s AND status IN ('pending', 'running')",
        (run_id,),
    )
    db.execute("UPDATE runs SET stage = 'notifying' WHERE id = %s", (run_id,))
    stats = compute_run_stats(run_id)
    notifications = notify.send_run_digest(run_id, stats)
    db.execute(
        "UPDATE runs SET status = 'completed', stage = 'done', finished_at = now(), stats = stats || %s WHERE id = %s",
        (db.jsonb(stats), run_id),
    )
    log_event("run_completed", f"Run #{run_id} completed", run_id=run_id, data=stats)
    return {"run_id": run_id, "status": "completed", "stats": stats, "notifications": notifications}


def record_error(payload: dict[str, Any]) -> dict[str, Any]:
    from . import notify

    run_id = payload.get("run_id")
    if not run_id:
        latest = db.fetch_one("SELECT id FROM runs WHERE status = 'running' ORDER BY id DESC LIMIT 1")
        run_id = latest["id"] if latest else None
    node = payload.get("node") or "unknown node"
    message = str(payload.get("message") or "Unknown error")[:1500]
    log_event("workflow_error", f"n8n workflow failed at '{node}': {message}", level="error", run_id=run_id, data=payload)
    if run_id:
        db.execute(
            "UPDATE runs SET status = 'failed', finished_at = now(), error = %s WHERE id = %s AND status = 'running'",
            (f"{node}: {message}", run_id),
        )
    sent: list[dict[str, Any]] = []
    if load_config().get("notifications.alert_on_errors", True):
        text = f"⚠️ Job discovery workflow failed\nNode: {node}\nError: {message}"
        if payload.get("execution_url"):
            text += f"\nExecution: {payload['execution_url']}"
        sent = notify.send_alert(text, run_id=run_id)
    return {"logged": True, "run_id": run_id, "notifications": sent}
