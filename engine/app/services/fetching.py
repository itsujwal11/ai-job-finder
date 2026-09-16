"""Executes queued fetch tasks in small time-boxed batches (called repeatedly by n8n)."""
from __future__ import annotations

import logging
import time
from typing import Any

from .. import db
from ..ai import AIError, AIUnavailable, BudgetExceeded, ClaudeService, create_ai_service
from ..config import Config, get_env, load_config
from ..events import log_event
from ..models import DiscoveredLink, NormalizedJob, TaskResult
from ..pipeline.normalize import parse_datetime, parse_salary_text
from ..profile import load_profile
from ..sources.base import BlockedError, FetchError, PoliteClient, TaskContext, finalize
from ..sources.registry import get_handler
from .ingest import store_job, store_link
from .runs import bump_counter, get_running_run

log = logging.getLogger(__name__)


def _claim_task(run_id: int) -> dict[str, Any] | None:
    return db.fetch_one(
        """
        UPDATE fetch_tasks SET status = 'running', started_at = now()
        WHERE id = (
            SELECT id FROM fetch_tasks WHERE run_id = %s AND status = 'pending'
            ORDER BY priority, id LIMIT 1 FOR UPDATE SKIP LOCKED
        )
        RETURNING *
        """,
        (run_id,),
    )


def _ai_count(run_id: int, purpose: str) -> int:
    return int(db.fetch_value("SELECT count(*) AS n FROM ai_usage WHERE run_id = %s AND purpose = %s", (run_id, purpose)) or 0)


def _make_extractor(ai: ClaudeService, run_id: int):
    def extract(url: str, text: str) -> NormalizedJob | None:
        posting = ai.extract_posting(url, text, run_id)
        if not posting.is_job_posting or not posting.title.strip():
            return None
        salary = parse_salary_text(posting.salary_text) or {}
        return finalize(NormalizedJob(
            source="web",
            source_url=url,
            title=posting.title,
            company_name=posting.company_name,
            company_website=posting.company_website,
            location_text=posting.location_text,
            remote_type=posting.remote_type,
            employment_type=posting.employment_type,
            salary_text=posting.salary_text,
            salary_min=salary.get("min"),
            salary_max=salary.get("max"),
            salary_currency=salary.get("currency"),
            salary_period=salary.get("period"),
            description=text,
            apply_url=posting.apply_url or url,
            apply_email=posting.apply_email,
            posted_at=parse_datetime(posting.posted_date),
            raw={"ai_extracted": True},
        ))

    return extract


def _run_ai_discovery(ctx: TaskContext, ai: ClaudeService | None) -> TaskResult:
    if ai is None:
        return TaskResult(status="skipped", note="AI unavailable")
    leads = ai.discover_leads(list(ctx.cfg.get("search.queries") or []), ctx.run_id)
    links = [
        DiscoveredLink(
            url=lead["url"],
            via="ai_discovery",
            title=lead.get("title"),
            snippet=" | ".join(x for x in (lead.get("company"), lead.get("location_note"), lead.get("why_relevant")) if x),
        )
        for lead in leads
        if str(lead.get("url", "")).startswith("http")
    ]
    return TaskResult(links=links, note=f"{len(leads)} leads from Claude web search")


def _execute(task: dict[str, Any], ctx: TaskContext, ai: ClaudeService | None) -> TaskResult:
    try:
        if task["kind"] == "ai_discovery":
            return _run_ai_discovery(ctx, ai)
        handler = get_handler(task["kind"], task["source"])
        if handler is None:
            return TaskResult(status="skipped", note=f"No handler for {task['kind']}/{task['source']}")
        return handler(task, ctx)
    except BlockedError as exc:
        return TaskResult(status="blocked", http_status=exc.status, error=str(exc))
    except FetchError as exc:
        return TaskResult(status="failed", http_status=exc.status, error=str(exc))
    except BudgetExceeded as exc:
        return TaskResult(status="skipped", error=str(exc))
    except AIError as exc:
        return TaskResult(status="failed", error=str(exc))
    except Exception as exc:  # adapter bug or unexpected payload: record and continue with other sources
        log.exception("Task %s failed", task["id"])
        return TaskResult(status="failed", error=f"{type(exc).__name__}: {exc}")


