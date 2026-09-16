"""Applications.

PHASE 1 - discovery and filtering only. This module can record applications YOU made
manually and explain exactly why an opportunity may not be auto-applied. It contains no
code that submits anything to an employer.
"""
from __future__ import annotations

from typing import Any

import psycopg

from .. import db
from ..config import Config, Env
from ..events import log_event
from ..pipeline.dedup import find_prior_application


class AutoApplyDisabled(RuntimeError):
    pass


class DuplicateApplication(RuntimeError):
    pass


def auto_apply_blockers(opportunity: dict[str, Any], materials: dict[str, Any] | None, cfg: Config, env: Env) -> list[str]:
    """Every reason automatic submission would be refused. Empty list is never returned in phase 1."""
    blockers: list[str] = []
    if not cfg.get("applications.auto_apply_enabled"):
        blockers.append("applications.auto_apply_enabled is false in config/config.yaml")
    if not env.auto_apply_enabled:
        blockers.append("AUTO_APPLY_ENABLED is not true in .env")
    if opportunity.get("recommendation") != "auto_apply_candidate":
        blockers.append(f"Recommendation is '{opportunity.get('recommendation')}', not auto_apply_candidate")
    if (opportunity.get("match_score") or 0) < int(cfg.get("thresholds.auto_apply_min", 85)):
        blockers.append("Match score is below the automatic-application threshold")
    if opportunity.get("nepal_eligibility") not in ("eligible", "likely_eligible"):
        blockers.append("Nepal eligibility is not confirmed")
    if opportunity.get("legitimacy_verdict") != "legitimate":
        blockers.append("Company legitimacy is not confirmed")
    if opportunity.get("apply_method") != "email" or not opportunity.get("apply_email"):
        blockers.append("No clear email application method (web forms and logins always need you)")
    if not materials or not materials.get("verified"):
        blockers.append("No fact-checked tailored materials")
    if find_prior_application(
        None, company_norm=opportunity.get("company_norm") or "", title_norm=opportunity.get("title_norm") or "",
        window_days=int(cfg.get("applications.duplicate_window_days", 365)),
        threshold=float(cfg.get("applications.duplicate_title_similarity", 88)),
    ):
        blockers.append("An application to this company for a similar role already exists")
    blockers.append("Phase 1 build: no application submitter is installed")
    return blockers


def submit_application(opportunity_id: int) -> None:
    raise AutoApplyDisabled("Automatic application submission is not available in phase 1 (discovery and filtering only).")


def mark_applied(opportunity_id: int, notes: str | None, cfg: Config) -> dict[str, Any]:
    """Record an application the candidate submitted themselves, with duplicate protection."""
    with db.connection() as conn:
        opportunity = db.fetch_one(
            "SELECT id, title, company_name, company_norm, title_norm FROM opportunities WHERE id = %s FOR UPDATE",
            (opportunity_id,), conn,
        )
        if opportunity is None:
            raise LookupError(f"Opportunity {opportunity_id} not found")
        prior = find_prior_application(
            conn, company_norm=opportunity["company_norm"], title_norm=opportunity["title_norm"],
            window_days=int(cfg.get("applications.duplicate_window_days", 365)),
            threshold=float(cfg.get("applications.duplicate_title_similarity", 88)),
            exclude_opportunity_id=opportunity_id,
        )
        if prior:
            raise DuplicateApplication(
                f"Already applied to {opportunity['company_name'] or 'this company'} for a similar role "
                f"(application #{prior['id']} on {prior['applied_at']:%Y-%m-%d})"
            )
        materials = db.fetch_one(
            "SELECT id FROM application_materials WHERE opportunity_id = %s ORDER BY version DESC LIMIT 1", (opportunity_id,), conn,
        )
        try:
            with conn.transaction():
                application = db.fetch_one(
                    """
                    INSERT INTO applications (opportunity_id, company_norm, title_norm, method, submitted_by, materials_id, notes)
                    VALUES (%s, %s, %s, 'manual', 'user', %s, %s) RETURNING id, applied_at
                    """,
                    (opportunity_id, opportunity["company_norm"], opportunity["title_norm"], materials["id"] if materials else None, notes),
                    conn,
                )
        except psycopg.errors.UniqueViolation as exc:
            raise DuplicateApplication("An application for this company and role is already recorded") from exc
        db.execute("UPDATE opportunities SET review_status = 'applied', updated_at = now() WHERE id = %s", (opportunity_id,), conn)
        log_event("application_recorded", f"Applied manually: {opportunity['title']} @ {opportunity['company_name']}",
                  opportunity_id=opportunity_id, data={"application_id": application["id"]}, conn=conn)
    return application
