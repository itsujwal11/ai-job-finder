"""Duplicate detection for postings and applications."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg
from rapidfuzz import fuzz

from .. import db

_TRACKING_EXACT = {
    "ref", "refs", "referrer", "source", "src", "gh_src", "lever-source", "lever-origin", "fbclid", "gclid",
    "dclid", "msclkid", "mc_cid", "mc_eid", "trk", "trackingid", "refid", "campaign", "medium", "via", "si",
    "igshid", "_hsenc", "_hsmi", "sc_cid", "jobsource", "source_id", "utm", "rx_source", "ashby_jid_source",
}


def _is_tracking(key: str) -> bool:
    k = key.lower()
    return k.startswith("utm_") or k in _TRACKING_EXACT


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() if parts.scheme in ("http", "https") else "https"
    if scheme == "http":
        scheme = "https"
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    port = f":{parts.port}" if parts.port and parts.port not in (80, 443) else ""
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    query = sorted((k, v) for k, v in parse_qsl(parts.query) if not _is_tracking(k))
    return urlunsplit((scheme, host + port, path, urlencode(query), ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(canonicalize_url(url).encode("utf-8")).hexdigest()


def fingerprint(company_norm: str, title_norm: str) -> str:
    return hashlib.sha1(f"{company_norm}|{title_norm}".encode("utf-8")).hexdigest()


def title_similarity(a: str, b: str) -> float:
    return fuzz.token_sort_ratio(a, b)


def find_duplicate_posting(
    conn: psycopg.Connection,
    *,
    company_norm: str,
    title_norm: str,
    fp: str,
    window_days: int = 60,
    threshold: float = 90,
) -> int | None:
    """Return the id of an existing original posting for the same company + role, if any.

    Postings without a company name are never matched on title alone - two unrelated
    "Frontend Developer" ads from unknown companies must not collapse into one.
    """
    if not company_norm or not title_norm:
        return None
    rows = db.fetch_all(
        """
        SELECT id, title_norm, fingerprint FROM opportunities
        WHERE duplicate_of IS NULL AND company_norm = %s
          AND first_seen_at > now() - make_interval(days => %s)
        ORDER BY first_seen_at LIMIT 200
        """,
        (company_norm, window_days),
        conn,
    )
    for row in rows:
        if row["fingerprint"] == fp:
            return row["id"]
    best = max(rows, key=lambda r: title_similarity(title_norm, r["title_norm"]), default=None)
    if best and title_similarity(title_norm, best["title_norm"]) >= threshold:
        return best["id"]
    return None


def find_prior_application(
    conn: psycopg.Connection | None,
    *,
    company_norm: str,
    title_norm: str,
    window_days: int,
    threshold: float,
    exclude_opportunity_id: int | None = None,
) -> dict | None:
    """Duplicate-application guard: same company and a similar title within the window."""
    if not company_norm:
        return None
    rows = db.fetch_all(
        """
        SELECT a.id, a.opportunity_id, a.title_norm, a.applied_at, a.status
        FROM applications a
        WHERE a.company_norm = %s AND a.status <> 'withdrawn'
          AND a.applied_at > now() - make_interval(days => %s)
        """,
        (company_norm, window_days),
        conn,
    )
    for row in rows:
        if exclude_opportunity_id and row["opportunity_id"] == exclude_opportunity_id:
            return row
        if title_similarity(title_norm, row["title_norm"]) >= threshold:
            return row
    return None
