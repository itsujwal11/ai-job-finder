"""Nepali boards, the Ojiiz API adapter and the rule-based pre-score."""
from app.pipeline.prescore import prescore
from app.pipeline.rules import assess_relevance, is_nepal_local
from app.services.tasks import page_source
from app.sources import local_boards, ojiiz

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


OJIIZ_PAGE = {
    "success": True,
    "data": {
        "data": [
            {"_id": "abc123", "jobTitle": "Junior React Developer", "jobHeading": "Individual Hiring",
             "jobDetail": "<p>Build UIs with React</p>", "jobPricing": "$2,000 - $3,000 USD",
             "jobCategory": "Web Development", "jobType": "job", "jobDate": "2026-09-19T05:12:54.014Z",
             "isOpen": True, "tags": ["Fixed Cost"]},
            {"_id": "closed1", "jobTitle": "Closed Role", "isOpen": False, "jobCategory": "Web Development"},
        ],
        "pagination": {"page": 1, "limit": 100, "total": 2, "totalPages": 1},
    },
}


def test_ojiiz_adapter(make_ctx):
    ctx = make_ctx([("api.ojiiz.com/api/jobs", OJIIZ_PAGE, "application/json")])
    result = ojiiz.fetch_ojiiz(
        {"url": "https://api.ojiiz.com/api/jobs", "source": "ojiiz",
         "params": {"categories": ["Web Development"], "pages": 1, "page_size": 100}},
        ctx,
    )
    assert len(result.jobs) == 1, "closed postings are dropped"
    job = result.jobs[0]
    assert job.source == "ojiiz"
    assert job.source_external_id == "abc123"
    assert job.source_url.endswith("/abc123")
    assert job.company_name is None, "company sits behind the paid unlock and is never scraped"
    assert "Build UIs with React" in job.description
    assert job.salary_text == "$2,000 - $3,000 USD"
    assert job.posted_at is not None


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
