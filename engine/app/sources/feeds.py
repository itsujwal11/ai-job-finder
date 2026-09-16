"""Public remote-job APIs and RSS feeds (documented endpoints intended for reuse).

Attribution: postings keep their original source URL, which the dashboard links to.
"""
from __future__ import annotations

from typing import Any

import feedparser

from ..models import NormalizedJob, TaskResult
from ..pipeline.normalize import html_to_text, map_employment_type, parse_datetime
from .base import TaskContext, finalize, split_regions


def fetch_remotive(task: dict[str, Any], ctx: TaskContext) -> TaskResult:
    data = ctx.client.get(task["url"]).json()
    jobs = []
    for j in data.get("jobs", []):
        if not j.get("url") or not j.get("title"):
            continue
        location = j.get("candidate_required_location") or ""
        jobs.append(finalize(NormalizedJob(
            source="remotive",
            source_external_id=str(j.get("id")),
            source_url=j["url"],
            title=j["title"],
            company_name=j.get("company_name"),
            location_text=location or None,
            location_restrictions=split_regions(location),
            remote_type="remote",
            employment_type=map_employment_type(j.get("job_type")),
            salary_text=j.get("salary") or None,
            description=html_to_text(j.get("description")),
            apply_url=j["url"],
            posted_at=parse_datetime(j.get("publication_date")),
            tags=j.get("tags") or [],
            raw={"category": j.get("category")},
        )))
    return TaskResult(jobs=jobs)


def fetch_remoteok(task: dict[str, Any], ctx: TaskContext) -> TaskResult:
    data = ctx.client.get(task["url"]).json()
    jobs = []
    for j in data if isinstance(data, list) else []:
        if not isinstance(j, dict) or "legal" in j or not j.get("position") or not j.get("url"):
            continue
        location = j.get("location") or ""
        salary_min = j.get("salary_min") or None
        salary_max = j.get("salary_max") or None
        jobs.append(finalize(NormalizedJob(
            source="remoteok",
            source_external_id=str(j.get("id")),
            source_url=j["url"],
            title=j["position"],
            company_name=j.get("company"),
            location_text=location or None,
            location_restrictions=split_regions(location) if location.lower() not in {"worldwide", "anywhere"} else [location],
            remote_type="remote",
            salary_min=float(salary_min) if salary_min else None,
            salary_max=float(salary_max) if salary_max else None,
            salary_currency="USD" if (salary_min or salary_max) else None,
            salary_period="year" if (salary_min or salary_max) else None,
            description=html_to_text(j.get("description")),
            apply_url=j.get("apply_url") or j["url"],
            posted_at=parse_datetime(j.get("date") or j.get("epoch")),
            tags=j.get("tags") or [],
        )))
    return TaskResult(jobs=jobs)


def fetch_jobicy(task: dict[str, Any], ctx: TaskContext) -> TaskResult:
    urls = [task["url"]] + [f"{task['url']}&tag={tag}" for tag in task.get("params", {}).get("tags", [])]
    seen: set[str] = set()
    jobs = []
    for url in urls:
        data = ctx.client.get(url).json()
        for j in data.get("jobs", []):
            job_id = str(j.get("id"))
            if job_id in seen or not j.get("url"):
                continue
            seen.add(job_id)
            geo = j.get("jobGeo") or ""
            salary_min, salary_max = j.get("annualSalaryMin"), j.get("annualSalaryMax")
            jobs.append(finalize(NormalizedJob(
                source="jobicy",
                source_external_id=job_id,
                source_url=j["url"],
                title=html_to_text(j.get("jobTitle")),
                company_name=j.get("companyName"),
                location_text=geo or None,
                location_restrictions=split_regions(geo),
                remote_type="remote",
                employment_type=map_employment_type(j.get("jobType")),
                salary_min=float(salary_min) if salary_min else None,
                salary_max=float(salary_max) if salary_max else None,
                salary_currency=j.get("salaryCurrency") if (salary_min or salary_max) else None,
                salary_period="year" if (salary_min or salary_max) else None,
                description=html_to_text(j.get("jobDescription") or j.get("jobExcerpt")),
                apply_url=j["url"],
                posted_at=parse_datetime(j.get("pubDate")),
                raw={"seniority_hint": j.get("jobLevel"), "industry": j.get("jobIndustry")},
            )))
    return TaskResult(jobs=jobs)


