"""Applicant tracking system public job-board APIs.

These endpoints are published by the ATS vendors specifically so that job boards and
career sites can list a company's open positions. Boards are discovered automatically
from any URL seen anywhere (feeds, search results, AI discovery, pages, HN comments).
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ..models import NormalizedJob, TaskResult
from ..pipeline.normalize import detect_remote_type, html_to_text, map_employment_type, parse_datetime
from .base import FetchError, TaskContext, finalize

_RESERVED = {"embed", "api", "v1", "jobs", "j", "careers", "static", "assets", "www", "app", "login", "search", "job", "apply"}

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("greenhouse", re.compile(r"^(?:job-)?boards(?:\.eu)?\.greenhouse\.io$")),
    ("lever", re.compile(r"^jobs(?:\.eu)?\.lever\.co$")),
    ("ashby", re.compile(r"^jobs\.ashbyhq\.com$")),
    ("smartrecruiters", re.compile(r"^(?:jobs|careers)\.smartrecruiters\.com$")),
    ("workable", re.compile(r"^apply\.workable\.com$")),
]
_RECRUITEE = re.compile(r"^([a-z0-9-]+)\.recruitee\.com$")


def detect_ats_board(url: str | None) -> tuple[str, str] | None:
    """Return (provider, slug) if the URL belongs to a supported ATS job board."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    segments = [s for s in parts.path.split("/") if s]
    if match := _RECRUITEE.match(host):
        slug = match.group(1)
        return ("recruitee", slug) if slug not in _RESERVED else None
    for provider, pattern in _PATTERNS:
        if not pattern.match(host):
            continue
        if provider == "greenhouse" and segments[:2] == ["embed", "job_board"]:
            slug = (parse_qs(parts.query).get("for") or [""])[0]
        else:
            slug = segments[0] if segments else ""
        slug = slug.strip()
        if not slug or slug.lower() in _RESERVED or not re.fullmatch(r"[A-Za-z0-9_.-]{2,80}", slug):
            return None
        return provider, slug
    return None


def _company(task: dict[str, Any]) -> str:
    return task.get("params", {}).get("company_name") or task["params"]["slug"].replace("-", " ").title()


def fetch_greenhouse(task: dict[str, Any], ctx: TaskContext) -> TaskResult:
    slug = task["params"]["slug"]
    data = ctx.client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"}).json()
    jobs = []
    for j in data.get("jobs", []):
        location = (j.get("location") or {}).get("name")
        description = html_to_text(j.get("content"))
        jobs.append(finalize(NormalizedJob(
            source="greenhouse",
            source_external_id=f"{slug}:{j.get('id')}",
            source_url=j["absolute_url"],
            title=j.get("title", ""),
            company_name=j.get("company_name") or _company(task),
            location_text=location,
            remote_type=_remote(j.get("title"), location, description),
            description=description,
            apply_url=j["absolute_url"],
            posted_at=parse_datetime(j.get("first_published") or j.get("updated_at")),
        )))
    return TaskResult(jobs=jobs)


def fetch_lever(task: dict[str, Any], ctx: TaskContext) -> TaskResult:
    slug = task["params"]["slug"]
    data: Any = None
    for base in ("https://api.lever.co", "https://api.eu.lever.co"):
        try:
            data = ctx.client.get(f"{base}/v0/postings/{slug}", params={"mode": "json"}).json()
            break
        except FetchError as exc:
            if exc.status == 404 and base == "https://api.lever.co":
                continue
            raise
    jobs = []
    for p in data or []:
        categories = p.get("categories") or {}
        location = categories.get("location") or ", ".join(categories.get("allLocations") or [])
        description = p.get("descriptionPlain") or html_to_text(p.get("description"))
        for block in p.get("lists") or []:
            description += f"\n\n{block.get('text', '')}\n{html_to_text(block.get('content'))}"
        description += "\n\n" + (p.get("additionalPlain") or "")
        workplace = (p.get("workplaceType") or "").lower()
        salary = p.get("salaryRange") or {}
        interval = (salary.get("interval") or "").lower()
        jobs.append(finalize(NormalizedJob(
            source="lever",
            source_external_id=f"{slug}:{p.get('id')}",
            source_url=p["hostedUrl"],
            title=p.get("text", ""),
            company_name=_company(task),
            location_text=location or None,
            remote_type={"remote": "remote", "hybrid": "hybrid", "on-site": "onsite", "onsite": "onsite"}.get(workplace)
            or _remote(p.get("text"), location, description),
            employment_type=map_employment_type(categories.get("commitment")),
            salary_min=salary.get("min"),
            salary_max=salary.get("max"),
            salary_currency=salary.get("currency"),
            salary_period="hour" if "hour" in interval else "month" if "month" in interval else "year" if salary else None,
            description=description.strip(),
            apply_url=p.get("applyUrl") or p["hostedUrl"],
            posted_at=parse_datetime(p.get("createdAt")),
        )))
    return TaskResult(jobs=jobs)


