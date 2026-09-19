"""Cheap rule-based pre-score (0-100), computed at ingest with no AI call.

It exists for one reason: the AI analysis budget per run is small compared with the number of
postings discovered, so the queue must be ordered by *likely* fit rather than by arrival time.
A posting's pre-score decides who gets the scarce AI slots; the real match score still comes
from the AI analysis and is never replaced by this number.

Deliberately blunt: title signals dominate, everything else nudges. Nothing here blocks a
posting - blocking stays in rules.py / policy.py.
"""
from __future__ import annotations

import re
from typing import Any

from ..config import Config
from .normalize import ascii_lower, contains_term, first_term

#: Sources whose postings are structured, dated and rarely spam.
_TRUSTED_SOURCES = {
    "greenhouse", "lever", "ashby", "workable", "smartrecruiters", "recruitee",
    "remotive", "himalayas", "weworkremotely", "jobicy", "remoteok", "workingnomads",
}

_JUNIOR = re.compile(r"\b(junior|jr|entry[- ]level|intern|internship|trainee|graduate|apprentice|associate|fresher)\b")
_SENIORISH = re.compile(r"\b(senior|sr|staff|principal|lead|head|director|architect|manager|expert)\b")

#: Title terms that map closely onto the CV, in descending order of how well they fit.
_TITLE_TIERS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (30, ("frontend", "front end", "front-end", "react", "next.js", "nextjs", "ui developer", "ui engineer")),
    (24, ("web developer", "web engineer", "javascript", "typescript", "fullstack", "full stack", "full-stack")),
    (18, ("software engineer", "software developer", "programmer", "web design")),
    (12, ("qa", "quality assurance", "test automation", "sdet", "devops", "cloud", "platform engineer")),
    (8, ("technical support", "support engineer", "application support", "it support", "it officer", "it executive")),
)

#: Stack terms anywhere in the posting, worth a few points each (capped).
_STACK_TERMS = (
    "react", "javascript", "typescript", "html", "css", "tailwind", "next.js", "nextjs",
    "redux", "node", "rest api", "git", "github", "figma", "responsive",
)


def prescore(job: dict[str, Any], cfg: Config, *, eligibility_status: str | None = None) -> tuple[int, list[str]]:
    """Return (0-100, human-readable reasons). Pure function of the stored posting fields."""
    title = ascii_lower(job.get("title"))
    description = ascii_lower(job.get("description"))
    haystack = f"{title}\n{description[:6000]}"
    score = 40
    reasons: list[str] = []

    for points, terms in _TITLE_TIERS:
        hit = first_term(title, list(terms))
        if hit:
            score += points
            reasons.append(f"Title matches '{hit}' (+{points})")
            break
    else:
        score -= 10
        reasons.append("Title has no direct role match (-10)")

    if _JUNIOR.search(title):
        score += 15
        reasons.append("Junior / entry-level title (+15)")
    elif _SENIORISH.search(title):
        score -= 25
        reasons.append("Senior-sounding title (-25)")

    stack_hits = [t for t in _STACK_TERMS if contains_term(haystack, t)]
    if stack_hits:
        points = min(15, 3 * len(stack_hits))
        score += points
        reasons.append(f"Mentions {', '.join(stack_hits[:5])} (+{points})")

    if eligibility_status:
        points = {"eligible": 12, "likely_eligible": 6, "unclear": 0, "not_eligible": -30}.get(eligibility_status, 0)
        if points:
            score += points
            reasons.append(f"Nepal eligibility '{eligibility_status}' ({points:+d})")

    if job.get("remote_type") == "remote":
        score += 5
        reasons.append("Remote role (+5)")

    location = ascii_lower(job.get("location_text"))
    if any(contains_term(location, place) for place in ("nepal", "kathmandu", "lalitpur", "bhaktapur", "pokhara")):
        score += 10
        reasons.append("Located in Nepal (+10)")

    if job.get("salary_npr_monthly_min") or job.get("salary_npr_monthly_max"):
        score += 4
        reasons.append("Pay is stated (+4)")

    if job.get("source") in _TRUSTED_SOURCES:
        score += 4
        reasons.append(f"Structured source '{job.get('source')}' (+4)")

    length = len(description)
    if length < int(cfg.get("pipeline.min_description_chars", 200)):
        score -= 8
        reasons.append("Very short description (-8)")

    return max(0, min(100, score)), reasons
