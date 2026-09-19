"""JSON API for the dashboard."""
from __future__ import annotations

import csv
import io
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from .. import db
from ..ai import AIUnavailable, BudgetExceeded, create_ai_service
from ..config import ConfigError, get_env, load_config
from ..events import log_event
from ..profile import ProfileError, load_profile
from ..services import apply, driver, materials as materials_service, runs as runs_service

router = APIRouter(prefix="/api/ui", tags=["dashboard"])

ACTIONABLE = ("auto_apply_candidate", "manual_apply", "approval_required")
VIEW_FILTERS: dict[str, str] = {
    "ready": "review_status IN ('ready_to_apply', 'approved', 'auto_apply_disabled')",
    "needs_approval": "review_status = 'pending_approval'",
    "applied": "review_status = 'applied'",
    "analyzed": "pipeline_status = 'analyzed'",
    "awaiting": "pipeline_status IN ('new', 'processing', 'awaiting_ai', 'analysis_failed')",
    "filtered": "(pipeline_status = 'filtered_out' OR recommendation IN ('blocked', 'ignore'))",
    "rejected": "review_status IN ('rejected', 'dismissed')",
    "duplicates": "pipeline_status = 'duplicate'",
    "all": "TRUE",
}
SORTS = {
    "score": "match_score DESC NULLS LAST, first_seen_at DESC",
    "newest": "first_seen_at DESC",
    "posted": "posted_at DESC NULLS LAST, first_seen_at DESC",
    "prescore": "prescore DESC NULLS LAST, first_seen_at DESC",
}
LIST_COLUMNS = (
    "id, title, company_name, source, source_url, location_text, remote_type, employment_type, salary_npr_monthly_min,"
    " salary_npr_monthly_max, match_score, recommendation, review_status, pipeline_status, nepal_eligibility,"
    " legitimacy_verdict, apply_method, materials_status, first_seen_at, posted_at, filter_reasons, duplicate_of,"
    " prescore"
)


# ---------------------------------------------------------------------------
# Status & overview
# ---------------------------------------------------------------------------
def system_status() -> dict[str, Any]:
    env = get_env()
    warnings: list[str] = []
    cfg = None
    try:
        cfg = load_config()
    except ConfigError as exc:
        warnings.append(f"Config error: {exc}")
    try:
        profile = load_profile()
        links_missing = [k for k, v in (profile.pref("candidate.links") or {}).items() if not v]
        profile_info: dict[str, Any] = {
            "ok": True, "name": profile.name, "cv_file": profile.cv_path.name,
            "experience_months": profile.experience_months(), "links_missing": links_missing,
        }
        if links_missing:
            warnings.append(f"Add your {', '.join(links_missing)} URL(s) in profile/preferences.yaml so tailored CVs can include them")
    except ProfileError as exc:
        profile_info = {"ok": False, "error": str(exc)}
        warnings.append(str(exc))
    if not env.ai_available:
        missing = ("OPENAI_BASE_URL / OPENAI_MODEL" if env.ai_provider == "openai_compatible" else "ANTHROPIC_API_KEY")
        warnings.append(f"{missing} is not set - AI matching is paused (rule-based filtering still runs)")
    if not env.search_providers():
        warnings.append("No search API key (Brave / Tavily / Google) - search-engine discovery is off")
    if not (env.telegram_enabled or env.email_enabled):
        warnings.append("No notification channel configured (Telegram or email)")
    auto_config = bool(cfg.get("applications.auto_apply_enabled")) if cfg else False
    return {
        "profile": profile_info,
        "ai": {"configured": env.ai_available, "provider": env.ai_provider,
               "model": env.ai_model(cfg.get("ai.model") if cfg else None),
               "daily_budget_usd": float(cfg.get("ai.daily_budget_usd", 0)) if cfg else 0},
        "search_providers": env.search_providers(),
        "notifications": {"telegram": env.telegram_enabled, "email": env.email_enabled},
        "auto_apply": {"config_flag": auto_config, "env_flag": env.auto_apply_enabled, "effective": False,
                       "note": "Phase 1: discovery and filtering only - no applications are ever submitted"},
        "thresholds": cfg.get("thresholds") if cfg else None,
        "warnings": warnings,
    }


