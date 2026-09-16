"""Hacker News monthly 'Who is hiring?' and 'Freelancer? Seeking freelancer?' threads (public Algolia API)."""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from ..models import DiscoveredLink, NormalizedJob, TaskResult
from ..pipeline.normalize import html_to_text, map_employment_type, parse_datetime
from .ats import detect_ats_board
from .base import TaskContext, finalize

ALGOLIA = "https://hn.algolia.com/api/v1"
_THREAD_NEEDLES = {"who_is_hiring": "who is hiring", "freelancer": "seeking freelancer"}
_ROLE_RE = re.compile(r"\b(engineer|developer|frontend|front-end|full[- ]?stack|react|web|qa|support|devops|intern|programmer|designer)\b", re.I)
_RELEVANT_RE = re.compile(r"\b(frontend|front-end|react|javascript|typescript|full[- ]?stack|web developer|software engineer|junior|intern|next\.?js|qa|support engineer)\b", re.I)
_URL_RE = re.compile(r"https?://[^\s)<>\"']+")


def fetch_hackernews(task: dict[str, Any], ctx: TaskContext) -> TaskResult:
    thread = task.get("params", {}).get("thread", "who_is_hiring")
    needle = _THREAD_NEEDLES[thread]
    search = ctx.client.get(f"{ALGOLIA}/search_by_date", params={"tags": "story,author_whoishiring", "hitsPerPage": 10}).json()
    hit = next((h for h in search.get("hits", []) if needle in (h.get("title") or "").lower()), None)
    if hit is None:
        return TaskResult(status="skipped", note=f"No recent '{needle}' thread found")
    created = parse_datetime(hit.get("created_at"))
    if created and created < datetime.now(UTC) - timedelta(days=40):
        return TaskResult(status="skipped", note="Latest thread is older than 40 days")

    item = ctx.client.get(f"{ALGOLIA}/items/{hit['objectID']}").json()
    jobs: list[NormalizedJob] = []
    links: list[DiscoveredLink] = []
    for comment in item.get("children", []):
        text = html_to_text(comment.get("text"))
        lower = text.lower()
        if not text or "remote" not in lower or not _RELEVANT_RE.search(text):
            continue
        if thread == "freelancer" and not lower.startswith("seeking freelancer"):
            continue
        first_line = re.sub(r"^seeking freelancer\s*[-:|]?\s*", "", text.split("\n", 1)[0], flags=re.I)
        parts = [p.strip() for p in re.split(r"\s+[|•·]\s+", first_line) if p.strip()]
        company = parts[0][:120] if parts else None
        title = next((p for p in parts[1:] if _ROLE_RE.search(p)), None) or first_line[:140]
        location = next((p for p in parts if re.search(r"remote|onsite|on-site|hybrid", p, re.I)), None)
        employment = "freelance" if thread == "freelancer" else map_employment_type(" ".join(parts))

        for url in _URL_RE.findall(text):
            if detect_ats_board(url):
                links.append(DiscoveredLink(url=url, via="hackernews"))

        jobs.append(finalize(NormalizedJob(
            source="hackernews",
            source_external_id=str(comment.get("id")),
            source_url=f"https://news.ycombinator.com/item?id={comment.get('id')}",
            title=title,
            company_name=company,
            location_text=location,
            remote_type="remote" if location and "remote" in location.lower() else "unknown",
            employment_type=employment,
            description=text,
            posted_at=parse_datetime(comment.get("created_at")),
            raw={"thread": thread, "author": comment.get("author")},
        )))
    return TaskResult(jobs=jobs, links=links)
