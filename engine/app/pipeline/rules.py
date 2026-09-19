"""Deterministic filters: relevance, seniority, Nepal eligibility, application method, freshness.

These run before any AI call. They are deliberately conservative about *blocking*:
only explicit signals block, everything ambiguous goes to Claude and then to you.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from ..config import Config
from .normalize import EMAIL_RE, ascii_lower, contains_term, first_term, host_matches, host_of

# ---------------------------------------------------------------------------
# Relevance
# ---------------------------------------------------------------------------
_GENERIC_TECH_TITLE = re.compile(r"\b(developer|engineer|programmer|intern|trainee|graduate)\b")
_CANDIDATE_STACK = re.compile(r"\b(react|typescript|javascript|frontend|front-end|next\.?js)\b")


NEPAL_PLACES = ("nepal", "kathmandu", "lalitpur", "bhaktapur", "pokhara", "biratnagar", "butwal", "chitwan")


def is_nepal_local(location_text: str | None, source: str | None, cfg: Config) -> bool:
    """True when the posting is clearly aimed at the Nepali market.

    Used to relax the technology-stack exclusions. Worldwide-remote listings are filtered hard
    because there are thousands of them, but the Kathmandu market is small and runs on stacks
    (PHP/Laravel, .NET, Java, WordPress) that are worth a look even though the CV is React-first.
    """
    if source and source in set(cfg.terms("local_boards.sources") or []):
        return True
    location = ascii_lower(location_text)
    return any(contains_term(location, place) for place in NEPAL_PLACES)


def assess_relevance(title: str, description: str, cfg: Config, *, nepal_local: bool = False) -> tuple[bool, str]:
    t = ascii_lower(title)
    excluded = first_term(t, cfg.terms("relevance.exclude_title_terms"))
    if excluded and not (nepal_local and excluded in cfg.terms("relevance.nepal_relaxed_exclude_terms")):
        return False, f"Title matches excluded term '{excluded}'"
    include_terms = cfg.terms("relevance.include_title_terms")
    if nepal_local:
        include_terms = include_terms + cfg.terms("relevance.nepal_extra_include_terms")
    included = first_term(t, include_terms)
    if included:
        return True, f"Title matches target term '{included}'"
    if _GENERIC_TECH_TITLE.search(t) and _CANDIDATE_STACK.search(ascii_lower(description)):
        return True, "Generic technical title and posting mentions the candidate's stack"
    return False, "Title is not related to the target roles"

# ---------------------------------------------------------------------------
# Seniority & years of experience
# ---------------------------------------------------------------------------
_YEARS_RE = re.compile(
    r"(?<![\d.])(?P<min>\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*(?P<max>\d{1,2})\s*\+?\s*)?(?:years?|yrs?)\b(?![- ]old)",
    re.IGNORECASE,
)
_EXPERIENCE_WORDS = re.compile(r"\b(experience|experienced|professional|working|commercial|industry|hands[- ]on|background|track record)\b")
_OPTIONAL_WORDS = re.compile(r"\b(preferred|nice to have|nice-to-have|bonus|ideally|a plus|advantage|advantageous|desirable)\b")
_JUNIOR_TITLE = re.compile(r"\b(junior|jr|entry[- ]level|intern|internship|trainee|graduate|apprentice|associate)\b")


def required_years(description: str) -> int | None:
    """Largest minimum-years requirement stated as mandatory, ignoring 'preferred' mentions."""
    best: int | None = None
    for sentence in re.split(r"[.\n;•]|\s-\s", ascii_lower(description)):
        if not _EXPERIENCE_WORDS.search(sentence) or _OPTIONAL_WORDS.search(sentence):
            continue
        for match in _YEARS_RE.finditer(sentence):
            years = int(match.group("min"))
            if 0 <= years <= 20:
                best = years if best is None else max(best, years)
    return best


@dataclass
class SeniorityResult:
    blocked: bool
    reasons: list[str] = field(default_factory=list)
    required_years: int | None = None
    junior_title: bool = False


def assess_seniority(title: str, description: str, cfg: Config) -> SeniorityResult:
    t = ascii_lower(title)
    reasons: list[str] = []
    blocked_term = first_term(t, cfg.terms("seniority.blocked_title_terms"))
    junior = bool(_JUNIOR_TITLE.search(t))
    if blocked_term and not junior:
        reasons.append(f"Senior-level title ('{blocked_term}')")
    years = required_years(description)
    max_years = int(cfg.get("seniority.max_required_years", 2))
    if years is not None and years > max_years:
        reasons.append(f"Requires {years}+ years of experience (limit {max_years})")
    return SeniorityResult(blocked=bool(reasons), reasons=reasons, required_years=years, junior_title=junior)


# ---------------------------------------------------------------------------
# Nepal eligibility
# ---------------------------------------------------------------------------
_REGION = (
    r"(?:the\s)?(us|u\.s\.?|usa|united states|canada|uk|united kingdom|eu|european union|europe|emea|latam|"
    r"latin america|north america|india|germany|australia|brazil|mexico|philippines)"
)
_REGION_NO_US = (
    r"(?:the\s)?(u\.s\.?|usa|united states|canada|uk|united kingdom|eu|european union|europe|emea|latam|"
    r"latin america|north america|india|germany|australia|brazil|mexico|philippines)"
)
# "US only" is matched case-sensitively on the original text so "contact us only" is not a restriction.
_US_ONLY = re.compile(r"\b(?:US|USA|U\.S\.)[\s-]*(?:only|Only|ONLY|based only|residents only)\b")
_RESTRICTION_PATTERNS = [
    re.compile(rf"\b{_REGION_NO_US}[\s-]*(?:only|based only|residents only)\b"),
    re.compile(rf"\bonly (?:open to |accepting |hiring |considering )?(?:candidates |applicants |people |residents )?(?:who are )?(?:based |located |living |residing )?in {_REGION}\b"),
    re.compile(rf"\bmust (?:be )?(?:currently )?(?:based|located|reside|residing|live|living) in {_REGION}\b"),
    re.compile(rf"\b(?:legally )?(?:authori[sz]ed|eligible) to work in {_REGION}\b"),
    re.compile(rf"\b(?:unable|not able) to (?:hire|employ|sponsor)[^.]{{0,30}}outside (?:of )?{_REGION}\b"),
    re.compile(rf"\bremote\s*[-–(,:]\s*{_REGION}\s*\)?\s*$"),
]
_NEPAL_EXCLUDED = [
    re.compile(r"\b(?:not|cannot|can't|unable to|don't|do not) (?:hire|accept|consider|employ|support)[^.]{0,60}\bnepal\b"),
    re.compile(r"\bnepal\b[^.]{0,40}\b(?:not eligible|excluded|not supported|not accepted|restricted)\b"),
    re.compile(r"\bexclud\w*[^.]{0,80}\bnepal\b"),
]


@dataclass
class EligibilityResult:
    status: str  # eligible | likely_eligible | unclear | not_eligible
    evidence: str
    explicit: bool = False
    onsite_in_nepal: bool = False


def _norm_region(value: str) -> str:
    return re.sub(r"\s+", " ", ascii_lower(value).strip(" .,-"))


def assess_nepal_eligibility(
    *,
    title: str,
    location_text: str | None,
    location_restrictions: list[str],
    remote_type: str,
    description: str,
    cfg: Config,
) -> EligibilityResult:
    positive = cfg.terms("eligibility.nepal_positive_terms")
    includes = cfg.terms("eligibility.region_includes_nepal")
    excludes = cfg.terms("eligibility.region_excludes_nepal")
    location = ascii_lower(location_text)
    desc = ascii_lower(description)
    in_nepal = contains_term(location, "nepal") or contains_term(location, "kathmandu") or contains_term(location, "lalitpur")

    # 1. Explicit exclusion of Nepal anywhere in the posting
    for pattern in _NEPAL_EXCLUDED:
        if (m := pattern.search(desc)) is not None:
            return EligibilityResult("not_eligible", f"Posting says: \"{m.group(0)}\"", explicit=True)

    # 2. Onsite / hybrid roles must be located in Nepal
    if remote_type in ("onsite", "hybrid"):
        if in_nepal:
            return EligibilityResult("eligible", f"{remote_type.title()} role located in Nepal ({location_text})", True, onsite_in_nepal=True)
        if location:
            return EligibilityResult("not_eligible", f"{remote_type.title()} role located outside Nepal ({location_text})", explicit=True)

    # 3. Structured location restrictions (Himalayas, JSON-LD applicantLocationRequirements, ...)
    regions = [_norm_region(r) for r in location_restrictions if r and r.strip()]
    if regions:
        if any(r == "np" or contains_term(r, "nepal") for r in regions):
            return EligibilityResult("eligible", "Location restrictions include Nepal", True)
        if any(first_term(r, positive) for r in regions):
            return EligibilityResult("eligible", f"Open location: {', '.join(location_restrictions)}", True)
        if any(first_term(r, includes) for r in regions):
            return EligibilityResult("likely_eligible", f"Region includes Nepal: {', '.join(location_restrictions)}")
        return EligibilityResult("not_eligible", f"Restricted to: {', '.join(location_restrictions)}", explicit=True)

    # 4. Location text
    if in_nepal:
        return EligibilityResult("eligible", f"Location mentions Nepal ({location_text})", True)
    if location:
        if first_term(location, positive):
            return EligibilityResult("eligible", f"Location: {location_text}", True)
        if first_term(location, includes):
            return EligibilityResult("likely_eligible", f"Location region includes Nepal: {location_text}")
        excluded_region = first_term(location, excludes)
        if excluded_region:
            return EligibilityResult("not_eligible", f"Location limited to {location_text}", explicit=True)

    # 5. Description text
    if contains_term(desc, "nepal"):
        return EligibilityResult("eligible", "Posting mentions Nepal", True)
    restriction = _US_ONLY.search(description) or next(
        (m for p in _RESTRICTION_PATTERNS if (m := p.search(desc)) is not None), None
    )
    open_term = first_term(desc, ["work from anywhere", "anywhere in the world", "worldwide", "fully distributed", "any country", "all countries"])
    if restriction and open_term:
        return EligibilityResult("unclear", f"Conflicting signals: \"{open_term}\" vs \"{restriction.group(0)}\"")
    if restriction:
        return EligibilityResult("not_eligible", f"Posting says: \"{restriction.group(0)}\"", explicit=True)
    if open_term:
        return EligibilityResult("eligible", f"Posting says: \"{open_term}\"")
    region_hit = first_term(desc, includes)
    if region_hit:
        return EligibilityResult("likely_eligible", f"Posting mentions {region_hit}")
    return EligibilityResult("unclear", "No location restriction information found")


_ELIGIBILITY_RANK = {"not_eligible": 0, "unclear": 1, "likely_eligible": 2, "eligible": 3}


def combine_eligibility(rule: EligibilityResult, ai_status: str, ai_evidence: str) -> tuple[str, str]:
    """Merge rule and AI verdicts. Any exclusion wins, unless the other side found explicit inclusion (then: unclear)."""
    r, a = rule.status, ai_status
    if r == "not_eligible" or a == "not_eligible":
        if r == a:
            return "not_eligible", f"{rule.evidence} | AI: {ai_evidence}"
        other = a if r == "not_eligible" else r
        if other == "eligible":
            return "unclear", f"Conflicting signals - rules: {rule.evidence} | AI ({a}): {ai_evidence}"
        return "not_eligible", rule.evidence if r == "not_eligible" else f"AI: {ai_evidence}"
    if r == "unclear":
        return a, ai_evidence if a != "unclear" else rule.evidence
    if a == "unclear":
        return r, rule.evidence
    # Both positive: keep the more cautious verdict.
    status = min(r, a, key=lambda s: _ELIGIBILITY_RANK.get(s, 1))
    return status, rule.evidence if status == r else ai_evidence


# ---------------------------------------------------------------------------
# Application method
# ---------------------------------------------------------------------------
_APPLY_CONTEXT = re.compile(r"\b(apply|application|send|email|e-mail|cv|resume|résumé|portfolio)\b")
_NON_APPLY_EMAIL = re.compile(r"^(no-?reply|privacy|legal|security|abuse|dpo|gdpr|accessibility)@", re.IGNORECASE)


def detect_apply_method(apply_url: str | None, apply_email: str | None, description: str, cfg: Config) -> tuple[str, str | None]:
    if apply_email:
        return "email", apply_email
    if apply_url and apply_url.lower().startswith("mailto:"):
        return "email", apply_url[7:].split("?")[0]
    if apply_url:
        host = host_of(apply_url)
        if host_matches(host, cfg.terms("legitimacy.known_ats_domains")):
            return "ats_form", None
        # Both lists are sites you have to open and apply on yourself: no_fetch_domains are never
        # touched, jina_domains are readable only through a reader proxy and still need a login.
        if host_matches(host, cfg.terms("http.no_fetch_domains") + cfg.terms("http.jina_domains")):
            return "login_required", None
        return "external_form", None
    for sentence in re.split(r"(?<=[.!?])\s+|\n", description):
        if _APPLY_CONTEXT.search(sentence.lower()):
            for email_addr in EMAIL_RE.findall(sentence):
                if not _NON_APPLY_EMAIL.match(email_addr):
                    return "email", email_addr
    return "unclear", None


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------
def staleness_reason(posted_at: datetime | None, expires_at: datetime | None, max_age_days: int, now: datetime | None = None) -> str | None:
    now = now or datetime.now(UTC)
    if expires_at and expires_at < now:
        return f"Posting expired on {expires_at.date().isoformat()}"
    if posted_at and posted_at < now - timedelta(days=max_age_days):
        return f"Posted {(now - posted_at).days} days ago (limit {max_age_days})"
    return None