def fetch_ashby(task: dict[str, Any], ctx: TaskContext) -> TaskResult:
    slug = task["params"]["slug"]
    data = ctx.client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", params={"includeCompensation": "true"}).json()
    jobs = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False or not j.get("jobUrl"):
            continue
        locations = [j.get("location")] + [s.get("location") for s in j.get("secondaryLocations") or []]
        location = ", ".join(loc for loc in locations if loc)
        description = j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml"))
        workplace = (j.get("workplaceType") or "").lower()
        if j.get("isRemote") or workplace == "remote":
            remote_type = "remote"
        else:
            remote_type = {"hybrid": "hybrid", "onsite": "onsite"}.get(workplace) or _remote(j.get("title"), location, description)
        compensation = (j.get("compensation") or {}).get("compensationTierSummary")
        jobs.append(finalize(NormalizedJob(
            source="ashby",
            source_external_id=f"{slug}:{j.get('id') or j['jobUrl']}",
            source_url=j["jobUrl"],
            title=j.get("title", ""),
            company_name=_company(task),
            location_text=location or None,
            remote_type=remote_type,
            employment_type=map_employment_type(j.get("employmentType")),
            salary_text=compensation,
            description=description,
            apply_url=j.get("applyUrl") or j["jobUrl"],
            posted_at=parse_datetime(j.get("publishedAt")),
        )))
    return TaskResult(jobs=jobs)


def fetch_smartrecruiters(task: dict[str, Any], ctx: TaskContext) -> TaskResult:
    slug = task["params"]["slug"]
    data = ctx.client.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", params={"limit": 100}).json()
    jobs = []
    for p in data.get("content", []):
        loc = p.get("location") or {}
        location = ", ".join(x for x in (loc.get("city"), loc.get("region"), (loc.get("country") or "").upper()) if x)
        level = (p.get("experienceLevel") or {}).get("label")
        country = (loc.get("country") or "").upper()
        jobs.append(finalize(NormalizedJob(
            source="smartrecruiters",
            source_external_id=f"{slug}:{p.get('id')}",
            source_url=f"https://jobs.smartrecruiters.com/{slug}/{p.get('id')}",
            title=p.get("name", ""),
            company_name=(p.get("company") or {}).get("name") or _company(task),
            location_text=location or None,
            location_restrictions=[country] if loc.get("remote") and country else [],
            remote_type="remote" if loc.get("remote") else _remote(p.get("name"), location, ""),
            employment_type=map_employment_type((p.get("typeOfEmployment") or {}).get("label")),
            description="\n".join(x for x in (p.get("name"), (p.get("function") or {}).get("label"), level) if x),
            description_quality="partial",
            apply_url=f"https://jobs.smartrecruiters.com/{slug}/{p.get('id')}",
            posted_at=parse_datetime(p.get("releasedDate")),
            raw={"seniority_hint": level, "detail_api": p.get("ref")},
        )))
    return TaskResult(jobs=jobs)


def enrich_smartrecruiters(raw: dict[str, Any], ctx: TaskContext) -> str | None:
    detail_url = raw.get("detail_api")
    if not detail_url:
        return None
    data = ctx.client.get(detail_url).json()
    sections = (data.get("jobAd") or {}).get("sections") or {}
    parts = [
        f"{(sections.get(key) or {}).get('title', '')}\n{html_to_text((sections.get(key) or {}).get('text'))}"
        for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation")
        if sections.get(key)
    ]
    return "\n\n".join(parts).strip() or None


def fetch_recruitee(task: dict[str, Any], ctx: TaskContext) -> TaskResult:
    slug = task["params"]["slug"]
    data = ctx.client.get(f"https://{slug}.recruitee.com/api/offers/").json()
    jobs = []
    for o in data.get("offers", []):
        url = o.get("careers_url")
        if not url:
            continue
        salary = o.get("salary") or {}
        description = html_to_text((o.get("description") or "") + "\n" + (o.get("requirements") or ""))
        jobs.append(finalize(NormalizedJob(
            source="recruitee",
            source_external_id=f"{slug}:{o.get('id')}",
            source_url=url,
            title=o.get("title", ""),
            company_name=o.get("company_name") or _company(task),
            location_text=o.get("location"),
            remote_type="remote" if o.get("remote") else _remote(o.get("title"), o.get("location"), description),
            employment_type=map_employment_type(o.get("employment_type_code")),
            salary_min=_float(salary.get("min")),
            salary_max=_float(salary.get("max")),
            salary_currency=salary.get("currency"),
            salary_period=_period(salary.get("period")),
            description=description,
            apply_url=o.get("careers_apply_url") or url,
            posted_at=parse_datetime(o.get("published_at") or o.get("created_at")),
        )))
    return TaskResult(jobs=jobs)


def fetch_workable(task: dict[str, Any], ctx: TaskContext) -> TaskResult:
    slug = task["params"]["slug"]
    data = ctx.client.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", params={"details": "true"}).json()
    jobs = []
    for j in data.get("jobs", []):
        url = j.get("url") or j.get("shortlink")
        if not url:
            continue
        location = ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)
        description = html_to_text(j.get("description"))
        jobs.append(finalize(NormalizedJob(
            source="workable",
            source_external_id=f"{slug}:{j.get('shortcode') or url}",
            source_url=url,
            title=j.get("title", ""),
            company_name=data.get("name") or _company(task),
            location_text=location or None,
            remote_type="remote" if j.get("telecommuting") else _remote(j.get("title"), location, description),
            employment_type=map_employment_type(j.get("employment_type")),
            description=description,
            description_quality="full" if len(description) > 300 else "partial",
            apply_url=j.get("application_url") or url,
            posted_at=parse_datetime(j.get("published_on") or j.get("created_at")),
        )))
    return TaskResult(jobs=jobs)


def _remote(title: str | None, location: str | None, description: str) -> str:
    kind = detect_remote_type(title, location)
    return kind if kind != "unknown" else detect_remote_type(description[:2000])


def _float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _period(value: str | None) -> str | None:
    text = (value or "").lower()
    for period in ("hour", "day", "week", "month", "year"):
        if period in text:
            return period
    return None


BOARD_HANDLERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "recruitee": fetch_recruitee,
    "workable": fetch_workable,
}
