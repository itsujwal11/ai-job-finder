"""Filtering -> AI matching -> scoring -> decision -> materials, in time-boxed batches."""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from .. import db
from ..ai import AIError, AIUnavailable, BudgetExceeded, ClaudeService, create_ai_service
from ..config import Config, Env, get_env, load_config
from ..events import log_event
from ..models import JobAnalysis
from ..pipeline import legitimacy as legitimacy_checks
from ..pipeline.dedup import find_prior_application
from ..pipeline.legitimacy import CURATED_SOURCES, LegitimacyResult, combine_legitimacy, heuristic_legitimacy
from ..pipeline.normalize import ascii_lower, contains_term, host_matches, host_of, registrable_domain
from ..pipeline.policy import blocking_reasons, decide
from ..pipeline.rules import (
    EligibilityResult,
    SeniorityResult,
    assess_nepal_eligibility,
    assess_relevance,
    assess_seniority,
    combine_eligibility,
    detect_apply_method,
    staleness_reason,
)
from ..pipeline.scoring import ELIGIBILITY_POINTS, compensation_points, compute_match_score
from ..profile import CandidateProfile, load_profile
from ..sources.ats import enrich_smartrecruiters
from ..sources.base import FetchError, PoliteClient, TaskContext
from ..sources.page import enrich_from_page
from . import materials as materials_service
from .runs import bump_counter, get_running_run

log = logging.getLogger(__name__)

ACTIONABLE = ("auto_apply_candidate", "manual_apply", "approval_required")
_SENIOR_HINT = re.compile(r"\b(senior|lead|principal|director|executive|mid-senior|staff)\b", re.IGNORECASE)
_BOARD_HOSTS = ["remotive.com", "remoteok.com", "weworkremotely.com", "himalayas.app", "jobicy.com",
                "workingnomads.com", "ycombinator.com", "arbeitnow.com"]
_NEPAL_PLACES = ("nepal", "kathmandu", "lalitpur", "bhaktapur", "pokhara")


@dataclass
class RuleState:
    row: dict[str, Any]
    job: dict[str, Any]
    seniority: SeniorityResult
    eligibility: EligibilityResult
    legitimacy: LegitimacyResult
    apply_method: str
    apply_email: str | None
    signals: dict[str, Any]


