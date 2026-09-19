"""Generic job pages found by search engines / AI discovery.

Extraction order: schema.org JobPosting JSON-LD (used by most career sites and ATSs)
-> Claude extraction from visible page text (budget-capped) -> skip.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ..models import DiscoveredLink, NormalizedJob, TaskResult
from ..pipeline.normalize import detect_remote_type, html_to_text, map_employment_type, parse_datetime
from .ats import detect_ats_board
from .base import TaskContext, finalize

_JOB_INDICATORS = [
    "responsibilities", "requirements", "qualifications", "about the role", "what you'll do", "what you will do",
    "job description", "experience with", "apply now", "how to apply", "we are looking for", "you will", "benefits",
]
_UNIT = {"HOUR": "hour", "DAY": "day", "WEEK": "week", "MONTH": "month", "YEAR": "year"}


# ---------------------------------------------------------------------------
# JSON-LD
# ---------------------------------------------------------------------------
def extract_jsonld_postings(html_text: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html_text, "lxml")
    postings: list[dict[str, Any]] = []
    for tag in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = (tag.string or tag.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw, strict=False)
        except json.JSONDecodeError:
            continue
        stack: list[Any] = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                types = node.get("@type")
                types = types if isinstance(types, list) else [types]
                if "JobPosting" in types:
                    postings.append(node)
                if "@graph" in node:
                    stack.append(node["@graph"])
    return postings


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else value


def _place_name(place: Any) -> str:
    if isinstance(place, str):
        return place
    if not isinstance(place, dict):
        return ""
    if place.get("name") and not place.get("address"):
        return str(place["name"])
    address = place.get("address") or place
    if isinstance(address, str):
        return address
    country = address.get("addressCountry")
    country = country.get("name") if isinstance(country, dict) else country
    return ", ".join(str(x) for x in (address.get("addressLocality"), address.get("addressRegion"), country) if x)


def jsonld_to_job(posting: dict[str, Any], page_url: str, source: str = "web") -> NormalizedJob:
    org = posting.get("hiringOrganization") or {}
    org = org if isinstance(org, dict) else {"name": str(org)}
    website = _first(org.get("sameAs")) or org.get("url")
    locations = posting.get("jobLocation") or []
    locations = locations if isinstance(locations, list) else [locations]
    location_text = "; ".join(filter(None, (_place_name(loc) for loc in locations)))
    requirements = posting.get("applicantLocationRequirements") or []
    requirements = requirements if isinstance(requirements, list) else [requirements]
    restrictions = [name for name in (_place_name(r) for r in requirements) if name]

    title = html_to_text(posting.get("title") or "")
    description = html_to_text(posting.get("description") or "")
    if "TELECOMMUTE" in str(posting.get("jobLocationType", "")).upper():
        remote_type = "remote"
    else:
        remote_type = detect_remote_type(title, location_text)
        if remote_type == "unknown":
            remote_type = detect_remote_type(description[:2500])
        if remote_type == "unknown" and location_text:
            remote_type = "onsite"

    salary = posting.get("baseSalary") or {}
    value = salary.get("value") if isinstance(salary, dict) else None
    value = value if isinstance(value, dict) else {"value": value} if value else {}
    minimum = value.get("minValue") or value.get("value")
    maximum = value.get("maxValue")

    return finalize(NormalizedJob(
        source=source,
        source_url=posting.get("url") or page_url,
        title=title,
        company_name=org.get("name"),
        company_website=website if isinstance(website, str) else None,
        location_text=location_text or ("Remote" if remote_type == "remote" else None),
        location_restrictions=restrictions,
        remote_type=remote_type,
        employment_type=map_employment_type(posting.get("employmentType")),
        salary_min=_to_float(minimum),
        salary_max=_to_float(maximum),
        salary_currency=salary.get("currency") if isinstance(salary, dict) else None,
        salary_period=_UNIT.get(str(value.get("unitText", "")).upper()),
        description=description,
        apply_url=posting.get("url") or page_url,
        posted_at=parse_datetime(posting.get("datePosted")),
        expires_at=parse_datetime(posting.get("validThrough")),
        raw={"jsonld": True, "direct_apply": posting.get("directApply")},
    ))


def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Visible text
# ---------------------------------------------------------------------------
def main_text(html_text: str, limit: int = 15_000) -> str:
    soup = BeautifulSoup(html_text, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form", "iframe"]):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    return html_to_text(str(root))[:limit]


def looks_like_job_posting(text: str) -> bool:
    lower = text.lower()
    hits = sum(1 for word in _JOB_INDICATORS if word in lower)
    return hits >= 2 and 500 <= len(text) <= 60_000


def ats_links(html_text: str, limit: int = 20) -> list[DiscoveredLink]:
    soup = BeautifulSoup(html_text, "lxml")
    links: list[DiscoveredLink] = []
    seen: set[tuple[str, str]] = set()
    for anchor in soup.find_all("a", href=True):
        board = detect_ats_board(anchor["href"])
        if board and board not in seen:
            seen.add(board)
            links.append(DiscoveredLink(url=anchor["href"], via="page"))
            if len(links) >= limit:
                break
    return links


def fetch_page(task: dict[str, Any], ctx: TaskContext) -> TaskResult:
    url = task["url"]
    # Pages queued from a named job board keep that board's name (see services.ingest.store_link).
    source = task.get("source") or "web"
    
    domain = urlsplit(url).hostname or ""
    jina_domains = ctx.cfg.terms("http.jina_domains")
    
    if any(d in domain for d in jina_domains):
        jina_url = f"https://r.jina.ai/{url}"
        response = ctx.client.get(jina_url, check_robots=False, accept="text/plain")
        if response.status >= 400:
            return TaskResult(status="failed", http_status=response.status, note="Jina Reader failed")
            
        text = response.text
        if not looks_like_job_posting(text):
            return TaskResult(status="skipped", http_status=response.status, note="Jina output does not look like a single job posting")
        if ctx.extractor is None:
            return TaskResult(status="skipped", http_status=response.status, note="No structured data; AI extraction unavailable")
        job = ctx.extractor(url, text)
        if job is None:
            return TaskResult(status="skipped", http_status=response.status, note="AI extraction: not a job posting")
        job.source = source
        return TaskResult(http_status=response.status, jobs=[job], note="AI-extracted via Jina Reader")

    response = ctx.client.get(
        url,
        check_robots=bool(ctx.cfg.get("http.respect_robots_txt_for_pages", True)),
        accept="text/html,application/xhtml+xml",
    )
    if "html" not in response.content_type.lower():
        return TaskResult(status="skipped", http_status=response.status, note=f"Not an HTML page ({response.content_type})")

    links = ats_links(response.text)
    postings = extract_jsonld_postings(response.text)
    if postings:
        jobs = [jsonld_to_job(p, response.url, source=source) for p in postings[:5]]
        return TaskResult(http_status=response.status, jobs=[j for j in jobs if j.title], links=links, note="JSON-LD JobPosting")

    text = main_text(response.text)
    if not looks_like_job_posting(text):
        return TaskResult(status="skipped", http_status=response.status, links=links, note="Page does not look like a single job posting")
    if ctx.extractor is None:
        return TaskResult(status="skipped", http_status=response.status, links=links, note="No structured data; AI extraction unavailable or capped")
    job = ctx.extractor(response.url, text)
    if job is None:
        return TaskResult(status="skipped", http_status=response.status, links=links, note="AI extraction: not a job posting")
    job.source = source
    return TaskResult(http_status=response.status, jobs=[job], links=links, note="AI-extracted")


def enrich_from_page(url: str, ctx: TaskContext) -> str | None:
    """Fetch a posting page to get a fuller description (JSON-LD first, then visible text)."""
    domain = urlsplit(url).hostname or ""
    jina_domains = ctx.cfg.terms("http.jina_domains")
    
    if any(d in domain for d in jina_domains):
        jina_url = f"https://r.jina.ai/{url}"
        response = ctx.client.get(jina_url, check_robots=False, accept="text/plain")
        if response.status >= 400:
            return None
        text = response.text
        return text if looks_like_job_posting(text) else None

    response = ctx.client.get(url, check_robots=True, accept="text/html,application/xhtml+xml")
    if "html" not in response.content_type.lower():
        return None
    postings = extract_jsonld_postings(response.text)
    if postings:
        return html_to_text(postings[0].get("description") or "") or None
    text = main_text(response.text)
    return text if looks_like_job_posting(text) else None
