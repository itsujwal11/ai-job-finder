"""Store normalized postings and discovered links. Duplicate protection happens here."""
from __future__ import annotations

import re

import psycopg

from .. import db
from ..config import Config
from ..models import DiscoveredLink, NormalizedJob
from ..pipeline.dedup import canonicalize_url, find_duplicate_posting, fingerprint, url_hash
from ..pipeline.normalize import host_matches, host_of, norm_company, norm_title, parse_salary_text, to_npr_monthly
from ..pipeline.prescore import prescore
from ..sources.ats import detect_ats_board
from .tasks import PRIORITY, board_url, enqueue_within_caps, page_source

_NON_HTML = re.compile(r"\.(pdf|docx?|pptx?|xlsx?|png|jpe?g|gif|zip|mp4)(\?|$)", re.IGNORECASE)


def _touch(conn: psycopg.Connection, opportunity_id: int, run_id: int, job: NormalizedJob) -> None:
    db.execute("UPDATE opportunities SET last_seen_at = now(), last_run_id = %s WHERE id = %s", (run_id, opportunity_id), conn)
    db.execute(
        "INSERT INTO opportunity_sightings (opportunity_id, run_id, source, url) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (opportunity_id, run_id, job.source, job.source_url),
        conn,
    )


def store_job(conn: psycopg.Connection, job: NormalizedJob, run_id: int, cfg: Config) -> str:
    """Returns 'new', 'seen' (same posting already stored), 'duplicate' (same role elsewhere) or 'invalid'."""
    if not job.title.strip() or not job.source_url.startswith(("http://", "https://")):
        return "invalid"

    hashed = url_hash(job.source_url)
    sql = "SELECT id FROM opportunities WHERE url_hash = %s"
    params: list[object] = [hashed]
    if job.source_external_id:
        sql += " OR (source = %s AND source_external_id = %s)"
        params += [job.source, job.source_external_id]
    existing = db.fetch_one(sql + " LIMIT 1", params, conn)
    if existing:
        _touch(conn, existing["id"], run_id, job)
        return "seen"

    company_norm = norm_company(job.company_name)
    title_norm = norm_title(job.title)
    fp = fingerprint(company_norm, title_norm)

    salary_min, salary_max, currency, period = job.salary_min, job.salary_max, job.salary_currency, job.salary_period
    if salary_min is None and salary_max is None and job.salary_text:
        parsed = parse_salary_text(job.salary_text)
        if parsed:
            salary_min, salary_max, currency, period = parsed["min"], parsed["max"], parsed["currency"], parsed["period"]
    if (salary_min or salary_max) and not period:
        period = "year"
    fx = cfg.get("compensation.fx_to_npr", {}) or {}
    npr_min = to_npr_monthly(salary_min, currency, period, fx)
    npr_max = to_npr_monthly(salary_max, currency, period, fx)

    duplicate_of = find_duplicate_posting(conn, company_norm=company_norm, title_norm=title_norm, fp=fp)
    raw = dict(job.raw)
    raw.update({"tags": job.tags, "salary_text": job.salary_text})

    # Cheap ordering signal so the scarce AI analysis slots go to the most promising postings.
    rank, rank_reasons = prescore(
        {
            "title": job.title, "description": job.description, "remote_type": job.remote_type,
            "location_text": job.location_text, "source": job.source,
            "salary_npr_monthly_min": npr_min, "salary_npr_monthly_max": npr_max,
        },
        cfg,
    )

    row = db.fetch_one(
        """
        INSERT INTO opportunities (
            first_run_id, last_run_id, source, source_external_id, source_url, canonical_url, url_hash, fingerprint,
            duplicate_of, title, title_norm, company_name, company_norm, company_website, location_text,
            location_restrictions, remote_type, employment_type, salary_min, salary_max, salary_currency, salary_period,
            salary_npr_monthly_min, salary_npr_monthly_max, description, description_quality, apply_url, apply_email,
            posted_at, expires_at, raw, pipeline_status, prescore, prescore_reasons
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        RETURNING id
        """,
        (
            run_id, run_id, job.source, job.source_external_id, job.source_url, canonicalize_url(job.source_url), hashed, fp,
            duplicate_of, job.title, title_norm, job.company_name, company_norm, job.company_website, job.location_text,
            job.location_restrictions, job.remote_type, job.employment_type, salary_min, salary_max, currency, period,
            npr_min, npr_max, job.description, job.description_quality, job.apply_url, job.apply_email,
            job.posted_at, job.expires_at, db.jsonb(raw), "duplicate" if duplicate_of else "new",
            rank, db.jsonb(rank_reasons),
        ),
        conn,
    )
    if row is None:
        return "seen"
    db.execute(
        "INSERT INTO opportunity_sightings (opportunity_id, run_id, source, url) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (duplicate_of or row["id"], run_id, job.source, job.source_url),
        conn,
    )
    return "duplicate" if duplicate_of else "new"


def store_link(conn: psycopg.Connection, link: DiscoveredLink, run_id: int, cfg: Config) -> str:
    """Route a discovered URL: ATS board -> crawl via API; login-walled site -> lead; else -> page fetch."""
    url = link.url.strip()
    if not url.startswith(("http://", "https://")) or len(url) > 2000 or _NON_HTML.search(url):
        return "invalid"

    board = detect_ats_board(url)
    if board:
        if not cfg.get("ats.auto_discover", True):
            return "board_ignored"
        provider, slug = board
        inserted = db.fetch_one(
            "INSERT INTO ats_boards (provider, slug, discovered_via) VALUES (%s, %s, %s)"
            " ON CONFLICT (provider, slug) DO NOTHING RETURNING id",
            (provider, slug, link.via),
            conn,
        )
        if inserted is None:
            return "board_known"
        enqueue_within_caps(
            conn, cfg, run_id, "ats_board", provider, f"{provider}: {slug} (new)", board_url(provider, slug),
            {"slug": slug}, PRIORITY["ats_board_discovered"],
        )
        return "board_new"

    hashed = url_hash(url)
    if host_matches(host_of(url), cfg.terms("http.no_fetch_domains")):
        db.execute(
            "INSERT INTO discovered_urls (url, url_hash, discovered_via, title_hint, snippet, status, status_reason)"
            " VALUES (%s, %s, %s, %s, %s, 'lead_only', 'Site requires login or does not allow automated access')"
            " ON CONFLICT (url_hash) DO NOTHING",
            (url, hashed, link.via, link.title, link.snippet),
            conn,
        )
        return "lead"

    already_stored = db.fetch_one("SELECT id FROM opportunities WHERE url_hash = %s", (hashed,), conn)
    if already_stored:
        return "url_known"
    inserted = db.fetch_one(
        "INSERT INTO discovered_urls (url, url_hash, discovered_via, title_hint, snippet)"
        " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (url_hash) DO NOTHING RETURNING id",
        (url, hashed, link.via, link.title, link.snippet),
        conn,
    )
    if inserted is None:
        return "url_known"
    label = f"{host_of(url)}{('/' + url.split('/', 3)[3])[:80] if url.count('/') >= 3 else ''}"
    if enqueue_within_caps(conn, cfg, run_id, "page", page_source(link.via), label, url,
                           {"title_hint": link.title}, PRIORITY["page"]):
        return "page_queued"
    return "page_deferred"