def _store_result(task: dict[str, Any], result: TaskResult, cfg: Config, run_id: int) -> dict[str, int]:
    counts: dict[str, int] = {"found": len(result.jobs), "links": len(result.links)}
    with db.connection() as conn:
        for job in result.jobs:
            try:
                with conn.transaction():
                    outcome = store_job(conn, job, run_id, cfg)
            except Exception as exc:
                log.warning("Could not store job %s: %s", job.source_url, exc)
                outcome = "invalid"
            counts[outcome] = counts.get(outcome, 0) + 1
        for link in result.links:
            try:
                with conn.transaction():
                    outcome = store_link(conn, link, run_id, cfg)
            except Exception as exc:
                log.warning("Could not store link %s: %s", link.url, exc)
                outcome = "invalid"
            counts[outcome] = counts.get(outcome, 0) + 1

        db.execute(
            "UPDATE fetch_tasks SET status = %s, http_status = %s, items_found = %s, items_new = %s, error = %s, finished_at = now()"
            " WHERE id = %s",
            (result.status, result.http_status, counts["found"], counts.get("new", 0), result.error or result.note, task["id"]),
            conn,
        )
        if task["kind"] == "ats_board":
            ok = result.status == "ok"
            invalid_board = result.http_status == 404
            db.execute(
                """
                UPDATE ats_boards SET last_fetched_at = now(), last_status = %s,
                    consecutive_failures = CASE WHEN %s THEN 0 ELSE consecutive_failures + 1 END,
                    enabled = CASE WHEN %s THEN FALSE ELSE enabled END,
                    company_name = COALESCE(company_name, %s)
                WHERE provider = %s AND slug = %s
                """,
                (result.status if ok else (result.error or result.status)[:200], ok, invalid_board,
                 result.jobs[0].company_name if result.jobs else None, task["source"], task["params"].get("slug")),
                conn,
            )
        elif task["kind"] == "page":
            page_status = {"ok": "fetched", "blocked": "blocked", "skipped": "skipped"}.get(result.status, "failed")
            db.execute(
                """
                UPDATE discovered_urls SET attempts = attempts + 1, fetched_at = now(), status_reason = %s,
                    status = CASE WHEN %s = 'failed' AND attempts + 1 < 3 THEN 'pending' ELSE %s END
                WHERE url_hash = (SELECT url_hash FROM discovered_urls WHERE url = %s LIMIT 1)
                """,
                ((result.error or result.note or "")[:500], page_status, page_status, task["url"]),
                conn,
            )
    if result.status in ("failed", "blocked") and task["kind"] != "page":
        log_event(
            f"fetch_{result.status}", f"{task['label']}: {result.error}", level="warn", run_id=run_id,
            data={"task_id": task["id"], "http_status": result.http_status},
        )
    return counts


def fetch_next(run_id: int, max_tasks: int = 10, max_seconds: int = 90) -> dict[str, Any]:
    started = time.monotonic()
    if get_running_run(run_id) is None:
        return {"run_id": run_id, "executed": [], "remaining": 0, "note": "Run is not running"}
    if bump_counter(run_id, "fetch_batches") > 300:
        return {"run_id": run_id, "executed": [], "remaining": 0, "note": "Fetch batch limit reached"}

    cfg, env, profile = load_config(), get_env(), load_profile()
    db.execute(
        "UPDATE fetch_tasks SET status = 'failed', error = 'Interrupted (engine restart or timeout)', finished_at = now()"
        " WHERE run_id = %s AND status = 'running' AND started_at < now() - interval '15 minutes'",
        (run_id,),
    )
    try:
        ai: ClaudeService | None = create_ai_service(cfg, env, profile)
    except AIUnavailable:
        ai = None

    executed: list[dict[str, Any]] = []
    with PoliteClient(cfg) as client:
        ctx = TaskContext(client=client, cfg=cfg, env=env, run_id=run_id)
        while len(executed) < max_tasks and time.monotonic() - started < max_seconds:
            task = _claim_task(run_id)
            if task is None:
                break
            under_cap = _ai_count(run_id, "extract") < int(cfg.get("pipeline.max_ai_extractions_per_run", 15))
            ctx.extractor = _make_extractor(ai, run_id) if ai is not None and under_cap else None
            result = _execute(task, ctx, ai)
            counts = _store_result(task, result, cfg, run_id)
            executed.append({"task_id": task["id"], "label": task["label"], "status": result.status,
                             "error": result.error, **counts})

    remaining = int(db.fetch_value("SELECT count(*) AS n FROM fetch_tasks WHERE run_id = %s AND status = 'pending'", (run_id,)) or 0)
    return {"run_id": run_id, "executed": executed, "remaining": remaining}
