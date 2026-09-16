"""Web search providers (official APIs only - no scraping of search result pages)."""
from __future__ import annotations

from typing import Any

from ..models import DiscoveredLink, TaskResult
from ..pipeline.normalize import html_to_text
from .base import TaskContext


def _brave(query: str, ctx: TaskContext, count: int, days: int) -> list[DiscoveredLink]:
    freshness = "pd" if days <= 1 else "pw" if days <= 7 else "pm" if days <= 31 else "py"
    data = ctx.client.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": min(count, 20), "freshness": freshness},
        headers={"X-Subscription-Token": ctx.env.brave_api_key or ""},
    ).json()
    return [
        DiscoveredLink(url=r["url"], via="search:brave", title=r.get("title"), snippet=html_to_text(r.get("description")))
        for r in data.get("web", {}).get("results", [])
        if r.get("url")
    ]


def _tavily(query: str, ctx: TaskContext, count: int, days: int) -> list[DiscoveredLink]:
    time_range = "day" if days <= 1 else "week" if days <= 7 else "month" if days <= 31 else "year"
    data = ctx.client.post_json(
        "https://api.tavily.com/search",
        {"query": query, "max_results": min(count, 20), "search_depth": "basic", "time_range": time_range},
        headers={"Authorization": f"Bearer {ctx.env.tavily_api_key or ''}"},
    ).json()
    return [
        DiscoveredLink(url=r["url"], via="search:tavily", title=r.get("title"), snippet=(r.get("content") or "")[:500])
        for r in data.get("results", [])
        if r.get("url")
    ]


def _google_cse(query: str, ctx: TaskContext, count: int, days: int) -> list[DiscoveredLink]:
    data = ctx.client.get(
        "https://www.googleapis.com/customsearch/v1",
        params={"key": ctx.env.google_cse_api_key, "cx": ctx.env.google_cse_cx, "q": query, "num": min(count, 10), "dateRestrict": f"d{days}"},
    ).json()
    return [
        DiscoveredLink(url=i["link"], via="search:google_cse", title=i.get("title"), snippet=i.get("snippet"))
        for i in data.get("items", [])
        if i.get("link")
    ]


PROVIDERS = {"brave": _brave, "tavily": _tavily, "google_cse": _google_cse}


def run_search(task: dict[str, Any], ctx: TaskContext) -> TaskResult:
    provider = task["source"]
    handler = PROVIDERS.get(provider)
    if handler is None:
        return TaskResult(status="skipped", note=f"Unknown search provider {provider}")
    links = handler(
        task["params"]["query"],
        ctx,
        int(ctx.cfg.get("search.results_per_query", 10)),
        int(ctx.cfg.get("search.freshness_days", 7)),
    )
    return TaskResult(links=links, note=f"{len(links)} results")
