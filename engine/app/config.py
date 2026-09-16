"""Configuration: config/config.yaml (with ${ENV} substitution) plus environment variables."""
from __future__ import annotations

import hashlib
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(os.environ.get("APP_ROOT") or Path(__file__).resolve().parents[2])
CONFIG_PATH = Path(os.environ.get("CONFIG_PATH") or ROOT / "config" / "config.yaml")
PROFILE_DIR = Path(os.environ.get("PROFILE_DIR") or ROOT / "profile")
PROMPTS_DIR = Path(os.environ.get("PROMPTS_DIR") or ROOT / "prompts")
MIGRATIONS_DIR = Path(os.environ.get("MIGRATIONS_DIR") or ROOT / "db" / "migrations")
OUTPUT_ROOT = Path(os.environ.get("OUTPUT_ROOT") or ROOT)

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


class ConfigError(RuntimeError):
    pass


def substitute_env(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        value = os.environ.get(match.group(1))
        if value:
            return value
        return match.group(2) if match.group(2) is not None else ""

    return _ENV_PATTERN.sub(replace, text)


class Config:
    """Thin read-only wrapper around the parsed YAML with dotted-path lookup."""

    def __init__(self, data: dict[str, Any], digest: str):
        self.data = data
        self.digest = digest

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def terms(self, path: str) -> list[str]:
        return [str(t).lower() for t in (self.get(path) or [])]


def validate_config(data: dict[str, Any]) -> None:
    errors: list[str] = []
    approval = data.get("thresholds", {}).get("approval_min")
    auto = data.get("thresholds", {}).get("auto_apply_min")
    if not (isinstance(approval, int) and isinstance(auto, int) and 0 < approval < auto <= 100):
        errors.append("thresholds: require 0 < approval_min < auto_apply_min <= 100")
    weights = data.get("scoring", {}).get("weights", {})
    expected = {"skills_match", "experience_fit", "role_fit", "eligibility", "compensation", "growth_value"}
    if set(weights) != expected:
        errors.append(f"scoring.weights must define exactly {sorted(expected)}")
    elif sum(weights.values()) != 100:
        errors.append("scoring.weights must sum to 100")
    if not data.get("ai", {}).get("model"):
        errors.append("ai.model is empty")
    if errors:
        raise ConfigError("; ".join(errors))


_lock = threading.Lock()
_cache: dict[Path, tuple[float, Config]] = {}


def load_config(path: Path = CONFIG_PATH) -> Config:
    """Load config, re-reading the file whenever it changes on disk."""
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {path}") from exc
    with _lock:
        cached = _cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        text = path.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(substitute_env(text)) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
        validate_config(data)
        config = Config(data, hashlib.sha256(text.encode()).hexdigest()[:16])
        _cache[path] = (mtime, config)
        return config


def _flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _str(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


@dataclass(frozen=True)
class Env:
    database_url: str
    engine_api_token: str | None
    anthropic_api_key: str | None
    ai_provider: str
    openai_base_url: str | None
    openai_api_key: str | None
    openai_model: str | None
    auto_apply_enabled: bool
    dashboard_password: str | None
    public_base_url: str
    contact_email: str | None
    brave_api_key: str | None
    tavily_api_key: str | None
    google_cse_api_key: str | None
    google_cse_cx: str | None
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_password: str | None
    smtp_from: str | None
    smtp_starttls: bool
    notify_email_to: str | None

    @classmethod
    def load(cls) -> "Env":
        return cls(
            database_url=os.environ.get("DATABASE_URL", "postgresql://jobs:jobs@localhost:5432/jobs"),
            engine_api_token=_str("ENGINE_API_TOKEN"),
            anthropic_api_key=_str("ANTHROPIC_API_KEY"),
            ai_provider=(_str("AI_PROVIDER") or "claude").lower(),
            openai_base_url=_str("OPENAI_BASE_URL"),
            openai_api_key=_str("OPENAI_API_KEY"),
            openai_model=_str("OPENAI_MODEL"),
            auto_apply_enabled=_flag("AUTO_APPLY_ENABLED"),
            dashboard_password=_str("DASHBOARD_PASSWORD"),
            public_base_url=(_str("PUBLIC_BASE_URL") or "http://localhost:8000").rstrip("/"),
            contact_email=_str("CONTACT_EMAIL"),
            brave_api_key=_str("BRAVE_API_KEY"),
            tavily_api_key=_str("TAVILY_API_KEY"),
            google_cse_api_key=_str("GOOGLE_CSE_API_KEY"),
            google_cse_cx=_str("GOOGLE_CSE_CX"),
            telegram_bot_token=_str("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_str("TELEGRAM_CHAT_ID"),
            smtp_host=_str("SMTP_HOST"),
            smtp_port=int(os.environ.get("SMTP_PORT") or 587),
            smtp_username=_str("SMTP_USERNAME"),
            smtp_password=_str("SMTP_PASSWORD"),
            smtp_from=_str("SMTP_FROM"),
            smtp_starttls=_flag("SMTP_STARTTLS", True),
            notify_email_to=_str("NOTIFY_EMAIL_TO"),
        )

    @property
    def ai_available(self) -> bool:
        if self.ai_provider == "openai_compatible":
            return bool(self.openai_base_url and self.openai_model)
        return bool(self.anthropic_api_key)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and self.notify_email_to and (self.smtp_from or self.smtp_username))

    def search_providers(self) -> list[str]:
        providers = []
        if self.brave_api_key:
            providers.append("brave")
        if self.tavily_api_key:
            providers.append("tavily")
        if self.google_cse_api_key and self.google_cse_cx:
            providers.append("google_cse")
        return providers


def get_env() -> Env:
    return Env.load()
