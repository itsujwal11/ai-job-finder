"""Candidate profile: the CV (source of truth) and job-search preferences."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .config import PROFILE_DIR

CV_CANDIDATES = ("cv.md", "cv.txt", "cv.pdf", "cv.docx")
MIN_CV_CHARS = 200


class ProfileError(RuntimeError):
    pass


@dataclass(frozen=True)
class CandidateProfile:
    cv_text: str
    cv_path: Path
    preferences: dict[str, Any]
    digest: str

    @property
    def name(self) -> str:
        return self.preferences.get("candidate", {}).get("name") or "Candidate"

    def pref(self, path: str, default: Any = None) -> Any:
        node: Any = self.preferences
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def experience_months(self, today: date | None = None) -> int | None:
        start = self.pref("candidate.professional_experience_start")
        if not start:
            return None
        today = today or date.today()
        year, month = (int(p) for p in str(start).split("-")[:2])
        return max(0, (today.year - year) * 12 + today.month - month)

    def links(self) -> dict[str, str]:
        return {k: v for k, v in (self.pref("candidate.links") or {}).items() if v}

    def prompt_block(self, today: date | None = None) -> str:
        """Stable candidate context placed in the (cached) system prompt."""
        months = self.experience_months(today)
        prefs_yaml = yaml.safe_dump(self.preferences, sort_keys=True, allow_unicode=True)
        exp_line = (
            f"Professional experience to date: about {months} months (first role started "
            f"{self.pref('candidate.professional_experience_start')}, per the CV)."
            if months is not None
            else "Professional experience: see CV dates."
        )
        return (
            "<candidate_cv>\n"
            f"{self.cv_text.strip()}\n"
            "</candidate_cv>\n\n"
            f"<experience_summary>{exp_line}</experience_summary>\n\n"
            "<candidate_preferences>\n"
            f"{prefs_yaml.strip()}\n"
            "</candidate_preferences>"
        )


def _read_cv(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        return path.read_text(encoding="utf-8")
    if suffix == ".pdf":
        from pypdf import PdfReader

        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    if suffix == ".docx":
        import docx

        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    raise ProfileError(f"Unsupported CV format: {path.name}")


def load_profile(profile_dir: Path = PROFILE_DIR) -> CandidateProfile:
    cv_path = next((profile_dir / name for name in CV_CANDIDATES if (profile_dir / name).is_file()), None)
    if cv_path is None:
        raise ProfileError(f"No CV found. Put your CV at {profile_dir / 'cv.md'} (or cv.txt / cv.pdf / cv.docx).")
    cv_text = _read_cv(cv_path).strip()
    if len(cv_text) < MIN_CV_CHARS:
        raise ProfileError(f"CV at {cv_path} is empty or too short to analyse ({len(cv_text)} chars).")

    prefs_path = profile_dir / "preferences.yaml"
    preferences: dict[str, Any] = {}
    if prefs_path.is_file():
        preferences = yaml.safe_load(prefs_path.read_text(encoding="utf-8")) or {}

    digest = hashlib.sha256(
        (cv_text + yaml.safe_dump(preferences, sort_keys=True)).encode("utf-8")
    ).hexdigest()[:16]
    return CandidateProfile(cv_text=cv_text, cv_path=cv_path, preferences=preferences, digest=digest)