@router.get("/overview")
def overview() -> dict[str, Any]:
    cfg = load_config()
    tz = cfg.get("timezone", "Asia/Kathmandu")
    approval_min, auto_min = int(cfg.get("thresholds.approval_min", 70)), int(cfg.get("thresholds.auto_apply_min", 85))
    kpis = db.fetch_one(
        """
        SELECT count(*) FILTER (WHERE first_seen_at > now() - interval '24 hours') AS discovered_24h,
               count(*) FILTER (WHERE first_seen_at > now() - interval '7 days') AS discovered_7d,
               count(*) FILTER (WHERE review_status = 'pending_approval') AS needs_approval,
               count(*) FILTER (WHERE review_status IN ('ready_to_apply', 'approved', 'auto_apply_disabled')) AS ready_to_apply,
               count(*) FILTER (WHERE review_status = 'applied') AS applied,
               count(*) FILTER (WHERE pipeline_status IN ('new', 'awaiting_ai', 'analysis_failed')) AS awaiting_analysis,
               count(*) AS total
        FROM opportunities
        """
    )
    funnel = db.fetch_one(
        """
        SELECT count(*) AS discovered,
               count(*) FILTER (WHERE pipeline_status <> 'duplicate') AS unique_postings,
               count(*) FILTER (WHERE pipeline_status IN ('analyzed', 'awaiting_ai', 'analysis_failed')) AS passed_filters,
               count(*) FILTER (WHERE pipeline_status = 'analyzed') AS analyzed,
               count(*) FILTER (WHERE match_score >= %s AND recommendation IN ('auto_apply_candidate', 'manual_apply', 'approval_required')) AS matches,
               count(*) FILTER (WHERE match_score >= %s AND recommendation IN ('auto_apply_candidate', 'manual_apply')) AS strong_matches
        FROM opportunities WHERE first_seen_at > now() - interval '7 days'
        """,
        (approval_min, auto_min),
    )
    daily = db.fetch_all(
        """
        SELECT to_char(d, 'YYYY-MM-DD') AS day,
               count(o.id) AS discovered,
               count(o.id) FILTER (WHERE o.recommendation IN ('auto_apply_candidate', 'manual_apply', 'approval_required')) AS matches
        FROM generate_series((now() AT TIME ZONE %s)::date - 13, (now() AT TIME ZONE %s)::date, interval '1 day') AS d
        LEFT JOIN opportunities o ON (o.first_seen_at AT TIME ZONE %s)::date = d::date
        GROUP BY d ORDER BY d
        """,
        (tz, tz, tz),
    )
    top = db.fetch_all(
        f"""
        SELECT {LIST_COLUMNS} FROM opportunities
        WHERE recommendation IN ('auto_apply_candidate', 'manual_apply', 'approval_required')
          AND review_status NOT IN ('rejected', 'dismissed', 'applied')
        ORDER BY match_score DESC, first_seen_at DESC LIMIT 6
        """
    )
    recent_runs = db.fetch_all(
        """
        SELECT r.id, r.trigger, r.status, r.stage, r.started_at, r.finished_at, r.stats, r.error,
               (SELECT COALESCE(SUM(cost_usd), 0) FROM ai_usage u WHERE u.run_id = r.id) AS ai_cost_usd
        FROM runs r ORDER BY r.id DESC LIMIT 6
        """
    )
    sources = db.fetch_all(
        """
        SELECT CASE WHEN kind = 'ats_board' THEN 'ATS company boards' WHEN kind = 'page' THEN 'Web pages'
                    WHEN kind = 'search' THEN 'Search: ' || source WHEN kind = 'ai_discovery' THEN 'Claude web search'
                    ELSE source END AS name,
               count(*) FILTER (WHERE status = 'ok') AS ok,
               count(*) FILTER (WHERE status = 'failed') AS failed,
               count(*) FILTER (WHERE status = 'blocked') AS blocked,
               count(*) FILTER (WHERE status = 'skipped') AS skipped,
               COALESCE(SUM(items_found), 0) AS found,
               COALESCE(SUM(items_new), 0) AS new,
               max(finished_at) AS last_run
        FROM fetch_tasks WHERE created_at > now() - interval '7 days'
        GROUP BY 1 ORDER BY 1
        """
    )
    spend = db.fetch_one(
        """
        SELECT COALESCE(SUM(cost_usd) FILTER (WHERE (created_at AT TIME ZONE %s)::date = (now() AT TIME ZONE %s)::date), 0) AS today,
               COALESCE(SUM(cost_usd) FILTER (WHERE created_at > now() - interval '7 days'), 0) AS last_7d,
               COALESCE(SUM(cost_usd) FILTER (WHERE created_at > now() - interval '30 days'), 0) AS last_30d
        FROM ai_usage
        """,
        (tz, tz),
    )
    leads = db.fetch_value("SELECT count(*) AS n FROM discovered_urls WHERE status = 'lead_only'")
    boards = db.fetch_value("SELECT count(*) AS n FROM ats_boards WHERE enabled")
    return {
        "kpis": {**kpis, "leads": leads, "ats_boards": boards},
        "funnel": funnel, "daily": daily, "top_matches": top, "recent_runs": recent_runs, "sources": sources,
        "ai_spend": spend, "status": system_status(),
    }


