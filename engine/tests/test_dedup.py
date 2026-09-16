from app.pipeline.dedup import canonicalize_url, fingerprint, title_similarity, url_hash
from app.pipeline.normalize import norm_company, norm_title


def test_canonical_url_strips_tracking_and_normalizes():
    url = "http://www.Example.com/jobs/123/?utm_source=x&gh_jid=5&ref=abc#top"
    assert canonicalize_url(url) == "https://example.com/jobs/123?gh_jid=5"
    assert url_hash(url) == url_hash("https://example.com/jobs/123?gh_jid=5")


def test_query_order_does_not_matter():
    assert canonicalize_url("https://a.com/x?b=2&a=1") == canonicalize_url("https://a.com/x?a=1&b=2")


def test_same_role_different_formatting_has_same_fingerprint():
    a = fingerprint(norm_company("Acme Inc."), norm_title("Sr. Front-End Developer (Remote)"))
    b = fingerprint(norm_company("ACME"), norm_title("Senior Frontend Developer - Remote"))
    assert a == b


def test_title_similarity():
    assert title_similarity("junior frontend developer", "frontend developer junior") == 100
    assert title_similarity("junior frontend developer", "senior data engineer") < 70
