"""Nepali job boards (merojob, froxjob, jobaxle, ...) via their public sitemaps.

None of these publish a job API, but all of them serve a schema.org JobPosting block on every
detail page, which page.py already understands. So this adapter does the cheap half: read the
board's sitemap, keep the entries that are new enough, and hand the URLs back as discovered
links. They become normal `page` fetch tasks and are parsed with zero AI cost.

robots.txt is honoured for both the sitemap and every detail page (page.py checks it), and the
per-host delay in PoliteClient keeps the crawl slow.
"""
from __future__ import annotations

import gzip
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from ..config import Config
from ..models import DiscoveredLink, TaskResult
from ..pipeline.normalize import parse_datetime
from ..pipeline.rules import assess_relevance, assess_seniority
from .base import TaskContext

log = logging.getLogger(__name__)

_URL_ENTRY = re.compile(r"<url>(.*?)</url>", re.DOTALL | re.IGNORECASE)
_SITEMAP_ENTRY = re.compile(r"<sitemap>(.*?)</sitemap>", re.DOTALL | re.IGNORECASE)
_LOC = re.compile(r"<loc>\s*([^<]+?)\s*</loc>", re.IGNORECASE)
_LASTMOD = re.compile(r"<lastmod>\s*([^<]+?)\s*</lastmod>", re.IGNORECASE)


def _fetch_sitemap(url: str, ctx: TaskContext) -> str:
    """Read a sitemap, transparently unpacking a `.gz` body.

    httpx unpacks Content-Encoding, but a sitemap served as a gzip *file* (merojob does this)
    arrives still compressed, so it has to be inflated from the raw bytes.
    """
    response = ctx.client.get(url, accept="application/xml,text/xml,application/gzip,*/*")
    if response.content[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(response.content).decode("utf-8", "replace")
        except (OSError, EOFError):
            log.warning("Sitemap %s looks gzipped but could not be inflated", url)
    return response.text


def _entries(xml: str) -> list[tuple[str, datetime | None]]:
    out: list[tuple[str, datetime | None]] = []
    for block in _URL_ENTRY.findall(xml):
        loc = _LOC.search(block)
        if not loc:
            continue
        stamp = _LASTMOD.search(block)
        out.append((loc.group(1), parse_datetime(stamp.group(1)) if stamp else None))
    return out


def _resolve_sitemaps(url: str, ctx: TaskContext, pattern: str | None) -> list[str]:
    """Follow one level of <sitemapindex>, keeping only child sitemaps matching `pattern`."""
    xml = _fetch_sitemap(url, ctx)
    if "<sitemapindex" not in xml.lower():
        return [xml]
    children = [_LOC.search(b).group(1) for b in _SITEMAP_ENTRY.findall(xml) if _LOC.search(b)]
    if pattern:
        children = [c for c in children if re.search(pattern, c)]
    return [_fetch_sitemap(child, ctx) for child in children[:3]]


def title_from_url(url: str) -> str:
    """Best-effort job title from the URL slug: '/junior-php-developer-12' -> 'junior php developer'.

    These boards put the posting title in the slug, which lets the same relevance rules the
    processor uses run *before* the detail page is fetched. That keeps the crawl small: a board
    publishes mostly non-technical vacancies, and there is no reason to download them.
    """
    slug = url.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
    slug = re.sub(r"-\d+$", "", slug)                    # trailing board id
    return slug.replace("-", " ").replace("_", " ").strip()


def _worth_fetching(url: str, cfg: Config) -> bool:
    title = title_from_url(url)
    if len(title) < 3:
        return True                                       # unreadable slug: let the page decide
    relevant, _ = assess_relevance(title, "", cfg, nepal_local=True)
    if not relevant:
        return False
    return not assess_seniority(title, "", cfg).blocked


def fetch_local_board(task: dict[str, Any], ctx: TaskContext) -> TaskResult:
    params = task.get("params") or {}
    url_pattern = params.get("job_url_pattern")
    max_urls = int(params.get("max_urls", 40))
    max_age_days = int(params.get("max_age_days", ctx.cfg.get("pipeline.max_posting_age_days", 30)))
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)

    entries: list[tuple[str, datetime | None]] = []
    for xml in _resolve_sitemaps(task["url"], ctx, params.get("sitemap_pattern")):
        entries.extend(_entries(xml))

    if url_pattern:
        entries = [(u, d) for u, d in entries if re.search(url_pattern, u)]
    total = len(entries)
    entries = [(u, d) for u, d in entries if _worth_fetching(u, ctx.cfg)]
    dated = [(u, d) for u, d in entries if d is not None]
    if dated:
        # Boards that publish <lastmod> let us take only genuinely recent postings.
        fresh = [(u, d) for u, d in dated if d >= cutoff]
        fresh.sort(key=lambda pair: pair[1], reverse=True)
        chosen = [u for u, _ in fresh[:max_urls]]
        note = f"{len(chosen)} fetched; {len(entries)} of {total} sitemap URLs pass the title filter"
    else:
        # No dates: sitemaps of these boards are append-ordered, so the tail is newest.
        chosen = [u for u, _ in entries[-max_urls:]][::-1]
        note = f"{len(chosen)} fetched; {len(entries)} of {total} pass the title filter (no lastmod)"

    links = [DiscoveredLink(url=u, via=f"local_board:{task['source']}") for u in chosen]
    return TaskResult(links=links, note=note)
