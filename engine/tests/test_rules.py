from datetime import UTC, datetime, timedelta

from app.pipeline.rules import (
    EligibilityResult,
    assess_nepal_eligibility,
    assess_relevance,
    assess_seniority,
    combine_eligibility,
    detect_apply_method,
    required_years,
    staleness_reason,
)


def elig(cfg, **overrides):
    params = {"title": "Frontend Developer", "location_text": None, "location_restrictions": [], "remote_type": "remote", "description": ""}
    params.update(overrides)
    return assess_nepal_eligibility(cfg=cfg, **params)


def test_relevance(cfg):
    assert assess_relevance("Junior Frontend Developer", "", cfg)[0]
    assert assess_relevance("Software Engineer I", "", cfg)[0]
    assert assess_relevance("Engineer", "We use React and TypeScript", cfg)[0]
    assert not assess_relevance("Senior Sales Executive", "", cfg)[0]
    assert not assess_relevance("React Native Developer", "", cfg)[0]
    assert not assess_relevance("ASP.NET Developer", "", cfg)[0]
    assert not assess_relevance("Warehouse Associate", "", cfg)[0]
    assert assess_relevance("Trust & Safety Software Engineer", "", cfg)[0]  # 'rust' must not match 'trust'


def test_seniority(cfg):
    assert assess_seniority("Senior Frontend Engineer", "", cfg).blocked
    assert assess_seniority("Staff Software Engineer", "", cfg).blocked
    assert assess_seniority("Software Engineer III", "", cfg).blocked
    junior = assess_seniority("Junior Frontend Developer", "Requires 1+ years of experience with React.", cfg)
    assert not junior.blocked and junior.required_years == 1
    three = assess_seniority("Frontend Developer", "You have 3+ years of professional experience building web apps.", cfg)
    assert three.blocked and three.required_years == 3
    assert not assess_seniority("Frontend Developer", "5+ years of experience is a plus.", cfg).blocked


def test_required_years_range():
    assert required_years("We expect 1-3 years of working experience.") == 1
    assert required_years("At least 2 years experience with Vue") == 2
    assert required_years("Founded 10 years ago.") is None


def test_eligibility_structured_restrictions(cfg):
    assert elig(cfg, location_restrictions=["USA"]).status == "not_eligible"
    assert elig(cfg, location_restrictions=["Worldwide"]).status == "eligible"
    assert elig(cfg, location_restrictions=["Asia"]).status == "likely_eligible"
    assert elig(cfg, location_restrictions=["India", "Nepal"]).status == "eligible"


def test_eligibility_onsite(cfg):
    local = elig(cfg, remote_type="onsite", location_text="Kathmandu, Nepal")
    assert local.status == "eligible" and local.onsite_in_nepal
    assert elig(cfg, remote_type="onsite", location_text="Berlin, Germany").status == "not_eligible"
    assert elig(cfg, remote_type="hybrid", location_text="London, UK").status == "not_eligible"


def test_eligibility_text(cfg):
    assert elig(cfg, description="This role is US only.").status == "not_eligible"
    assert elig(cfg, description="Contact us only through the application form.").status == "unclear"
    assert elig(cfg, description="Unfortunately we cannot hire candidates in Nepal.").status == "not_eligible"
    assert elig(cfg, description="We hire from anywhere in the world.").status == "eligible"
    assert elig(cfg, description="Work from anywhere, but you must be authorized to work in the United States.").status == "unclear"
    assert elig(cfg, description="Our team is spread across APAC.").status == "likely_eligible"
    assert elig(cfg, location_text="Remote - US").status == "not_eligible"
    assert elig(cfg).status == "unclear"


def test_combine_eligibility():
    explicit_no = EligibilityResult("not_eligible", "US only", explicit=True)
    assert combine_eligibility(explicit_no, "eligible", "worldwide")[0] == "unclear"
    assert combine_eligibility(explicit_no, "unclear", "")[0] == "not_eligible"
    assert combine_eligibility(EligibilityResult("unclear", "none"), "likely_eligible", "APAC")[0] == "likely_eligible"
    assert combine_eligibility(EligibilityResult("eligible", "Nepal listed"), "unclear", "")[0] == "eligible"
    assert combine_eligibility(EligibilityResult("eligible", "Nepal listed"), "likely_eligible", "Asia")[0] == "likely_eligible"
    assert combine_eligibility(EligibilityResult("unclear", "none"), "not_eligible", "EU only")[0] == "not_eligible"


def test_apply_method(cfg):
    assert detect_apply_method("https://boards.greenhouse.io/acme/jobs/1", None, "", cfg) == ("ats_form", None)
    assert detect_apply_method("https://www.linkedin.com/jobs/view/1", None, "", cfg) == ("login_required", None)
    assert detect_apply_method("https://acme.com/careers/apply", None, "", cfg) == ("external_form", None)
    assert detect_apply_method("mailto:hr@acme.com?subject=Job", None, "", cfg) == ("email", "hr@acme.com")
    assert detect_apply_method(None, None, "To apply, send your CV to jobs@acme.com. Thanks!", cfg) == ("email", "jobs@acme.com")
    assert detect_apply_method(None, None, "Questions? privacy@acme.com. Apply via privacy@acme.com", cfg)[0] == "unclear"
    assert detect_apply_method(None, None, "Great job", cfg) == ("unclear", None)


def test_staleness():
    now = datetime(2026, 9, 14, tzinfo=UTC)
    assert staleness_reason(now - timedelta(days=40), None, 30, now)
    assert staleness_reason(None, now - timedelta(days=1), 30, now)
    assert staleness_reason(now - timedelta(days=5), None, 30, now) is None
