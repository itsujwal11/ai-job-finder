"""Polite HTTP client and shared source-adapter types.

Rules enforced here for every request:
  * honest User-Agent with contact address
  * per-host minimum delay
  * robots.txt respected for generic web pages
  * response size cap
  * 401/403/429 and anti-bot challenge pages are reported as BLOCKED and never retried
    or worked around (no CAPTCHA solving, no header spoofing, no login automation)
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from ..config import Config, Env
from ..models import NormalizedJob, TaskResult

log = logging.getLogger(__name__)

_CHALLENGE_MARKERS = (
    "cf-challenge", "challenge-platform", "just a moment...", "attention required! | cloudflare",
    "px-captcha", "captcha-delivery", "datadome", "verify you are human", "are you a robot",
    "please enable js and disable any ad blocker",
)


class FetchError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class BlockedError(FetchError):
    """The site refused automated access. We record it and move on."""


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


@dataclass
class FetchResponse:
    url: str
    status: int
    content_type: str
    text: str
    #: Undecoded body. Needed for binary payloads such as gzipped sitemaps, where `text`
    #: is lossy because it was decoded with errors="replace".
    content: bytes = b""

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            raise FetchError(f"Invalid JSON from {redact_url(self.url)}: {exc}", self.status) from exc


class PoliteClient:
    def __init__(self, cfg: Config):
        self.user_agent = str(cfg.get("http.user_agent", "PersonalJobDiscovery/1.0"))
        self.robots_token = self.user_agent.split("/")[0]
        self.min_delay = float(cfg.get("http.min_delay_per_host_seconds", 2.0))
        self.max_bytes = int(float(cfg.get("http.max_response_mb", 5)) * 1024 * 1024)
        self._client = httpx.Client(
            headers={"User-Agent": self.user_agent, "Accept-Language": "en"},
            timeout=float(cfg.get("http.timeout_seconds", 25)),
            follow_redirects=True,
            max_redirects=5,
        )
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self._lock = threading.Lock()

    @property
    def raw(self) -> httpx.Client:
        return self._client

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- politeness -------------------------------------------------------------
    def _wait_for_host(self, host: str) -> None:
        with self._lock:
            last = self._last_hit.get(host, 0.0)
            wait = self.min_delay - (time.monotonic() - last)
            self._last_hit[host] = time.monotonic() + max(0.0, wait)
        if wait > 0:
            time.sleep(wait)

    def allowed_by_robots(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser: RobotFileParser | None = RobotFileParser()
            try:
                response = self._client.get(origin + "/robots.txt", timeout=10)
                if response.status_code in (401, 403):
                    parser.disallow_all = True
                elif response.status_code >= 500:
                    parser.disallow_all = True
                elif response.status_code >= 400:
                    parser.allow_all = True
                else:
                    parser.parse(response.text.splitlines())
            except httpx.HTTPError:
                parser.disallow_all = True  # unreachable robots.txt -> assume disallowed (RFC 9309)
            self._robots[origin] = parser
        parser = self._robots[origin]
        return parser is None or parser.can_fetch(self.robots_token, url)

    # -- requests ----------------------------------------------------------------
    def _send(self, method: str, url: str, **kwargs: Any) -> FetchResponse:
        host = urlsplit(url).hostname or ""
        self._wait_for_host(host)
        safe_url = redact_url(url)
        try:
            with self._client.stream(method, url, **kwargs) as response:
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise FetchError(f"Response from {safe_url} exceeds {self.max_bytes // 1_048_576} MB")
                    chunks.append(chunk)
                body = b"".join(chunks)
                status = response.status_code
                content_type = response.headers.get("content-type", "")
                text = body.decode(response.encoding or "utf-8", errors="replace")
                final_url = str(response.url)
        except httpx.TimeoutException as exc:
            raise FetchError(f"Timeout fetching {safe_url}") from exc
        except httpx.HTTPError as exc:
            raise FetchError(f"Network error fetching {safe_url}: {type(exc).__name__}") from exc

        head = text[:6000].lower()
        if status in (401, 403, 407, 429) or (status == 503 and any(m in head for m in _CHALLENGE_MARKERS)):
            raise BlockedError(f"Access refused by {host} (HTTP {status})", status)
        if status >= 400:
            raise FetchError(f"HTTP {status} from {safe_url}", status)
        if "html" in content_type and len(text) < 40_000 and any(m in head for m in _CHALLENGE_MARKERS):
            raise BlockedError(f"Anti-bot challenge page at {host} - skipped", status)
        return FetchResponse(url=final_url, status=status, content_type=content_type, text=text, content=body)

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        check_robots: bool = False,
        accept: str = "application/json",
    ) -> FetchResponse:
        if check_robots and not self.allowed_by_robots(url):
            raise BlockedError(f"Disallowed by robots.txt: {redact_url(url)}")
        return self._send("GET", url, params=params, headers={"Accept": accept, **(headers or {})})

    def post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> FetchResponse:
        return self._send("POST", url, json=payload, headers={"Accept": "application/json", **(headers or {})})


@dataclass
class TaskContext:
    client: PoliteClient
    cfg: Config
    env: Env
    run_id: int
    # (url, page_text) -> NormalizedJob | None ; provided when AI extraction is allowed
    extractor: Callable[[str, str], NormalizedJob | None] | None = None


Handler = Callable[[dict[str, Any], TaskContext], TaskResult]


def split_regions(value: str | None) -> list[str]:
    """'USA, Canada' -> ['USA', 'Canada']; generic 'Remote' -> []."""
    if not value:
        return []
    parts = [p.strip() for p in value.replace("/", ",").replace(";", ",").replace("|", ",").split(",")]
    return [p for p in parts if p and p.lower() not in {"remote", "remote job", "anywhere remote", ""}]


def finalize(job: NormalizedJob) -> NormalizedJob:
    """Trim fields to sane sizes."""
    job.title = job.title.strip()[:300]
    job.description = job.description[:30_000]
    if job.company_name:
        job.company_name = job.company_name.strip()[:200]
    return job
