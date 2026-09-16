"""Data models: normalized postings from sources and Claude structured-output schemas."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RemoteType = Literal["remote", "hybrid", "onsite", "unknown"]
EmploymentType = Literal["full_time", "part_time", "internship", "contract", "freelance", "temporary", "unspecified"]
Eligibility = Literal["eligible", "likely_eligible", "unclear", "not_eligible"]
ApplyMethod = Literal["email", "ats_form", "external_form", "login_required", "unclear"]
LegitimacyVerdict = Literal["legitimate", "uncertain", "suspicious"]


# ---------------------------------------------------------------------------
# Source-level models
# ---------------------------------------------------------------------------
class NormalizedJob(BaseModel):
    """A posting as produced by any source adapter, before storage."""

    model_config = ConfigDict(extra="ignore")

    source: str
    source_external_id: str | None = None
    source_url: str
    title: str
    company_name: str | None = None
    company_website: str | None = None
    location_text: str | None = None
    location_restrictions: list[str] = Field(default_factory=list)
    remote_type: str = "unknown"
    employment_type: str = "unspecified"
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    salary_text: str | None = None
    description: str = ""
    description_quality: str = "full"
    apply_url: str | None = None
    apply_email: str | None = None
    posted_at: datetime | None = None
    expires_at: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


@dataclass
class DiscoveredLink:
    url: str
    via: str
    title: str | None = None
    snippet: str | None = None


@dataclass
class TaskResult:
    status: str = "ok"  # ok | failed | blocked | skipped
    http_status: int | None = None
    jobs: list[NormalizedJob] = field(default_factory=list)
    links: list[DiscoveredLink] = field(default_factory=list)
    error: str | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# Claude structured outputs
# ---------------------------------------------------------------------------
class ComponentScore(BaseModel):
    score: int = Field(description="Integer from 0 to 100 following the rubric")
    reasoning: str = Field(description="One or two sentences citing concrete evidence")


class RequirementMatch(BaseModel):
    requirement: str = Field(description="A requirement or responsibility from the job posting")
    cv_evidence: str = Field(description="Verbatim quote from the candidate CV that supports the match")
    strength: Literal["strong", "partial"]


class JobAnalysis(BaseModel):
    is_real_job_posting: bool = Field(description="False for listing pages, articles, expired or placeholder pages")
    role_summary: str
    role_category: Literal[
        "frontend", "react", "software_engineer", "full_stack", "web_developer", "qa_automation",
        "technical_support", "devops", "cloud_platform", "other_adjacent", "unrelated",
    ]
    seniority_level: Literal["intern", "entry", "junior", "mid", "senior", "lead_or_above", "unspecified"]
    required_years_experience: int | None = Field(description="Minimum years explicitly required, null if not stated")
    seniority_mismatch: bool = Field(description="True if the role needs significantly more experience than the candidate has")
    nepal_eligibility: Eligibility
    nepal_eligibility_evidence: str = Field(description="Verbatim quote(s) from the posting that justify the eligibility verdict, or 'No location information in posting'")
    remote_type: RemoteType
    employment_type: EmploymentType
    compensation_summary: str = Field(description="Stated pay with currency and period, or 'Not stated'")
    application_method: ApplyMethod
    application_instructions: str = Field(description="How to apply, quoted or closely paraphrased from the posting; empty if unclear")
    application_email: str | None = Field(description="Application email address explicitly given in the posting, else null")
    legitimacy_verdict: LegitimacyVerdict
    legitimacy_concerns: list[str]
    matched_requirements: list[RequirementMatch]
    missing_requirements: list[str] = Field(description="Requirements the CV does not show evidence for")
    skills_match: ComponentScore
    experience_fit: ComponentScore
    role_fit: ComponentScore
    growth_value: ComponentScore
    summary_for_candidate: str = Field(description="2-3 sentence plain-language summary of fit and risks")


class ExtractedPosting(BaseModel):
    is_job_posting: bool = Field(description="True only if the page describes one specific open position")
    title: str
    company_name: str | None
    company_website: str | None
    location_text: str | None
    remote_type: RemoteType
    employment_type: EmploymentType
    salary_text: str | None
    apply_url: str | None
    apply_email: str | None
    posted_date: str | None = Field(description="YYYY-MM-DD if stated on the page, else null")


class TailoredMaterials(BaseModel):
    cv_markdown: str
    cover_letter_markdown: str
    tailoring_notes: list[str] = Field(description="What was emphasised or reordered and why")


class UnsupportedClaim(BaseModel):
    claim: str
    location: Literal["cv", "cover_letter"]
    reason: str


class MaterialsVerification(BaseModel):
    all_claims_supported: bool
    unsupported_claims: list[UnsupportedClaim]
