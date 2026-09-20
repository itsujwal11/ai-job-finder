"""Additional public job APIs.

- The Muse: curated tech/startup jobs, no key, and it can filter by experience level.
- FindWork.dev: software jobs. Needs a free API key, so it stays disabled until
  FINDWORK_API_KEY is set in .env.

Anything that needs a key is opt-in: a source that silently returns nothing every run is worse
than one that is visibly switched off.
"""
from __future__ import annotations

from typing import Any

from ..models import NormalizedJob, TaskResult
from ..pipeline.normalize import html_to_text, map_employment_type, parse_datetime
from .base import TaskContext, finalize, split_regions


def fetch_themuse(task: dict[str, Any], ctx: TaskContext) -> TaskResult:
    """The Muse public API - https://www.themuse.com/developers/api/v2

    Paging is **1-based**; asking for page 0 returns an empty result set. `category` and `level`
    may be repeated, so they are sent as a list of pairs rather than a dict.
    """
    params = task.get("params", {})
    pages = int(params.get("pages", 3))
    categories = list(params.get("categories") or ["Software Engineering"])
    levels = list(params.get("levels") or [])

    jobs: list[NormalizedJob] = []
    seen: set[str] = set()
    for page_num in range(1, pages + 1):
        query: list[tuple[str, Any]] = [("page", page_num)]
        query += [("category", c) for c in categories]
        query += [("level", level) for level in levels]
        data = ctx.client.get(task["url"], params=query).json()
        results = data.get("results") or []
        for j in results:
            job_id = str(j.get("id", ""))
            if not job_id or job_id in seen or not j.get("name"):
                continue
            seen.add(job_id)
            company = j.get("company") or {}
            locations = j.get("locations") or []
            location_text = ", ".join(loc.get("name", "") for loc in locations) or None
            level_names = [lvl.get("name", "") for lvl in (j.get("levels") or [])]
            landing = (j.get("refs") or {}).get("landing_page")
            if not landing:
                continue
            remote = any("remote" in (loc.get("name") or "").lower() for loc in locations)
            jobs.append(finalize(NormalizedJob(
                source="themuse",
                source_external_id=job_id,
                source_url=landing,
                title=j["name"],
                company_name=company.get("name"),
                location_text=location_text,
                location_restrictions=[] if remote else split_regions(location_text),
                remote_type="remote" if remote else "unknown",
                employment_type=map_employment_type(j.get("type")),
                description=html_to_text(j.get("contents") or ""),
                apply_url=landing,
                posted_at=parse_datetime(j.get("publication_date")),
                tags=[t.get("name", "") if isinstance(t, dict) else str(t) for t in (j.get("tags") or [])],
                raw={
                    # assess_seniority reads seniority_hint, so "Senior Level" blocks without an AI call.
                    "seniority_hint": ", ".join(level_names),
                    "category": ", ".join(c.get("name", "") for c in (j.get("categories") or [])),
                },
            )))
        if page_num >= int(data.get("page_count") or 1) or not results:
            break
    return TaskResult(jobs=jobs, note=f"{len(jobs)} postings over {min(pages, page_num)} page(s)")


def fetch_findwork(task: dict[str, Any], ctx: TaskContext) -> TaskResult:
    """FindWork.dev - https://findwork.dev/developers/

    The endpoint rejects anonymous calls with HTTP 401, so without a key the task is reported as
    skipped rather than failing the run.
    """
    api_key = ctx.env.findwork_api_key
    if not api_key:
        return TaskResult(status="skipped", note="FINDWORK_API_KEY is not set in .env")
    params = task.get("params", {})
    data = ctx.client.get(
        task["url"],
        params={"search": params.get("search", "developer"), "remote": "true", "sort_by": "date"},
        headers={"Authorization": f"Token {api_key}"},
    ).json()

    jobs: list[NormalizedJob] = []
    for j in data.get("results") or []:
        if not j.get("url") or not j.get("role"):
            continue
        location = j.get("location") or ""
        remote = bool(j.get("remote")) or "remote" in location.lower()
        jobs.append(finalize(NormalizedJob(
            source="findwork",
            source_external_id=str(j.get("id", "")),
            source_url=j["url"],
            title=j["role"],
            company_name=j.get("company_name"),
            company_website=j.get("company_url"),
            location_text=location or ("Remote" if remote else None),
            location_restrictions=[] if remote else split_regions(location),
            remote_type="remote" if remote else "unknown",
            employment_type=map_employment_type(j.get("employment_type")),
            description=html_to_text(j.get("text") or ""),
            apply_url=j["url"],
            posted_at=parse_datetime(j.get("date_posted")),
            tags=j.get("keywords") or [],
            raw={"source_origin": j.get("source")},
        )))
    return TaskResult(jobs=jobs)