# ---------------------------------------------------------------------------
# Batch entry point
# ---------------------------------------------------------------------------
def process_next(run_id: int, max_items: int = 6, max_seconds: int = 240) -> dict[str, Any]:
    started = time.monotonic()
    if get_running_run(run_id) is None:
        return {"run_id": run_id, "processed": 0, "results": [], "remaining": 0, "note": "Run is not running"}
    if bump_counter(run_id, "process_batches") > 300:
        return {"run_id": run_id, "processed": 0, "results": [], "remaining": 0, "note": "Process batch limit reached"}

    cfg, env, profile = load_config(), get_env(), load_profile()
    db.execute("UPDATE runs SET stage = 'processing' WHERE id = %s AND stage = 'fetching'", (run_id,))
    _reset_stale()

    ai, ai_note = _ai_service(cfg, env, profile)
    analysis_cap = int(cfg.get("pipeline.max_ai_analyses_per_run", 40))
    slots = max(0, analysis_cap - _ai_count(run_id, "analyze")) if ai else 0

    claimed = _claim(run_id, max_items, include_retry=slots > 0)
    results: list[dict[str, Any]] = []
    needs_ai: list[RuleState] = []
    with PoliteClient(cfg) as client:
        ctx = TaskContext(client=client, cfg=cfg, env=env, run_id=run_id)
        for row in claimed:
            try:
                outcome = _apply_rules(row, ctx, run_id)
            except Exception as exc:
                log.exception("Rule evaluation failed for opportunity %s", row["id"])
                _mark_failed(row, run_id, f"Rule evaluation error: {exc}", increment=True)
                results.append({"id": row["id"], "title": row["title"], "outcome": "analysis_failed", "error": str(exc)})
                continue
            if isinstance(outcome, RuleState):
                needs_ai.append(outcome)
            else:
                results.append(outcome)

    batch, deferred = needs_ai[:slots], needs_ai[slots:]
    for state in deferred:
        reason = ai_note or f"Per-run AI analysis limit ({analysis_cap}) reached"
        _await_ai(state, run_id, reason)
        results.append({"id": state.row["id"], "title": state.row["title"], "outcome": "awaiting_ai", "note": reason})
    if batch and ai is not None:
        workers = max(1, int(cfg.get("pipeline.analysis_concurrency", 3)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results.extend(pool.map(lambda s: _analyze(s, ai, cfg, env, profile, run_id), batch))

    if ai is not None and time.monotonic() - started < max_seconds:
        results.extend(_materials_batch(run_id, ai, cfg, profile, deadline=started + max_seconds))

    return {
        "run_id": run_id,
        "processed": len(claimed),
        "results": results,
        "remaining": _remaining(run_id, cfg, ai, analysis_cap),
        "ai_note": ai_note,
    }


def _ai_service(cfg: Config, env: Env, profile: CandidateProfile) -> tuple[ClaudeService | None, str | None]:
    try:
        service = create_ai_service(cfg, env, profile)
        service.ensure_budget()
        return service, None
    except (AIUnavailable, BudgetExceeded) as exc:
        return None, str(exc)


def _ai_count(run_id: int, purpose: str, distinct: bool = False) -> int:
    expr = "count(DISTINCT opportunity_id)" if distinct else "count(*)"
    return int(db.fetch_value(f"SELECT {expr} AS n FROM ai_usage WHERE run_id = %s AND purpose = %s", (run_id, purpose)) or 0)


def _reset_stale() -> None:
    db.execute("UPDATE opportunities SET pipeline_status = 'new' WHERE pipeline_status = 'processing' AND updated_at < now() - interval '30 minutes'")
    db.execute("UPDATE opportunities SET materials_status = 'none' WHERE materials_status = 'generating' AND updated_at < now() - interval '30 minutes'")


_CLAIMABLE = """
    (pipeline_status = 'new'
     OR (%(retry)s AND pipeline_status IN ('awaiting_ai', 'analysis_failed') AND analysis_attempts < 3))
    AND last_processed_run_id IS DISTINCT FROM %(run)s
"""


def _claim(run_id: int, limit: int, include_retry: bool) -> list[dict[str, Any]]:
    return db.fetch_all(
        f"""
        UPDATE opportunities SET pipeline_status = 'processing', updated_at = now()
        WHERE id IN (
            SELECT id FROM opportunities WHERE {_CLAIMABLE}
            ORDER BY (pipeline_status = 'new') DESC, posted_at DESC NULLS LAST, first_seen_at DESC
            LIMIT %(limit)s FOR UPDATE SKIP LOCKED
        )
        RETURNING *
        """,
        {"retry": include_retry, "run": run_id, "limit": limit},
    )


def _remaining(run_id: int, cfg: Config, ai: ClaudeService | None, analysis_cap: int) -> int:
    ai_ok = ai is not None and _ai_count(run_id, "analyze") < analysis_cap
    count = int(db.fetch_value(f"SELECT count(*) AS n FROM opportunities WHERE {_CLAIMABLE}", {"retry": ai_ok, "run": run_id}) or 0)
    if ai is not None and cfg.get("materials.enabled", True) and _ai_count(run_id, "materials", distinct=True) < int(cfg.get("materials.max_per_run", 10)):
        count += int(db.fetch_value(
            """
            SELECT count(*) AS n FROM opportunities
            WHERE pipeline_status = 'analyzed' AND materials_status = 'none' AND recommendation = ANY(%s)
              AND match_score >= %s AND review_status NOT IN ('rejected', 'dismissed', 'applied')
            """,
            (list(cfg.get("materials.generate_for") or ACTIONABLE), int(cfg.get("materials.min_score", 70))),
        ) or 0)
    return count


# ---------------------------------------------------------------------------
# Deterministic stage
# ---------------------------------------------------------------------------
def _apply_rules(row: dict[str, Any], ctx: TaskContext, run_id: int) -> RuleState | dict[str, Any]:
    cfg = ctx.cfg
    raw = row.get("raw") or {}
    job = dict(row)
    job["salary_text"] = raw.get("salary_text")

    stale = staleness_reason(row["posted_at"], row["expires_at"], int(cfg.get("pipeline.max_posting_age_days", 30)))
    if stale:
        return _filter(row, run_id, [stale], None, {})
    relevant, relevance_reason = assess_relevance(row["title"], row["description"] or "", cfg)
    if not relevant:
        return _filter(row, run_id, [relevance_reason], None, {})

    if len(row["description"] or "") < int(cfg.get("pipeline.min_description_chars", 200)) or row["description_quality"] != "full":
        enriched = _enrich(row, ctx)
        if enriched and len(enriched) > len(row["description"] or ""):
            job["description"] = enriched
            db.execute("UPDATE opportunities SET description = %s, description_quality = 'full' WHERE id = %s", (enriched, row["id"]))

    seniority = assess_seniority(job["title"], job["description"] or "", cfg)
    hint = str(raw.get("seniority_hint") or "")
    if hint and _SENIOR_HINT.search(hint) and not seniority.junior_title:
        seniority.reasons.append(f"Listed seniority level: {hint}")
        seniority.blocked = True
    eligibility = assess_nepal_eligibility(
        title=job["title"], location_text=row["location_text"], location_restrictions=row["location_restrictions"] or [],
        remote_type=row["remote_type"], description=job["description"] or "", cfg=cfg,
    )
    signals: dict[str, Any] = {
        "relevance": relevance_reason,
        "seniority": {"blocked": seniority.blocked, "reasons": seniority.reasons, "required_years": seniority.required_years},
        "nepal_eligibility_rule": {"status": eligibility.status, "evidence": eligibility.evidence, "explicit": eligibility.explicit},
    }
    if seniority.blocked:
        return _filter(row, run_id, seniority.reasons, "blocked", signals, eligibility)
    if eligibility.status == "not_eligible" and eligibility.explicit:
        return _filter(row, run_id, [f"Not open to Nepal-based candidates: {eligibility.evidence}"], "blocked", signals, eligibility)

    legitimacy = heuristic_legitimacy(job, cfg, _domain_age(job, ctx))
    signals["legitimacy_rule"] = {"score": legitimacy.score, "verdict": legitimacy.verdict, "flags": [f["message"] for f in legitimacy.flags]}
    if legitimacy.verdict == "suspicious" and legitimacy.has_strong_flag:
        reasons = ["Listing looks like a scam: " + "; ".join(f["message"] for f in legitimacy.flags if f["severity"] == "high")]
        return _filter(row, run_id, reasons, "blocked", signals, eligibility, legitimacy)

    method, email = detect_apply_method(row["apply_url"], row["apply_email"], job["description"] or "", cfg)
    prior = find_prior_application(
        None, company_norm=row["company_norm"], title_norm=row["title_norm"],
        window_days=int(cfg.get("applications.duplicate_window_days", 365)),
        threshold=float(cfg.get("applications.duplicate_title_similarity", 88)),
    )
    if prior:
        return _filter(row, run_id, [f"Already applied to this company for a similar role (application #{prior['id']})"],
                       "blocked", signals, eligibility, legitimacy)
    signals["apply_method_rule"] = method
    signals["salary_npr_monthly"] = [row["salary_npr_monthly_min"], row["salary_npr_monthly_max"]]
    return RuleState(row, job, seniority, eligibility, legitimacy, method, email, signals)


def _enrich(row: dict[str, Any], ctx: TaskContext) -> str | None:
    try:
        if row["source"] == "smartrecruiters":
            return enrich_smartrecruiters(row.get("raw") or {}, ctx)
        if row["source"] == "hackernews" or host_matches(host_of(row["source_url"]), ctx.cfg.terms("http.no_fetch_domains")):
            return None
        return enrich_from_page(row["source_url"], ctx)
    except FetchError as exc:
        log.info("Enrichment skipped for %s: %s", row["source_url"], exc)
        return None


def _domain_age(job: dict[str, Any], ctx: TaskContext) -> int | None:
    skip = ctx.cfg.terms("legitimacy.known_ats_domains") + ctx.cfg.terms("http.no_fetch_domains") + _BOARD_HOSTS
    for url in (job.get("company_website"), job.get("apply_url")):
        host = host_of(url)
        if host and not host_matches(host, skip) and job.get("source") not in CURATED_SOURCES - {"hackernews"}:
            return legitimacy_checks.domain_age_days(registrable_domain(host), ctx.client.raw)
    return None


def _filter(
    row: dict[str, Any],
    run_id: int,
    reasons: list[str],
    recommendation: str | None,
    signals: dict[str, Any],
    eligibility: EligibilityResult | None = None,
    legitimacy: LegitimacyResult | None = None,
) -> dict[str, Any]:
    db.execute(
        """
        UPDATE opportunities SET pipeline_status = 'filtered_out', filter_reasons = %s, recommendation = %s,
            recommendation_reasons = %s, rule_signals = %s,
            nepal_eligibility = COALESCE(%s, nepal_eligibility), nepal_evidence = COALESCE(%s, nepal_evidence),
            legitimacy_score = COALESCE(%s, legitimacy_score), legitimacy_verdict = COALESCE(%s, legitimacy_verdict),
            legitimacy_flags = COALESCE(%s, legitimacy_flags), review_status = 'none',
            last_processed_run_id = %s, updated_at = now()
        WHERE id = %s
        """,
        (
            db.jsonb(reasons), recommendation, db.jsonb(reasons if recommendation else []), db.jsonb(signals),
            eligibility.status if eligibility else None, eligibility.evidence if eligibility else None,
            legitimacy.score if legitimacy else None, legitimacy.verdict if legitimacy else None,
            db.jsonb(legitimacy.flags) if legitimacy else None, run_id, row["id"],
        ),
    )
    return {"id": row["id"], "title": row["title"], "outcome": "blocked" if recommendation else "filtered_out", "reasons": reasons}


def _await_ai(state: RuleState, run_id: int, reason: str) -> None:
    db.execute(
        """
        UPDATE opportunities SET pipeline_status = 'awaiting_ai', rule_signals = %s, nepal_eligibility = %s, nepal_evidence = %s,
            legitimacy_score = %s, legitimacy_verdict = %s, legitimacy_flags = %s, apply_method = %s, apply_email = %s,
            filter_reasons = %s, last_processed_run_id = %s, updated_at = now()
        WHERE id = %s
        """,
        (
            db.jsonb(state.signals), state.eligibility.status, state.eligibility.evidence, state.legitimacy.score,
            state.legitimacy.verdict, db.jsonb(state.legitimacy.flags), state.apply_method, state.apply_email,
            db.jsonb([reason]), run_id, state.row["id"],
        ),
    )


def _mark_failed(row: dict[str, Any], run_id: int, error: str, increment: bool) -> None:
    db.execute(
        "UPDATE opportunities SET pipeline_status = 'analysis_failed', filter_reasons = %s,"
        " analysis_attempts = analysis_attempts + %s, last_processed_run_id = %s, updated_at = now() WHERE id = %s",
        (db.jsonb([error[:500]]), 1 if increment else 0, run_id, row["id"]),
    )
    log_event("analysis_failed", f"{row['title']}: {error}", level="warn", run_id=run_id, opportunity_id=row["id"])


# ---------------------------------------------------------------------------
# AI stage
# ---------------------------------------------------------------------------
def _analyze(state: RuleState, ai: ClaudeService, cfg: Config, env: Env, profile: CandidateProfile, run_id: int) -> dict[str, Any]:
    row = state.row
    try:
        analysis = ai.analyze_job(state.job, state.signals, run_id)
        return _apply_analysis(state, analysis, cfg, env, profile, run_id, ai.model)
    except (BudgetExceeded, AIUnavailable) as exc:
        _await_ai(state, run_id, str(exc))
        return {"id": row["id"], "title": row["title"], "outcome": "awaiting_ai", "note": str(exc)}
    except AIError as exc:
        _mark_failed(row, run_id, str(exc), increment=True)
        return {"id": row["id"], "title": row["title"], "outcome": "analysis_failed", "error": str(exc)}
    except Exception as exc:
        log.exception("Analysis failed for opportunity %s", row["id"])
        _mark_failed(row, run_id, f"{type(exc).__name__}: {exc}", increment=True)
        return {"id": row["id"], "title": row["title"], "outcome": "analysis_failed", "error": str(exc)}


def _apply_analysis(
    state: RuleState, analysis: JobAnalysis, cfg: Config, env: Env, profile: CandidateProfile, run_id: int, model: str,
) -> dict[str, Any]:
    row, job = state.row, state.job
    eligibility, evidence = combine_eligibility(state.eligibility, analysis.nepal_eligibility, analysis.nepal_eligibility_evidence)
    legitimacy = combine_legitimacy(state.legitimacy, analysis.legitimacy_verdict, analysis.legitimacy_concerns)
    remote_type = row["remote_type"] if row["remote_type"] != "unknown" else analysis.remote_type
    employment_type = row["employment_type"] if row["employment_type"] != "unspecified" else analysis.employment_type
    location = ascii_lower(row["location_text"])
    onsite_in_nepal = state.eligibility.onsite_in_nepal or (
        remote_type in ("onsite", "hybrid") and any(contains_term(location, place) for place in _NEPAL_PLACES)
    )

    apply_method, apply_email = state.apply_method, state.apply_email
    if apply_method == "unclear" and analysis.application_method != "unclear":
        if analysis.application_method == "email":
            # Only trust an email address that literally appears in the posting.
            if analysis.application_email and analysis.application_email.lower() in (job["description"] or "").lower():
                apply_method, apply_email = "email", analysis.application_email
        else:
            apply_method = analysis.application_method

    seniority_reasons = list(state.seniority.reasons)
    max_years = int(cfg.get("seniority.max_required_years", 2))
    if analysis.required_years_experience is not None and analysis.required_years_experience > max_years and not seniority_reasons:
        seniority_reasons.append(f"Requires {analysis.required_years_experience}+ years of experience (limit {max_years})")

    comp_score, comp_reason = compensation_points(
        row["salary_npr_monthly_min"], row["salary_npr_monthly_max"], employment_type,
        float(profile.pref("compensation.min_monthly_npr", 20000)),
        list(profile.pref("compensation.allow_below_minimum_for", []) or []),
    )
    components: dict[str, Any] = {
        name: getattr(analysis, name).model_dump() for name in ("skills_match", "experience_fit", "role_fit", "growth_value")
    }
    components["eligibility"] = {"score": ELIGIBILITY_POINTS.get(eligibility, 50), "reasoning": evidence}
    components["compensation"] = {"score": comp_score, "reasoning": comp_reason}
    score, breakdown = compute_match_score(components, cfg.get("scoring.weights"))

    blockers = blocking_reasons(
        is_real_posting=analysis.is_real_job_posting, seniority_reasons=seniority_reasons,
        ai_seniority_mismatch=analysis.seniority_mismatch, eligibility=eligibility, eligibility_evidence=evidence,
        legitimacy_verdict=legitimacy.verdict, prior_application=None, remote_type=remote_type, onsite_in_nepal=onsite_in_nepal,
    )
    decision = decide(
        score=score, blockers=blockers, eligibility=eligibility, legitimacy_verdict=legitimacy.verdict,
        remote_type=remote_type, apply_method=apply_method, apply_email=apply_email,
        auto_apply_min=int(cfg.get("thresholds.auto_apply_min", 85)), approval_min=int(cfg.get("thresholds.approval_min", 70)),
        auto_apply_enabled=bool(cfg.get("applications.auto_apply_enabled")) and env.auto_apply_enabled,
    )
    signals = dict(state.signals, auto_apply_blockers=decision.auto_apply_blockers)
    db.execute(
        """
        UPDATE opportunities SET
            pipeline_status = 'analyzed', analysis = %(analysis)s, analysis_model = %(model)s, analyzed_at = now(),
            analysis_attempts = analysis_attempts + 1, match_score = %(score)s, score_breakdown = %(breakdown)s,
            recommendation = %(rec)s, recommendation_reasons = %(reasons)s,
            review_status = CASE WHEN review_status IN ('none', 'pending_approval', 'ready_to_apply', 'auto_apply_disabled')
                                 THEN %(review)s ELSE review_status END,
            nepal_eligibility = %(elig)s, nepal_evidence = %(evidence)s,
            legitimacy_score = %(lscore)s, legitimacy_verdict = %(lverdict)s, legitimacy_flags = %(lflags)s,
            remote_type = %(remote)s, employment_type = %(employment)s, apply_method = %(method)s, apply_email = %(email)s,
            rule_signals = %(signals)s, filter_reasons = '[]'::jsonb, last_processed_run_id = %(run)s, updated_at = now()
        WHERE id = %(id)s
        """,
        {
            "analysis": db.jsonb(analysis.model_dump()), "model": model, "score": score, "breakdown": db.jsonb(breakdown),
            "rec": decision.recommendation, "reasons": db.jsonb(decision.reasons), "review": decision.review_status,
            "elig": eligibility, "evidence": evidence, "lscore": legitimacy.score, "lverdict": legitimacy.verdict,
            "lflags": db.jsonb(legitimacy.flags), "remote": remote_type, "employment": employment_type,
            "method": apply_method, "email": apply_email, "signals": db.jsonb(signals), "run": run_id, "id": row["id"],
        },
    )
    log_event(
        "opportunity_analyzed",
        f"{row['title']} @ {row['company_name'] or 'unknown company'}: score {score} -> {decision.recommendation}",
        run_id=run_id, opportunity_id=row["id"],
        data={"score": score, "recommendation": decision.recommendation, "reasons": decision.reasons},
    )
    return {"id": row["id"], "title": row["title"], "outcome": decision.recommendation, "score": score}


def _materials_batch(run_id: int, ai: ClaudeService, cfg: Config, profile: CandidateProfile, deadline: float) -> list[dict[str, Any]]:
    if not cfg.get("materials.enabled", True):
        return []
    cap = int(cfg.get("materials.max_per_run", 10))
    results: list[dict[str, Any]] = []
    while _ai_count(run_id, "materials", distinct=True) < cap and time.monotonic() < deadline:
        row = db.fetch_one(
            """
            UPDATE opportunities SET materials_status = 'generating', updated_at = now()
            WHERE id = (
                SELECT id FROM opportunities
                WHERE pipeline_status = 'analyzed' AND materials_status = 'none' AND recommendation = ANY(%s)
                  AND match_score >= %s AND review_status NOT IN ('rejected', 'dismissed', 'applied')
                ORDER BY match_score DESC LIMIT 1 FOR UPDATE SKIP LOCKED
            )
            RETURNING id
            """,
            (list(cfg.get("materials.generate_for") or ACTIONABLE), int(cfg.get("materials.min_score", 70))),
        )
        if row is None:
            break
        result = materials_service.generate_for_opportunity(row["id"], run_id, ai, cfg, profile)
        results.append(result)
        if result.get("outcome") == "materials_deferred":
            break
    return results
