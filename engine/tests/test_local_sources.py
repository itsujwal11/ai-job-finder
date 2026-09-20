"""Nepali boards and the rule-based pre-score."""
from app.pipeline.prescore import prescore
from app.pipeline.rules import assess_relevance, is_nepal_local
from app.services.tasks import page_source
from app.sources import free_apis, local_boards

SITEMAP_INDEX = """<?xml version="1.0"?>
<sitemapindex><sitemap><loc>https://board.test/sitemap-job_post-1.xml</loc></sitemap>
<sitemap><loc>https://board.test/sitemap-blog_post-1.xml</loc></sitemap></sitemapindex>"""

JOB_SITEMAP = """<?xml version="1.0"?>
<urlset>
  <url><loc>https://board.test/junior-frontend-developer-12</loc><lastmod>2099-01-02</lastmod></url>
  <url><loc>https://board.test/senior-frontend-developer-13</loc><lastmod>2099-01-02</lastmod></url>
  <url><loc>https://board.test/sales-officer-14</loc><lastmod>2099-01-02</lastmod></url>
  <url><loc>https://board.test/qa-engineer-15</loc><lastmod>2010-01-02</lastmod></url>
  <url><loc>https://board.test/-5331</loc><lastmod>2099-01-02</lastmod></url>
</urlset>"""

BLOG_SITEMAP = """<?xml version="1.0"?><urlset>
  <url><loc>https://board.test/blog/how-to-write-a-cv</loc><lastmod>2099-01-02</lastmod></url></urlset>"""


def test_title_from_url():
    assert local_boards.title_from_url("https://board.test/junior-php-developer-12") == "junior php developer"
    assert local_boards.title_from_url("https://board.test/jobs/qa-engineer/") == "qa engineer"
    assert local_boards.title_from_url("https://board.test/-5331") == ""


def test_local_board_filters_by_title_and_freshness(make_ctx):
    ctx = make_ctx([
        ("sitemap-job_post", JOB_SITEMAP, "application/xml"),
        ("sitemap-blog_post", BLOG_SITEMAP, "application/xml"),
        ("sitemap.xml", SITEMAP_INDEX, "application/xml"),
    ])
    result = local_boards.fetch_local_board(
        {"url": "https://board.test/sitemap.xml", "source": "board",
         "params": {"sitemap_pattern": "job_post", "max_urls": 10}},
        ctx,
    )
    urls = [link.url for link in result.links]
    assert "https://board.test/junior-frontend-developer-12" in urls
    assert "https://board.test/-5331" in urls                      # unreadable slug: fetched, judged later
    assert "https://board.test/senior-frontend-developer-13" not in urls   # seniority
    assert "https://board.test/sales-officer-14" not in urls               # not a target role
    assert "https://board.test/qa-engineer-15" not in urls                 # too old
    assert all(link.via == "local_board:board" for link in result.links)


def test_local_board_ignores_unmatched_child_sitemaps(make_ctx):
    """The blog sitemap must not be read at all - the pattern selects the job one."""
    ctx = make_ctx([
        ("sitemap-job_post", JOB_SITEMAP, "application/xml"),
        ("sitemap.xml", SITEMAP_INDEX, "application/xml"),
    ])
    result = local_boards.fetch_local_board(
        {"url": "https://board.test/sitemap.xml", "source": "board",
         "params": {"sitemap_pattern": "job_post", "max_urls": 10}},
        ctx,
    )
    assert result.status == "ok"
    assert not any("blog" in url for url in ctx.client.requested)


def test_nepal_local_relaxes_stack_exclusions(cfg):
    """A PHP role is noise worldwide but normal in Kathmandu."""
    assert assess_relevance("PHP Laravel Developer", "", cfg)[0] is False
    assert assess_relevance("PHP Laravel Developer", "", cfg, nepal_local=True)[0] is True
    # Relaxing the stack list must not let unrelated roles through.
    assert assess_relevance("Sales Executive", "", cfg, nepal_local=True)[0] is False


