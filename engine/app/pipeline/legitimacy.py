"""Company / listing legitimacy checks: heuristics, domain age (RDAP) and AI verdict merge."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from ..config import Config
from .normalize import ascii_lower, contains_term, host_matches, host_of, parse_datetime, registrable_domain

log = logging.getLogger(__name__)

CURATED_SOURCES = {"remotive", "remoteok", "weworkremotely", "himalayas", "jobicy", "workingnomads", "hackernews", "arbeitnow"}
_VERDICT_RANK = {"legitimate": 0, "uncertain": 1, "suspicious": 2}


@dataclass
class LegitimacyResult:
    score: int
    verdict: str
    flags: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_strong_flag(self) -> bool:
        return any(f["severity"] == "high" for f in self.flags)


def verdict_for(score: int, strong_flag: bool) -> str:
    if strong_flag or score < 40:
        return "suspicious"
    if score < 65:
        return "uncertain"
    return "legitimate"


def heuristic_legitimacy(job: dict[str, Any], cfg: Config, domain_age_days: int | None = None) -> LegitimacyResult:
    score = 60
    flags: list[dict[str, Any]] = []

    def flag(code: str, severity: str, message: str, delta: int) -> None:
        nonlocal score
        score += delta
        flags.append({"code": code, "severity": severity, "message": message, "delta": delta})

    text = ascii_lower(f"{job.get('title', '')}\n{job.get('description', '')}")
    ats_domains = cfg.terms("legitimacy.known_ats_domains")
    source_host = host_of(job.get("source_url"))
    apply_host = host_of(job.get("apply_url"))
    company_domain = registrable_domain(job.get("company_website")) if job.get("company_website") else ""

    if host_matches(source_host, ats_domains) or host_matches(apply_host, ats_domains):
        flag("ats_hosted", "positive", "Posting is hosted on a known applicant tracking system", 20)
    if job.get("source") in CURATED_SOURCES:
        flag("curated_board", "positive", f"Listed on an established job board ({job.get('source')})", 5)
    if company_domain:
        flag("company_website", "positive", f"Company website present ({company_domain})", 5)
        if apply_host and registrable_domain(apply_host) == company_domain:
            flag("apply_on_company_domain", "positive", "Application link is on the company's own domain", 10)
    if not (job.get("company_name") or "").strip():
        flag("no_company", "medium", "No company name given", -15)

    for term in cfg.terms("legitimacy.strong_red_flags"):
        if contains_term(text, term):
            flag("payment_request", "high", f"Scam indicator: mentions '{term}'", -40)
    for term in cfg.terms("legitimacy.weak_red_flags"):
        if contains_term(text, term):
            flag("weak_red_flag", "medium", f"Warning sign: mentions '{term}'", -12)

    apply_email = (job.get("apply_email") or "").lower()
    if apply_email and apply_email.split("@")[-1] in cfg.terms("legitimacy.free_email_domains"):
        flag("free_email", "medium", f"Applications go to a free email address ({apply_email})", -10)

    if len(job.get("description") or "") < 250:
        flag("vague_description", "low", "Very short or vague description", -8)

    min_age = int(cfg.get("legitimacy.min_domain_age_days", 180))
    if domain_age_days is not None:
        if domain_age_days < 30:
            flag("new_domain", "high", f"Company domain registered only {domain_age_days} days ago", -30)
        elif domain_age_days < min_age:
            flag("young_domain", "medium", f"Company domain is {domain_age_days} days old", -15)

    monthly = job.get("salary_npr_monthly_max") or job.get("salary_npr_monthly_min")
    if monthly and monthly > 1_500_000 and contains_term(text, "no experience"):
        flag("unrealistic_pay", "medium", "Unusually high pay for a no-experience role", -15)

    score = max(0, min(100, score))
    return LegitimacyResult(score=score, verdict=verdict_for(score, any(f["severity"] == "high" for f in flags)), flags=flags)


def combine_legitimacy(heuristic: LegitimacyResult, ai_verdict: str, ai_concerns: list[str]) -> LegitimacyResult:
    ai_score = {"legitimate": 80, "uncertain": 50, "suspicious": 15}.get(ai_verdict, 50)
    score = round(0.6 * heuristic.score + 0.4 * ai_score)
    flags = list(heuristic.flags) + [
        {"code": "ai_concern", "severity": "medium", "message": concern, "delta": 0} for concern in ai_concerns
    ]
    verdict = verdict_for(score, heuristic.has_strong_flag)
    if ai_verdict == "suspicious":
        verdict = "suspicious"
    elif _VERDICT_RANK[heuristic.verdict] > _VERDICT_RANK[verdict]:
        verdict = heuristic.verdict
    return LegitimacyResult(score=score, verdict=verdict, flags=flags)


_domain_age_cache: dict[str, int | None] = {}


def domain_age_days(domain: str, client: httpx.Client) -> int | None:
    """Registration age via public RDAP. Returns None when unknown (never blocks on failure)."""
    domain = domain.lower().strip()
    if not domain or "." not in domain:
        return None
    if domain in _domain_age_cache:
        return _domain_age_cache[domain]
    age: int | None = None
    try:
        response = client.get(f"https://rdap.org/domain/{domain}", timeout=8, follow_redirects=True)
        if response.status_code == 200:
            for event in response.json().get("events", []):
                if event.get("eventAction") == "registration":
                    registered = parse_datetime(event.get("eventDate"))
                    if registered:
                        age = (datetime.now(UTC) - registered).days
                    break
    except (httpx.HTTPError, ValueError) as exc:
        log.debug("RDAP lookup failed for %s: %s", domain, exc)
    _domain_age_cache[domain] = age
    return age
