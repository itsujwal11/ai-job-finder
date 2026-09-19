"""Claude API access for analysis, extraction, web discovery and application materials.

Every call is guarded by the daily budget in config (ai.daily_budget_usd) and recorded in
the ai_usage table with token counts and estimated cost.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Any, TypeVar

import anthropic
import httpx
import pydantic
from pydantic import BaseModel

from . import db
from .config import PROMPTS_DIR, Config, Env
from .models import ExtractedPosting, JobAnalysis, MaterialsVerification, TailoredMaterials
from .profile import CandidateProfile

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_POSTING_CHARS = 24_000


#: HTTP statuses that mean "busy / over quota, try again shortly" rather than "wrong request".
RETRYABLE_STATUS = (408, 409, 425, 429, 500, 502, 503, 504, 529)

#: Models we have already warned about having no pricing entry (keeps logs readable).
_unpriced_warned: set[str] = set()

#: model -> the local date on which its per-day quota ran out. Process-wide, so every run in
#: the same day skips a model that is already spent instead of re-discovering it.
_exhausted_today: dict[str, "date"] = {}


class RateLimiter:
    """Spreads requests evenly so a provider's requests-per-minute cap is never exceeded.

    Free API tiers (Gemini, Groq, OpenRouter, ...) reject bursts with HTTP 429 even when the
    daily quota is untouched, so the engine paces itself instead of relying on retries alone.
    """

    def __init__(self, per_minute: float):
        self.interval = 60.0 / per_minute if per_minute and per_minute > 0 else 0.0
        self._next_free = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        if self.interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next_free - now)
            self._next_free = max(now, self._next_free) + self.interval
        if wait > 0:
            time.sleep(wait)

    def pause_until(self, seconds_from_now: float) -> None:
        """Push every pending request back, e.g. after the server sent Retry-After."""
        with self._lock:
            self._next_free = max(self._next_free, time.monotonic() + seconds_from_now)


class AIError(RuntimeError):
    """A single AI call failed; the item can be retried in a later run."""


class AIUnavailable(AIError):
    """AI cannot be used at all right now (missing/invalid key)."""


class BudgetExceeded(AIError):
    """Daily budget reached; stop making calls until tomorrow."""


class AIRefused(AIError):
    pass


class _QuotaExhausted(AIError):
    """Internal: this model is spent for today. Callers fall through to the next model."""


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


_LEADS_TOOL = {
    "name": "record_job_leads",
    "description": "Record job postings found through web search. Only use URLs that appeared in search results.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "leads": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "title": {"type": "string"},
                        "company": {"type": "string"},
                        "location_note": {"type": "string"},
                        "why_relevant": {"type": "string"},
                    },
                    "required": ["url", "title", "company", "location_note", "why_relevant"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["leads"],
        "additionalProperties": False,
    },
}


class ClaudeService:
    def __init__(self, cfg: Config, env: Env, profile: CandidateProfile):
        if not env.anthropic_api_key:
            raise AIUnavailable("ANTHROPIC_API_KEY is not set - AI analysis is paused")
        self.cfg = cfg
        self.env = env
        self.profile = profile
        self.model = str(cfg.get("ai.model"))
        self.client = anthropic.Anthropic(api_key=env.anthropic_api_key, max_retries=3, timeout=600.0)

    # ------------------------------------------------------------------ budget
    def spent_today_usd(self) -> float:
        tz = self.cfg.get("timezone", "Asia/Kathmandu")
        value = db.fetch_value(
            "SELECT COALESCE(SUM(cost_usd), 0) AS spent FROM ai_usage"
            " WHERE (created_at AT TIME ZONE %s)::date = (now() AT TIME ZONE %s)::date",
            (tz, tz),
        )
        return float(value or 0)

    def ensure_budget(self) -> None:
        budget = float(self.cfg.get("ai.daily_budget_usd", 3.0))
        spent = self.spent_today_usd()
        if spent >= budget:
            raise BudgetExceeded(f"Daily AI budget reached (${spent:.2f} of ${budget:.2f})")

    def _price(self, model: str) -> dict[str, float]:
        pricing: dict[str, dict[str, float]] = self.cfg.get("ai.pricing", {}) or {}
        for key in sorted(pricing, key=len, reverse=True):
            if key != "default" and model.startswith(key):
                return pricing[key]
        if model not in _unpriced_warned:
            _unpriced_warned.add(model)
            log.warning("No ai.pricing entry for model %r - using ai.pricing.default for the budget guard", model)
        return pricing.get("default") or {"input": 5.0, "output": 25.0}

    def _record(self, purpose: str, message: Any, run_id: int | None, opportunity_id: int | None, ok: bool = True) -> None:
        usage = message.usage
        price = self._price(getattr(message, "model", None) or self.model)
        input_tokens = usage.input_tokens or 0
        output_tokens = usage.output_tokens or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        server_tools = getattr(usage, "server_tool_use", None)
        searches = (getattr(server_tools, "web_search_requests", 0) or 0) if server_tools else 0
        cost = (
            input_tokens * price["input"]
            + cache_write * price["input"] * 1.25
            + cache_read * price["input"] * 0.1
            + output_tokens * price["output"]
        ) / 1_000_000 + searches * float(self.cfg.get("ai.web_search_usd_per_1000", 10.0)) / 1000
        db.execute(
            """
            INSERT INTO ai_usage (run_id, opportunity_id, purpose, model, input_tokens, output_tokens,
                                  cache_read_tokens, cache_write_tokens, web_search_requests, cost_usd, request_id, ok)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (run_id, opportunity_id, purpose, getattr(message, "model", self.model), input_tokens, output_tokens,
             cache_read, cache_write, searches, round(cost, 5), getattr(message, "_request_id", None), ok),
        )

    # ------------------------------------------------------------------ request building
    def _model_params(self, effort_key: str) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if self.model.startswith("claude-haiku-4-5"):
            return params  # Haiku 4.5 has no adaptive thinking / effort
        params["thinking"] = {"type": "adaptive"}
        params["output_config"] = {"effort": self.cfg.get(f"ai.effort.{effort_key}", "medium")}
        if self.cfg.get("ai.use_refusal_fallbacks", True) and self.model.startswith(("claude-opus-5", "claude-fable-5")):
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    def _system(self, prompt_name: str) -> list[dict[str, Any]]:
        return [
            {"type": "text", "text": load_prompt(prompt_name)},
            {"type": "text", "text": self.profile.prompt_block(), "cache_control": {"type": "ephemeral"}},
        ]

    def _call(self, fn, **kwargs: Any) -> Any:
        try:
            return fn(**kwargs)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise AIUnavailable(f"Claude API rejected the credentials: {exc.message}") from exc
        except anthropic.NotFoundError as exc:
            raise AIUnavailable(f"Model or endpoint not found ({self.model}): {exc.message}") from exc
        except anthropic.RateLimitError as exc:
            raise AIError(f"Claude API rate limit: {exc.message}") from exc
        except anthropic.BadRequestError as exc:
            raise AIError(f"Claude API bad request: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise AIError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise AIError(f"Could not reach Claude API: {exc}") from exc
        except pydantic.ValidationError as exc:
            raise AIError(f"Claude returned output that failed validation: {exc.errors()[:2]}") from exc

    def _parse(
        self,
        *,
        purpose: str,
        prompt: str,
        user_content: str,
        output_model: type[T],
        effort_key: str,
        run_id: int | None,
        opportunity_id: int | None,
    ) -> T:
        self.ensure_budget()
        message = self._call(
            self.client.beta.messages.parse,
            model=self.model,
            max_tokens=16000,
            system=self._system(prompt),
            messages=[{"role": "user", "content": user_content}],
            output_format=output_model,
            **self._model_params(effort_key),
        )
        ok = message.stop_reason not in ("refusal", "max_tokens")
        self._record(purpose, message, run_id, opportunity_id, ok=ok)
        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            raise AIRefused(f"Claude declined the request ({getattr(details, 'category', None)})")
        if message.stop_reason == "max_tokens":
            raise AIError("Claude response was truncated (max_tokens)")
        if message.parsed_output is None:
            raise AIError("Claude returned no structured output")
        return message.parsed_output

    # ------------------------------------------------------------------ tasks
    def analyze_job(self, job: dict[str, Any], signals: dict[str, Any], run_id: int | None) -> JobAnalysis:
        description = job.get("description") or ""
        if len(description) > MAX_POSTING_CHARS:
            description = description[:MAX_POSTING_CHARS] + "\n[Description truncated for length]"
        salary = job.get("salary_text") or _salary_line(job)
        user = (
            f"Today's date: {date.today().isoformat()}\n\n"
            "<deterministic_signals>\n"
            f"{json.dumps(signals, indent=2, default=str, ensure_ascii=False)}\n"
            "</deterministic_signals>\n\n"
            f"<job_posting source=\"{job.get('source')}\" url=\"{job.get('source_url')}\">\n"
            f"Title: {job.get('title')}\n"
            f"Company: {job.get('company_name') or 'Not stated'}\n"
            f"Company website: {job.get('company_website') or 'Not stated'}\n"
            f"Location: {job.get('location_text') or 'Not stated'}\n"
            f"Location restrictions: {', '.join(job.get('location_restrictions') or []) or 'None listed'}\n"
            f"Work arrangement (detected): {job.get('remote_type')}\n"
            f"Employment type (detected): {job.get('employment_type')}\n"
            f"Salary: {salary or 'Not stated'}\n"
            f"Posted: {job.get('posted_at') or 'Unknown'}\n"
            f"Apply URL: {job.get('apply_url') or 'Not stated'}\n"
            f"Apply email: {job.get('apply_email') or 'Not stated'}\n\n"
            f"Description:\n{description}\n"
            "</job_posting>\n\n"
            "Analyse this posting for the candidate following your instructions."
        )
        return self._parse(
            purpose="analyze", prompt="analyze_job", user_content=user, output_model=JobAnalysis,
            effort_key="analyze", run_id=run_id, opportunity_id=job.get("id"),
        )

    def extract_posting(self, url: str, page_text: str, run_id: int | None) -> ExtractedPosting:
        user = f"<page url=\"{url}\">\n{page_text[:MAX_POSTING_CHARS]}\n</page>\n\nExtract the posting fields."
        return self._parse(
            purpose="extract", prompt="extract_job", user_content=user, output_model=ExtractedPosting,
            effort_key="extract", run_id=run_id, opportunity_id=None,
        )

    def generate_materials(self, job: dict[str, Any], analysis: dict[str, Any], run_id: int | None, feedback: str | None = None) -> TailoredMaterials:
        user = (
            f"<job_posting url=\"{job.get('source_url')}\">\n"
            f"Title: {job.get('title')}\nCompany: {job.get('company_name') or 'Not stated'}\n\n"
            f"{(job.get('description') or '')[:MAX_POSTING_CHARS]}\n</job_posting>\n\n"
            "<fit_analysis>\n"
            f"Matched requirements: {json.dumps(analysis.get('matched_requirements', []), ensure_ascii=False)}\n"
            f"Missing requirements (do not claim these): {json.dumps(analysis.get('missing_requirements', []), ensure_ascii=False)}\n"
            "</fit_analysis>\n"
        )
        if feedback:
            user += f"\n<fact_check_feedback>\nA previous draft contained unsupported claims. Remove or correct them:\n{feedback}\n</fact_check_feedback>\n"
        user += "\nWrite the tailored CV and cover letter."
        return self._parse(
            purpose="materials", prompt="tailor_materials", user_content=user, output_model=TailoredMaterials,
            effort_key="materials", run_id=run_id, opportunity_id=job.get("id"),
        )

    def verify_materials(self, job: dict[str, Any], materials: TailoredMaterials, run_id: int | None) -> MaterialsVerification:
        user = (
            f"<job_posting>\nTitle: {job.get('title')}\nCompany: {job.get('company_name')}\n\n"
            f"{(job.get('description') or '')[:MAX_POSTING_CHARS]}\n</job_posting>\n\n"
            f"<tailored_cv>\n{materials.cv_markdown}\n</tailored_cv>\n\n"
            f"<cover_letter>\n{materials.cover_letter_markdown}\n</cover_letter>\n\n"
            "Fact-check both documents against the CV."
        )
        return self._parse(
            purpose="verify", prompt="verify_materials", user_content=user, output_model=MaterialsVerification,
            effort_key="verify", run_id=run_id, opportunity_id=job.get("id"),
        )

    def discover_leads(self, queries: list[str], run_id: int | None) -> list[dict[str, str]]:
        """Claude web search -> list of {url, title, company, location_note, why_relevant}."""
        max_searches = int(self.cfg.get("ai.web_discovery.max_searches_per_run", 8))
        search_tool: dict[str, Any] = {
            "type": "web_search_20250305" if self.model.startswith("claude-haiku") else "web_search_20260209",
            "name": "web_search",
            "max_uses": max_searches,
            "user_location": {"type": "approximate", "city": "Kathmandu", "country": "NP", "timezone": "Asia/Kathmandu"},
        }
        tools = [search_tool, _LEADS_TOOL]
        user = (
            f"Today's date: {date.today().isoformat()}\n\n"
            "Example queries you can start from (adapt and vary them):\n"
            + "\n".join(f"- {q}" for q in queries)
            + "\n\nFind current openings and record them with record_job_leads."
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        leads: list[dict[str, str]] = []
        searches_used = 0
        for _ in range(8):
            self.ensure_budget()
            message = self._call(
                self.client.beta.messages.create,
                model=self.model,
                max_tokens=16000,
                system=self._system("web_discovery"),
                messages=messages,
                tools=tools,
                **self._model_params("discovery"),
            )
            self._record("discovery", message, run_id, None, ok=message.stop_reason != "refusal")
            server_tools = getattr(message.usage, "server_tool_use", None)
            searches_used += (getattr(server_tools, "web_search_requests", 0) or 0) if server_tools else 0
            if message.stop_reason == "refusal":
                raise AIRefused("Claude declined the discovery request")
            calls = [b for b in message.content if b.type == "tool_use" and b.name == "record_job_leads"]
            for call in calls:
                leads.extend(call.input.get("leads", []))
            if message.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": message.content})
                continue
            if message.stop_reason == "tool_use" and calls and searches_used < max_searches:
                messages.append({"role": "assistant", "content": message.content})
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": c.id, "content": f"Recorded {len(c.input.get('leads', []))} leads."}
                        for c in calls
                    ],
                })
                continue
            break
        return leads


