"""Ojiiz (ojiiz.com) - public job/project listing API.

The board is a lead marketplace: the listing endpoint at api.ojiiz.com is public and
unauthenticated, and returns the title, description, pay range, category and date. The
company name and contact details sit behind the paid "unlock" and are never fetched here,
so postings arrive without a company - the AI analysis and the dashboard link handle that.
"""
from __future__ import annotations

from typing import Any

from ..models import NormalizedJob, TaskResult
from ..pipeline.normalize import detect_remote_type, html_to_text, parse_datetime
from .base import TaskContext, finalize

_SLUG_SAFE = str.maketrans(dict.fromkeys(r""" /\&?#'"(),:;""", "-"))


def _job_url(job: dict[str, Any]) -> str:
    """Rebuild the public detail URL: /jobListing/<category>/<title-slug>/<id>."""
    category = str(job.get("jobCategory") or "other").translate(_SLUG_SAFE).lower()
    title = str(job.get("jobTitle") or "job").translate(_SLUG_SAFE).lower()
    while "--" in category:
        category = category.replace("--", "-")
    while "--" in title:
        title = title.replace("--", "-")
    return f"https://leads.ojiiz.com/jobListing/{category.strip('-')}/{title.strip('-')[:80]}/{job.get('_id')}"


def fetch_ojiiz(task: dict[str, Any], ctx: TaskContext) -> TaskResult:
    params = task.get("params") or {}
    categories: list[str] = list(params.get("categories") or [""])
    pages = int(params.get("pages", 1))
    page_size = min(int(params.get("page_size", 100)), 100)

    jobs: list[NormalizedJob] = []
    seen: set[str] = set()
    for category in categories:
        for page in range(1, pages + 1):
            query: dict[str, Any] = {"page": page, "limit": page_size}
            if category:
                query["category"] = category
            payload = ctx.client.get(task["url"], params=query).json()
            block = payload.get("data") or {}
            rows = block.get("data") if isinstance(block, dict) else block
            if not rows:
                break
            for job in rows:
                job_id = str(job.get("_id") or "")
                if not job_id or job_id in seen or not job.get("jobTitle"):
                    continue
                if job.get("isOpen") is False:
                    continue
                seen.add(job_id)
                description = html_to_text(job.get("jobDetail"))
                heading = str(job.get("jobHeading") or "")
                jobs.append(finalize(NormalizedJob(
                    source="ojiiz",
                    source_external_id=job_id,
                    source_url=_job_url(job),
                    title=str(job["jobTitle"]),
                    company_name=None,            # behind the paid unlock - never scraped
                    location_text=None,
                    remote_type=detect_remote_type(str(job["jobTitle"]), heading) or "unknown",
                    employment_type="contract" if job.get("jobType") == "project" else "unspecified",
                    salary_text=job.get("jobPricing") or None,
                    description=f"{heading}\n\n{description}".strip(),
                    apply_url=_job_url(job),
                    posted_at=parse_datetime(job.get("jobDate") or job.get("createdAt")),
                    tags=[t for t in (job.get("tags") or []) if isinstance(t, str)],
                    raw={"category": job.get("jobCategory"), "ojiiz_type": job.get("jobType")},
                )))
            pagination = block.get("pagination") if isinstance(block, dict) else None
            if pagination and page >= int(pagination.get("totalPages") or 1):
                break
    return TaskResult(jobs=jobs, note=f"{len(jobs)} postings across {len(categories)} categor(ies)")