def test_is_nepal_local(cfg):
    assert is_nepal_local("Kathmandu, Nepal", "web", cfg) is True
    assert is_nepal_local(None, "merojob", cfg) is True
    assert is_nepal_local("Berlin, Germany", "web", cfg) is False


def test_prescore_ranks_the_queue(cfg):
    junior_react, _ = prescore(
        {"title": "Junior Frontend Developer", "description": "React, TypeScript and CSS. " * 30,
         "remote_type": "remote", "source": "greenhouse"}, cfg)
    senior_other, _ = prescore(
        {"title": "Senior Data Scientist", "description": "Build models. " * 30,
         "remote_type": "onsite", "source": "web"}, cfg)
    assert junior_react > senior_other
    assert 0 <= senior_other <= 100 and 0 <= junior_react <= 100


def test_prescore_rewards_nepal_and_eligibility(cfg):
    base = {"title": "Web Developer", "description": "HTML and CSS. " * 30, "remote_type": "onsite", "source": "merojob"}
    away, _ = prescore(dict(base, location_text="Berlin"), cfg, eligibility_status="not_eligible")
    local, _ = prescore(dict(base, location_text="Kathmandu"), cfg, eligibility_status="eligible")
    assert local > away


def test_page_source_keeps_board_attribution():
    """A link from a board must stay attributed to it, including when re-queued next run."""
    assert page_source("local_board:merojob") == "merojob"
    assert page_source("search:tavily") == "web"
    assert page_source(None) == "web"


MUSE_PAGE = {
    "page": 1,
    "page_count": 1,
    "results": [
        {
            "id": 111, "name": "Junior Frontend Engineer", "type": "external",
            "contents": "<p>Build UIs with React</p>", "publication_date": "2026-09-18T10:00:00Z",
            "company": {"name": "Acme"},
            "locations": [{"name": "Flexible / Remote"}],
            "levels": [{"name": "Entry Level"}],
            "categories": [{"name": "Software Engineering"}],
            "refs": {"landing_page": "https://www.themuse.com/jobs/acme/junior-frontend-engineer"},
        },
        {"id": 112, "name": "No Landing Page", "refs": {}},
    ],
}


def test_themuse_uses_one_based_paging(make_ctx):
    """page=0 returns nothing from this API, so the first request must ask for page 1."""
    ctx = make_ctx([("themuse.com/api/public/jobs", MUSE_PAGE, "application/json")])
    result = free_apis.fetch_themuse(
        {"url": "https://www.themuse.com/api/public/jobs", "source": "themuse",
         "params": {"pages": 3, "categories": ["Software Engineering"], "levels": ["Entry Level"]}},
        ctx,
    )
    assert "page=1" in ctx.client.requested[0]
    assert "page=0" not in ctx.client.requested[0]
    assert len(result.jobs) == 1, "a posting without a landing page is skipped"
    job = result.jobs[0]
    assert job.source == "themuse"
    assert job.remote_type == "remote"
    assert job.raw["seniority_hint"] == "Entry Level"
    # page_count is 1, so it must not keep requesting further pages.
    assert len(ctx.client.requested) == 1


def test_themuse_repeats_category_and_level_params(make_ctx):
    ctx = make_ctx([("themuse.com/api/public/jobs", MUSE_PAGE, "application/json")])
    free_apis.fetch_themuse(
        {"url": "https://www.themuse.com/api/public/jobs", "source": "themuse",
         "params": {"pages": 1, "categories": ["Software Engineering", "IT"], "levels": ["Entry Level"]}},
        ctx,
    )
    url = ctx.client.requested[0]
    assert url.count("category=") == 2
    assert "level=Entry+Level" in url or "level=Entry%20Level" in url


def test_findwork_skips_without_a_key(make_ctx, monkeypatch):
    """No key must be a visible skip, not a silent empty result or a failed run."""
    monkeypatch.delenv("FINDWORK_API_KEY", raising=False)
    ctx = make_ctx([])
    result = free_apis.fetch_findwork({"url": "https://findwork.dev/api/jobs/", "source": "findwork"}, ctx)
    assert result.status == "skipped"
    assert "FINDWORK_API_KEY" in (result.note or "")
    assert ctx.client.requested == [], "no request is made without a key"
