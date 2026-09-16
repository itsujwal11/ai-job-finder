from app.pipeline.legitimacy import combine_legitimacy, heuristic_legitimacy
from app.pipeline.policy import MANUAL_REASON, blocking_reasons, decide
from app.pipeline.scoring import compensation_points, compute_match_score

PADDING = " We build web products for customers and collaborate closely as a team." * 6


def test_ats_posting_is_legitimate(cfg):
    job = {"source": "greenhouse", "source_url": "https://boards.greenhouse.io/acme/jobs/1",
           "apply_url": "https://boards.greenhouse.io/acme/jobs/1", "company_name": "Acme",
           "title": "Frontend Developer", "description": "Build React apps." + PADDING}
    result = heuristic_legitimacy(job, cfg)
    assert result.verdict == "legitimate" and result.score >= 75


def test_fee_request_is_suspicious(cfg):
    job = {"source": "web", "source_url": "https://randomsite.xyz/job", "company_name": "Global Hiring",
           "title": "Data Entry", "description": "Pay a registration fee of $50 to start. Contact on WhatsApp." + PADDING}
    result = heuristic_legitimacy(job, cfg)
    assert result.verdict == "suspicious" and result.has_strong_flag


def test_free_email_short_post_is_uncertain(cfg):
    job = {"source": "web", "source_url": "https://smallco.com/jobs", "company_name": "Small Co",
           "title": "Web Developer", "apply_email": "smallcohr@gmail.com", "description": "Send CV."}
    assert heuristic_legitimacy(job, cfg).verdict == "uncertain"


def test_young_domain_penalised(cfg):
    job = {"source": "web", "source_url": "https://newco.io/j", "company_name": "NewCo", "title": "Dev", "description": PADDING}
    assert heuristic_legitimacy(job, cfg, domain_age_days=10).verdict == "suspicious"


def test_ai_can_only_downgrade_legitimacy(cfg):
    job = {"source": "greenhouse", "source_url": "https://boards.greenhouse.io/acme/jobs/1", "company_name": "Acme",
           "title": "Frontend Developer", "description": PADDING}
    heuristic = heuristic_legitimacy(job, cfg)
    assert combine_legitimacy(heuristic, "suspicious", ["asks for fee"]).verdict == "suspicious"
    assert combine_legitimacy(heuristic, "legitimate", []).verdict == "legitimate"


def test_match_score_weights(cfg):
    weights = cfg.get("scoring.weights")
    score, breakdown = compute_match_score(
        {"skills_match": {"score": 90}, "experience_fit": {"score": 80}, "role_fit": {"score": 100},
         "eligibility": 100, "compensation": 60, "growth_value": {"score": 75}},
        weights,
    )
    assert score == 86
    assert breakdown["skills_match"]["points"] == 31.5


def test_compensation_points():
    allow = ["internship", "freelance", "part_time"]
    assert compensation_points(None, None, "full_time", 20000, allow)[0] == 60
    assert compensation_points(25000, 40000, "full_time", 20000, allow)[0] == 100
    assert compensation_points(15000, 25000, "full_time", 20000, allow)[0] == 85
    assert compensation_points(None, 12000, "internship", 20000, allow)[0] == 75
    assert compensation_points(5000, 8000, "full_time", 20000, allow)[0] == 25


def _decide(**overrides):
    params = dict(score=90, blockers=[], eligibility="eligible", legitimacy_verdict="legitimate", remote_type="remote",
                  apply_method="email", apply_email="jobs@acme.com", auto_apply_min=85, approval_min=70, auto_apply_enabled=False)
    params.update(overrides)
    return decide(**params)


def test_never_rules_block():
    reasons = blocking_reasons(is_real_posting=True, seniority_reasons=[], ai_seniority_mismatch=False,
                               eligibility="not_eligible", eligibility_evidence="US only", legitimacy_verdict="legitimate",
                               prior_application=None, remote_type="remote", onsite_in_nepal=False)
    assert reasons
    assert _decide(blockers=reasons).recommendation == "blocked"
    senior = blocking_reasons(is_real_posting=True, seniority_reasons=[], ai_seniority_mismatch=True, eligibility="eligible",
                              eligibility_evidence="", legitimacy_verdict="legitimate", prior_application={"id": 3},
                              remote_type="onsite", onsite_in_nepal=False)
    assert len(senior) == 3


def test_thresholds():
    assert _decide(score=65).recommendation == "ignore"
    assert _decide(score=78).recommendation == "approval_required"
    auto = _decide(score=90)
    assert auto.recommendation == "auto_apply_candidate" and auto.review_status == "auto_apply_disabled"


def test_high_score_but_manual_or_unclear():
    manual = _decide(apply_method="ats_form", apply_email=None)
    assert manual.recommendation == "manual_apply" and manual.auto_apply_blockers == [MANUAL_REASON]
    assert _decide(eligibility="unclear", apply_method="ats_form").recommendation == "approval_required"
    assert _decide(apply_method="unclear", apply_email=None).recommendation == "approval_required"
    assert _decide(remote_type="onsite", apply_method="ats_form").recommendation == "approval_required"
    assert _decide(legitimacy_verdict="uncertain").recommendation == "approval_required"