# ---------------------------------------------------------------------------
# Opportunities
# ---------------------------------------------------------------------------
@router.get("/opportunities")
def list_opportunities(
    view: str = "ready",
    q: str | None = None,
    source: str | None = None,
    sort: str = "score",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> dict[str, Any]:
    clauses = [VIEW_FILTERS.get(view, "TRUE")]
    params: list[Any] = []
    if q:
        clauses.append("(title ILIKE %s OR company_name ILIKE %s)")
        params += [f"%{q}%", f"%{q}%"]
    if source:
        clauses.append("source = %s")
        params.append(source)
    where = " AND ".join(f"({c})" for c in clauses)
    total = db.fetch_value(f"SELECT count(*) AS n FROM opportunities WHERE {where}", params)
    items = db.fetch_all(
        f"SELECT {LIST_COLUMNS} FROM opportunities WHERE {where} ORDER BY {SORTS.get(sort, SORTS['score'])} LIMIT %s OFFSET %s",
        params + [page_size, (page - 1) * page_size],
    )
    counts = db.fetch_one("SELECT " + ", ".join(f"count(*) FILTER (WHERE {f}) AS {k}" for k, f in VIEW_FILTERS.items()) + " FROM opportunities")
    sources = db.fetch_all("SELECT source, count(*) AS n FROM opportunities GROUP BY source ORDER BY n DESC")
    return {"items": items, "total": total, "page": page, "page_size": page_size, "counts": counts, "sources": sources}


def _detail(opportunity_id: int) -> dict[str, Any]:
    row = db.fetch_one("SELECT * FROM opportunities WHERE id = %s", (opportunity_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    materials = db.fetch_all(
        "SELECT id, version, cv_markdown, cover_letter, verification, verified, model, output_dir, created_at"
        " FROM application_materials WHERE opportunity_id = %s ORDER BY version DESC",
        (opportunity_id,),
    )
    cfg, env = load_config(), get_env()
    return {
        "opportunity": row,
        "materials": materials,
        "events": db.fetch_all(
            "SELECT id, occurred_at, level, type, message FROM events WHERE opportunity_id = %s ORDER BY occurred_at DESC LIMIT 100",
            (opportunity_id,),
        ),
        "sightings": db.fetch_all(
            "SELECT source, url, seen_at FROM opportunity_sightings WHERE opportunity_id = %s ORDER BY seen_at DESC LIMIT 50",
            (opportunity_id,),
        ),
        "application": db.fetch_one("SELECT * FROM applications WHERE opportunity_id = %s", (opportunity_id,)),
        "duplicates": db.fetch_all(
            "SELECT id, title, company_name, source, source_url FROM opportunities WHERE duplicate_of = %s", (opportunity_id,),
        ),
        "original": db.fetch_one(
            "SELECT id, title, company_name, source FROM opportunities WHERE id = %s", (row["duplicate_of"],),
        ) if row["duplicate_of"] else None,
        "auto_apply_blockers": apply.auto_apply_blockers(row, materials[0] if materials else None, cfg, env),
        "thresholds": cfg.get("thresholds"),
    }


@router.get("/opportunities/{opportunity_id}")
def get_opportunity(opportunity_id: int) -> dict[str, Any]:
    return _detail(opportunity_id)


class DecisionBody(BaseModel):
    action: Literal["approve", "reject", "dismiss", "reopen", "mark_applied", "note"]
    notes: str | None = None


_REOPEN = {"auto_apply_candidate": "auto_apply_disabled", "manual_apply": "ready_to_apply", "approval_required": "pending_approval"}


@router.post("/opportunities/{opportunity_id}/decision")
def decide(opportunity_id: int, body: DecisionBody) -> dict[str, Any]:
    row = db.fetch_one("SELECT id, title, recommendation FROM opportunities WHERE id = %s", (opportunity_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    if body.action == "mark_applied":
        try:
            apply.mark_applied(opportunity_id, body.notes, load_config())
        except apply.DuplicateApplication as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    elif body.action == "note":
        db.execute("UPDATE opportunities SET user_notes = %s, updated_at = now() WHERE id = %s", (body.notes, opportunity_id))
    else:
        status = {"approve": "approved", "reject": "rejected", "dismiss": "dismissed"}.get(body.action) or _REOPEN.get(row["recommendation"], "none")
        db.execute(
            "UPDATE opportunities SET review_status = %s, user_notes = COALESCE(%s, user_notes), updated_at = now() WHERE id = %s",
            (status, body.notes, opportunity_id),
        )
    log_event("user_decision", f"{body.action}: {row['title']}", opportunity_id=opportunity_id, data={"notes": body.notes})
    return _detail(opportunity_id)


@router.post("/opportunities/{opportunity_id}/materials")
def generate_materials(opportunity_id: int) -> dict[str, Any]:
    cfg, env = load_config(), get_env()
    row = db.fetch_one("SELECT id, analysis FROM opportunities WHERE id = %s", (opportunity_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    if not row["analysis"]:
        raise HTTPException(status_code=409, detail="This opportunity has not been analysed yet")
    try:
        profile = load_profile()
        ai = create_ai_service(cfg, env, profile)
        ai.ensure_budget()
    except ProfileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AIUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except BudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    db.execute("UPDATE opportunities SET materials_status = 'generating', updated_at = now() WHERE id = %s", (opportunity_id,))
    result = materials_service.generate_for_opportunity(opportunity_id, None, ai, cfg, profile)
    return {"result": result, **_detail(opportunity_id)}


@router.get("/materials/{material_id}/{filename}")
def download_material(material_id: int, filename: Literal["cv.md", "cover_letter.md", "cv.docx", "cover_letter.docx"]) -> Response:
    row = db.fetch_one(
        "SELECT m.cv_markdown, m.cover_letter, m.version, o.id AS opportunity_id, o.company_name"
        " FROM application_materials m JOIN opportunities o ON o.id = m.opportunity_id WHERE m.id = %s",
        (material_id,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Materials not found")
    content = row["cv_markdown"] if filename.startswith("cv") else row["cover_letter"]
    download_name = f"{materials_service._slug(row['company_name'])}-{filename}"
    headers = {"Content-Disposition": f'attachment; filename="{download_name}"'}
    if filename.endswith(".md"):
        return Response(content, media_type="text/markdown; charset=utf-8", headers=headers)
    buffer = io.BytesIO()
    materials_service.markdown_to_docx(content, buffer)
    return Response(
        buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Applications, runs, logs
# ---------------------------------------------------------------------------
@router.get("/applications")
def list_applications() -> dict[str, Any]:
    return {"items": db.fetch_all(
        """
        SELECT a.*, o.title, o.company_name, o.source_url, o.match_score, o.apply_method
        FROM applications a JOIN opportunities o ON o.id = a.opportunity_id
        ORDER BY a.applied_at DESC
        """
    )}


class ApplicationUpdate(BaseModel):
    status: Literal["applied", "interviewing", "offer", "rejected", "withdrawn", "no_response"] | None = None
    notes: str | None = None


@router.patch("/applications/{application_id}")
def update_application(application_id: int, body: ApplicationUpdate) -> dict[str, Any]:
    row = db.fetch_one(
        "UPDATE applications SET status = COALESCE(%s, status), notes = COALESCE(%s, notes), updated_at = now()"
        " WHERE id = %s RETURNING *",
        (body.status, body.notes, application_id),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Application not found")
    log_event("application_updated", f"Application #{application_id} -> {row['status']}", opportunity_id=row["opportunity_id"])
    return row


@router.post("/runs")
def start_run() -> dict[str, Any]:
    """Start a run and drive it to completion in the background (the dashboard's Run now)."""
    try:
        started = runs_service.start_run("dashboard")
    except runs_service.RunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ProfileError, ConfigError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        driver.start_background(started["run_id"])
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return started


@router.get("/runs/active")
def active_run() -> dict[str, Any]:
    run = db.fetch_one(
        "SELECT id, trigger, status, stage, started_at, stats FROM runs WHERE status = 'running' ORDER BY id DESC LIMIT 1"
    )
    return {"run": run, "driving": driver.is_driving()}


@router.get("/runs")
def list_runs(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    return {"items": db.fetch_all(
        """
        SELECT r.*, (SELECT COALESCE(SUM(cost_usd), 0) FROM ai_usage u WHERE u.run_id = r.id) AS ai_cost_usd
        FROM runs r ORDER BY r.id DESC LIMIT %s
        """,
        (limit,),
    )}


@router.get("/runs/{run_id}")
def get_run(run_id: int) -> dict[str, Any]:
    run = db.fetch_one("SELECT * FROM runs WHERE id = %s", (run_id,))
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {
        "run": run,
        "tasks": db.fetch_all(
            "SELECT id, kind, source, label, url, status, http_status, items_found, items_new, error, started_at, finished_at"
            " FROM fetch_tasks WHERE run_id = %s ORDER BY priority, id",
            (run_id,),
        ),
        "events": db.fetch_all(
            "SELECT id, occurred_at, level, type, message, opportunity_id FROM events WHERE run_id = %s ORDER BY occurred_at DESC LIMIT 300",
            (run_id,),
        ),
        "ai_usage": db.fetch_all(
            "SELECT purpose, count(*) AS calls, SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens,"
            " SUM(web_search_requests) AS web_searches, SUM(cost_usd) AS cost_usd FROM ai_usage WHERE run_id = %s GROUP BY purpose",
            (run_id,),
        ),
        "notifications": db.fetch_all(
            "SELECT channel, kind, status, subject, error, created_at FROM notifications WHERE run_id = %s ORDER BY id", (run_id,),
        ),
    }


@router.get("/events")
def list_events(level: str | None = None, type: str | None = None, limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
    clauses, params = ["TRUE"], []
    if level:
        clauses.append("level = %s")
        params.append(level)
    if type:
        clauses.append("type = %s")
        params.append(type)
    return {"items": db.fetch_all(
        f"SELECT id, occurred_at, level, type, run_id, opportunity_id, message FROM events WHERE {' AND '.join(clauses)}"
        " ORDER BY occurred_at DESC LIMIT %s",
        params + [limit],
    )}


@router.get("/leads")
def list_leads() -> dict[str, Any]:
    return {"items": db.fetch_all(
        "SELECT id, url, discovered_via, title_hint, snippet, first_seen_at FROM discovered_urls"
        " WHERE status = 'lead_only' ORDER BY first_seen_at DESC LIMIT 300"
    )}


@router.get("/boards")
def list_boards() -> dict[str, Any]:
    return {"items": db.fetch_all("SELECT * FROM ats_boards ORDER BY enabled DESC, last_fetched_at DESC NULLS FIRST LIMIT 1000")}


@router.get("/settings")
def settings() -> dict[str, Any]:
    cfg = load_config()
    return {
        "status": system_status(),
        "pipeline": cfg.get("pipeline"),
        "materials": cfg.get("materials"),
        "ai": {k: v for k, v in (cfg.get("ai") or {}).items() if k != "pricing"},
        "sources": {name: bool((src or {}).get("enabled")) for name, src in (cfg.get("sources") or {}).items()},
        "search": {"queries": cfg.get("search.queries"), "max_queries_per_run": cfg.get("search.max_queries_per_run")},
        "scoring": cfg.get("scoring.weights"),
        "compensation": cfg.get("compensation"),
    }


@router.get("/export/opportunities.csv")
def export_csv() -> StreamingResponse:
    rows = db.fetch_all(
        """
        SELECT id, first_seen_at, source, title, company_name, location_text, remote_type, employment_type, match_score,
               recommendation, review_status, pipeline_status, nepal_eligibility, legitimacy_verdict, apply_method,
               salary_npr_monthly_min, salary_npr_monthly_max, source_url, apply_url, apply_email
        FROM opportunities ORDER BY first_seen_at DESC
        """
    )
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()) if rows else ["id"])
    writer.writeheader()
    writer.writerows(rows)
    return StreamingResponse(
        iter([buffer.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="opportunities.csv"'},
    )
