"""Application decision policy: thresholds + the non-negotiable "never" rules.

85-100  -> auto_apply_candidate (only if every automatic-application precondition holds)
70-84   -> approval_required
< 70    -> ignore
Any never-rule -> blocked, regardless of score.
"""
from __future__ import annotations

from dataclasses import dataclass, field

MANUAL_METHODS = {"ats_form", "external_form", "login_required"}
MANUAL_REASON = "Application requires manual interaction (web form or login)"


@dataclass
class Decision:
    recommendation: str  # auto_apply_candidate | approval_required | manual_apply | ignore | blocked
    review_status: str   # none | pending_approval | ready_to_apply | auto_apply_disabled
    reasons: list[str] = field(default_factory=list)
    auto_apply_blockers: list[str] = field(default_factory=list)


def blocking_reasons(
    *,
    is_real_posting: bool,
    seniority_reasons: list[str],
    ai_seniority_mismatch: bool,
    eligibility: str,
    eligibility_evidence: str,
    legitimacy_verdict: str,
    prior_application: dict | None,
    remote_type: str,
    onsite_in_nepal: bool,
) -> list[str]:
    reasons: list[str] = []
    if not is_real_posting:
        reasons.append("Not a real, open job posting")
    reasons.extend(seniority_reasons)
    if ai_seniority_mismatch and not seniority_reasons:
        reasons.append("Requires significantly more experience than the candidate has")
    if eligibility == "not_eligible":
        reasons.append(f"Not open to Nepal-based candidates: {eligibility_evidence}")
    if legitimacy_verdict == "suspicious":
        reasons.append("Listing looks suspicious or scam-like")
    if prior_application:
        reasons.append(f"Already applied to this company for a similar role (application #{prior_application['id']})")
    if remote_type in ("onsite", "hybrid") and not onsite_in_nepal:
        reasons.append("Requires working onsite outside Nepal")
    return reasons


def decide(
    *,
    score: int,
    blockers: list[str],
    eligibility: str,
    legitimacy_verdict: str,
    remote_type: str,
    apply_method: str,
    apply_email: str | None,
    auto_apply_min: int,
    approval_min: int,
    auto_apply_enabled: bool,
) -> Decision:
    if blockers:
        return Decision("blocked", "none", list(blockers), list(blockers))
    if score < approval_min:
        return Decision("ignore", "none", [f"Match score {score} is below {approval_min}"], ["Score below approval threshold"])

    auto_blockers: list[str] = []
    if eligibility not in ("eligible", "likely_eligible"):
        auto_blockers.append("Nepal eligibility is not confirmed")
    if legitimacy_verdict != "legitimate":
        auto_blockers.append("Company legitimacy is not confirmed")
    if remote_type != "remote":
        auto_blockers.append("Not a fully remote role")
    if apply_method == "unclear":
        auto_blockers.append("Application method is unclear")
    elif apply_method in MANUAL_METHODS:
        auto_blockers.append(MANUAL_REASON)
    elif apply_method == "email" and not apply_email:
        auto_blockers.append("No application email address found")

    if score >= auto_apply_min:
        if not auto_blockers:
            review = "auto_apply_disabled" if not auto_apply_enabled else "pending_approval"
            reasons = [f"Match score {score} ≥ {auto_apply_min} and all automatic-application checks pass"]
            if not auto_apply_enabled:
                reasons.append("Automatic applications are disabled - review and apply yourself")
            return Decision("auto_apply_candidate", review, reasons, [])
        if auto_blockers == [MANUAL_REASON]:
            return Decision(
                "manual_apply",
                "ready_to_apply",
                [f"Match score {score} ≥ {auto_apply_min}", MANUAL_REASON],
                auto_blockers,
            )
        return Decision(
            "approval_required",
            "pending_approval",
            [f"Match score {score} ≥ {auto_apply_min} but: " + "; ".join(auto_blockers)],
            auto_blockers,
        )
    return Decision(
        "approval_required",
        "pending_approval",
        [f"Match score {score} is between {approval_min} and {auto_apply_min - 1}"] + auto_blockers,
        auto_blockers or ["Score below automatic-application threshold"],
    )