def fetch_himalayas(task: dict[str, Any], ctx: TaskContext) -> TaskResult:
    params = task.get("params", {})
    page_size = int(params.get("page_size", 20))
    jobs = []
    for page in range(int(params.get("pages", 5))):
        data = ctx.client.get(task["url"], params={"limit": page_size, "offset": page * page_size}).json()
        batch = data.get("jobs", [])
        for j in batch:
            url = j.get("applicationLink") or j.get("guid")
            if not url or not j.get("title"):
                continue
            restrictions = [r if isinstance(r, str) else (r.get("name") or "") for r in (j.get("locationRestrictions") or [])]
            seniority = j.get("seniority") or []
            jobs.append(finalize(NormalizedJob(
                source="himalayas",
                source_external_id=str(j.get("guid") or url),
                source_url=j.get("guid") or url,
                title=j["title"],
                company_name=j.get("companyName"),
                location_text=", ".join(restrictions) or "Worldwide",
                location_restrictions=[r for r in restrictions if r],
                remote_type="remote",
                employment_type=map_employment_type(j.get("employmentType")),
                salary_min=float(j["minSalary"]) if j.get("minSalary") else None,
                salary_max=float(j["maxSalary"]) if j.get("maxSalary") else None,
                salary_currency=(j.get("currency") or j.get("salaryCurrency")) if j.get("minSalary") or j.get("maxSalary") else None,
                salary_period="year" if j.get("minSalary") or j.get("maxSalary") else None,
                description=html_to_text(j.get("description") or j.get("excerpt")),
                apply_url=j.get("applicationLink") or url,
                posted_at=parse_datetime(j.get("pubDate")),
                expires_at=parse_datetime(j.get("expiryDate")),
                raw={"seniority_hint": ", ".join(seniority) if isinstance(seniority, list) else seniority,
                     "timezones": j.get("timezoneRestrictions")},
            )))
        if len(batch) < page_size:
            break
    return TaskResult(jobs=jobs)


def fetch_weworkremotely(task: dict[str, Any], ctx: TaskContext) -> TaskResult:
    feed = feedparser.parse(ctx.client.get(task["url"], accept="application/rss+xml, application/xml").text)
    jobs = []
    for entry in feed.entries:
        raw_title = entry.get("title", "")
        company, _, title = raw_title.partition(":")
        if not title:
            company, title = "", raw_title
        region = entry.get("region") or ""
        jobs.append(finalize(NormalizedJob(
            source="weworkremotely",
            source_external_id=entry.get("id") or entry.get("link"),
            source_url=entry.get("link"),
            title=title.strip(),
            company_name=company.strip() or None,
            location_text=region or None,
            location_restrictions=[] if "anywhere" in region.lower() else split_regions(region.replace(" Only", "")),
            remote_type="remote",
            employment_type=map_employment_type(entry.get("type")),
            description=html_to_text(entry.get("summary") or entry.get("description")),
            apply_url=entry.get("link"),
            posted_at=parse_datetime(entry.get("published")),
            raw={"region": region},
        )))
    return TaskResult(jobs=[j for j in jobs if j.source_url and j.title])


def fetch_workingnomads(task: dict[str, Any], ctx: TaskContext) -> TaskResult:
    data = ctx.client.get(task["url"]).json()
    jobs = []
    for j in data if isinstance(data, list) else []:
        if not j.get("url") or not j.get("title"):
            continue
        location = j.get("location") or ""
        tags = j.get("tags") or ""
        jobs.append(finalize(NormalizedJob(
            source="workingnomads",
            source_url=j["url"],
            title=j["title"],
            company_name=j.get("company_name"),
            location_text=location or None,
            location_restrictions=[] if location.lower() in {"anywhere", "worldwide", ""} else split_regions(location),
            remote_type="remote",
            description=html_to_text(j.get("description")),
            apply_url=j["url"],
            posted_at=parse_datetime(j.get("pub_date")),
            tags=[t.strip() for t in tags.split(",")] if isinstance(tags, str) else list(tags),
            raw={"category": j.get("category_name")},
        )))
    return TaskResult(jobs=jobs)


def fetch_arbeitnow(task: dict[str, Any], ctx: TaskContext) -> TaskResult:
    data = ctx.client.get(task["url"]).json()
    jobs = []
    for j in data.get("data", []):
        if not j.get("remote") or not j.get("url"):
            continue
        jobs.append(finalize(NormalizedJob(
            source="arbeitnow",
            source_external_id=j.get("slug"),
            source_url=j["url"],
            title=j.get("title", ""),
            company_name=j.get("company_name"),
            location_text=j.get("location"),
            remote_type="remote",
            employment_type=map_employment_type(j.get("job_types")),
            description=html_to_text(j.get("description")),
            apply_url=j["url"],
            posted_at=parse_datetime(j.get("created_at")),
            tags=j.get("tags") or [],
        )))
    return TaskResult(jobs=jobs)