class OpenAICompatibleService(ClaudeService):
    """Any OpenAI-compatible chat API (OmniRoute, OpenRouter, Ollama, LM Studio, ...).

    Same prompts and output models as Claude; JSON is requested in the prompt and validated
    with Pydantic (one automatic retry with the validation error). Web discovery is not supported.
    """

    def __init__(self, cfg: Config, env: Env, profile: CandidateProfile):
        if not (env.openai_base_url and env.openai_model):
            raise AIUnavailable("AI_PROVIDER=openai_compatible needs OPENAI_BASE_URL and OPENAI_MODEL in .env")
        self.cfg = cfg
        self.env = env
        self.profile = profile
        self.model = env.openai_model
        # Free tiers are metered per model, so a chain lets the run continue on the next model
        # instead of stopping the moment the primary one runs out for the day.
        self.models: list[str] = list(dict.fromkeys(
            [env.openai_model] + [str(m) for m in (cfg.get("ai.model_fallbacks") or [])]
        ))
        self.url = env.openai_base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {env.openai_api_key}"} if env.openai_api_key else {}
        self.http = httpx.Client(timeout=300, headers=headers)
        self.limiter = RateLimiter(float(cfg.get("ai.max_requests_per_minute", 10) or 0))
        self.max_retries = int(cfg.get("ai.max_retries", 4))

    def _record_openai(self, purpose: str, data: dict[str, Any], run_id: int | None, opportunity_id: int | None, ok: bool) -> None:
        usage = data.get("usage") or {}
        model = str(data.get("model") or self.model)
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
        cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
        price = self._price(model)
        cost = (
            max(0, input_tokens - cached) * price["input"]
            + cached * price["input"] * 0.25
            + output_tokens * price["output"]
        ) / 1_000_000
        db.execute(
            "INSERT INTO ai_usage (run_id, opportunity_id, purpose, model, input_tokens, output_tokens,"
            " cache_read_tokens, cost_usd, ok) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (run_id, opportunity_id, purpose, model, input_tokens, output_tokens, cached, round(cost, 6), ok),
        )

    @staticmethod
    def _retry_after_seconds(response: httpx.Response, attempt: int) -> float:
        """Honour Retry-After when the server sends one, else exponential backoff with jitter."""
        header = response.headers.get("retry-after", "")
        if header.strip().isdigit():
            return min(120.0, float(header.strip()))
        body = response.text[:2000]
        marker = '"retryDelay":"'                     # Google returns this inside the error payload
        if marker in body:
            value = body.split(marker, 1)[1].split('"', 1)[0].removesuffix("s")
            try:
                return min(120.0, float(value))
            except ValueError:
                pass
        return min(60.0, (2.0 ** attempt) + random.uniform(0, 1.0))

    @staticmethod
    def _is_daily_quota(text: str) -> bool:
        """Tell a per-day quota ('come back tomorrow') from a per-minute one ('slow down')."""
        return "PerDay" in text or "per day" in text.lower()

    def _today(self) -> date:
        return datetime.now(ZoneInfo(str(self.cfg.get("timezone", "UTC")))).date()

    def available_models(self) -> list[str]:
        today = self._today()
        return [m for m in self.models if _exhausted_today.get(m) != today]

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        """Send one chat completion, walking down the model chain past exhausted models."""
        models = self.available_models()
        if not models:
            raise AIUnavailable(
                f"Every configured model is out of quota for today ({', '.join(self.models)}). "
                "Add another model to ai.model_fallbacks, or switch to a paid key."
            )
        last_error = ""
        for model in models:
            try:
                data = self._post_one({**body, "model": model})
            except _QuotaExhausted as exc:
                _exhausted_today[model] = self._today()
                last_error = str(exc)
                log.warning("Model %s is out of quota for today - falling back to the next model", model)
                continue
            self.model = model          # what actually answered, for usage records and the dashboard
            return data
        raise AIUnavailable(f"All models are out of quota for today. Last error: {last_error[:300]}")

    def _post_one(self, body: dict[str, Any]) -> dict[str, Any]:
        last_status, last_text = 0, ""
        for attempt in range(self.max_retries + 1):
            self.limiter.acquire()
            try:
                response = self.http.post(self.url, json=body)
                if response.status_code == 400 and "response_format" in body:
                    # Some providers reject json_object mode; drop it and rely on the prompt.
                    body = {k: v for k, v in body.items() if k != "response_format"}
                    self.limiter.acquire()
                    response = self.http.post(self.url, json=body)
            except httpx.HTTPError as exc:
                if attempt < self.max_retries:
                    time.sleep(min(30.0, 2.0 ** attempt))
                    continue
                raise AIError(f"Could not reach AI API at {self.url}: {type(exc).__name__}") from exc

            if response.status_code in (401, 403):
                raise AIUnavailable(f"AI API rejected the key (HTTP {response.status_code})")
            if response.status_code == 404:
                raise _QuotaExhausted(f"Model {body.get('model')} is not available on this key (HTTP 404)")
            if response.status_code in RETRYABLE_STATUS:
                last_status, last_text = response.status_code, response.text[:400]
                # A daily quota will not clear by waiting, so stop and let the caller try another model.
                if response.status_code == 429 and self._is_daily_quota(response.text):
                    raise _QuotaExhausted(f"Daily quota exhausted for {body.get('model')}")
                if attempt < self.max_retries:
                    delay = self._retry_after_seconds(response, attempt)
                    self.limiter.pause_until(delay)
                    log.info("AI API busy (HTTP %s) - retrying in %.1fs (attempt %s/%s)",
                             last_status, delay, attempt + 1, self.max_retries)
                    time.sleep(delay)
                    continue
                raise _QuotaExhausted(f"{body.get('model')} still returning HTTP {last_status} after "
                                      f"{self.max_retries} retries: {last_text}")
            if response.status_code >= 400:
                raise AIError(f"AI API error {response.status_code}: {response.text[:300]}")
            return response.json()
        raise AIError(f"AI API error {last_status}: {last_text}")

    def _parse(self, *, purpose: str, prompt: str, user_content: str, output_model: type[T],
               effort_key: str, run_id: int | None, opportunity_id: int | None) -> T:
        schema = json.dumps(output_model.model_json_schema(), ensure_ascii=False)
        system = (
            f"{load_prompt(prompt)}\n\n{self.profile.prompt_block()}\n\n"
            "Respond with ONLY one JSON object (no markdown, no commentary) that validates against this JSON Schema:\n"
            f"{schema}"
        )
        messages: list[dict[str, str]] = [{"role": "system", "content": system}, {"role": "user", "content": user_content}]
        last_error = "no response"
        for _ in range(2):
            data = self._post({"messages": messages, "temperature": 0.2, "response_format": {"type": "json_object"}})
            text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            try:
                result = output_model.model_validate_json(_extract_json(text))
                self._record_openai(purpose, data, run_id, opportunity_id, ok=True)
                return result
            except (pydantic.ValidationError, ValueError) as exc:
                self._record_openai(purpose, data, run_id, opportunity_id, ok=False)
                last_error = str(exc)[:500]
                messages += [
                    {"role": "assistant", "content": text[:4000]},
                    {"role": "user", "content": f"That was not valid JSON for the schema: {last_error}\nReply with the corrected JSON object only."},
                ]
        raise AIError(f"Model did not return valid JSON: {last_error}")

    def discover_leads(self, queries: list[str], run_id: int | None) -> list[dict[str, str]]:
        return []


def _extract_json(text: str) -> str:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No JSON object in response")
    return text[start:end + 1]


def create_ai_service(cfg: Config, env: Env, profile: CandidateProfile) -> ClaudeService:
    if env.ai_provider == "openai_compatible":
        return OpenAICompatibleService(cfg, env, profile)
    return ClaudeService(cfg, env, profile)


def _salary_line(job: dict[str, Any]) -> str:
    if job.get("salary_min") is None and job.get("salary_max") is None:
        return ""
    parts = [str(int(v)) for v in (job.get("salary_min"), job.get("salary_max")) if v is not None]
    return f"{job.get('salary_currency') or ''} {' - '.join(parts)} per {job.get('salary_period') or 'period'}".strip()
