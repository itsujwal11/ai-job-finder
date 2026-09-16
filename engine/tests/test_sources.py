import json

from app.sources import ats, feeds, hackernews, page
from app.sources.ats import detect_ats_board


def test_detect_ats_board():
    assert detect_ats_board("https://boards.greenhouse.io/acme/jobs/123") == ("greenhouse", "acme")
    assert detect_ats_board("https://job-boards.greenhouse.io/acme") == ("greenhouse", "acme")
    assert detect_ats_board("https://boards.greenhouse.io/embed/job_board?for=acme") == ("greenhouse", "acme")
    assert detect_ats_board("https://jobs.lever.co/acme/abc-123") == ("lever", "acme")
    assert detect_ats_board("https://jobs.eu.lever.co/acme") == ("lever", "acme")
    assert detect_ats_board("https://jobs.ashbyhq.com/acme/xyz") == ("ashby", "acme")
    assert detect_ats_board("https://acme.recruitee.com/o/frontend") == ("recruitee", "acme")
    assert detect_ats_board("https://apply.workable.com/acme/j/ABC/") == ("workable", "acme")
    assert detect_ats_board("https://jobs.smartrecruiters.com/Acme/7434") == ("smartrecruiters", "Acme")
    assert detect_ats_board("https://www.linkedin.com/jobs/view/1") is None
    assert detect_ats_board("https://boards.greenhouse.io/") is None


def test_greenhouse(make_ctx):
    payload = {"jobs": [{"id": 1, "title": "Junior Frontend Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                         "location": {"name": "Remote - Worldwide"}, "content": "&lt;p&gt;Build UIs with React&lt;/p&gt;",
                         "updated_at": "2026-09-10T10:00:00-04:00"}]}
    ctx = make_ctx([("boards-api.greenhouse.io/v1/boards/acme/jobs", payload, "application/json")])
    job = ats.fetch_greenhouse({"params": {"slug": "acme"}}, ctx).jobs[0]
    assert job.remote_type == "remote"
    assert job.description == "Build UIs with React"
    assert job.source_external_id == "acme:1" and job.company_name == "Acme"


def test_lever(make_ctx):
    payload = [{"id": "abc", "text": "Frontend Developer", "hostedUrl": "https://jobs.lever.co/acme/abc",
                "applyUrl": "https://jobs.lever.co/acme/abc/apply", "categories": {"location": "Kathmandu", "commitment": "Full-time"},
                "workplaceType": "hybrid", "descriptionPlain": "We build.", "lists": [{"text": "Requirements", "content": "<li>React</li>"}],
                "additionalPlain": "", "createdAt": 1757000000000}]
    ctx = make_ctx([("api.lever.co/v0/postings/acme", payload, "application/json")])
    job = ats.fetch_lever({"params": {"slug": "acme"}}, ctx).jobs[0]
    assert job.remote_type == "hybrid" and job.employment_type == "full_time"
    assert "React" in job.description and job.apply_url.endswith("/apply")


def test_ashby(make_ctx):
    payload = {"jobs": [{"id": "x", "title": "Software Engineer Intern", "jobUrl": "https://jobs.ashbyhq.com/acme/x",
                         "applyUrl": "https://jobs.ashbyhq.com/acme/x/application", "location": "Remote", "isRemote": True,
                         "employmentType": "Intern", "descriptionPlain": "Learn a lot", "publishedAt": "2026-09-01T00:00:00Z",
                         "compensation": {"compensationTierSummary": "$1K – $2K per month"}}]}
    ctx = make_ctx([("api.ashbyhq.com/posting-api/job-board/acme", payload, "application/json")])
    job = ats.fetch_ashby({"params": {"slug": "acme"}}, ctx).jobs[0]
    assert job.remote_type == "remote" and job.employment_type == "internship"
    assert job.salary_text == "$1K – $2K per month"


def test_remotive(make_ctx):
    payload = {"jobs": [{"id": 5, "url": "https://remotive.com/remote-jobs/software-dev/react-dev-5", "title": "React Developer",
                         "company_name": "Foo", "job_type": "full_time", "publication_date": "2026-09-10T12:00:00",
                         "candidate_required_location": "USA Only", "salary": "$60k", "description": "<p>React</p>"}]}
    ctx = make_ctx([("remotive.com/api", payload, "application/json")])
    job = feeds.fetch_remotive({"url": "https://remotive.com/api/remote-jobs?limit=500"}, ctx).jobs[0]
    assert job.location_restrictions == ["USA Only"] and job.salary_text == "$60k"


def test_himalayas(make_ctx):
    payload = {"jobs": [{"title": "Junior Web Developer", "companyName": "Bar", "applicationLink": "https://bar.com/apply",
                         "guid": "https://himalayas.app/companies/bar/jobs/junior-web-developer", "locationRestrictions": ["Nepal", "India"],
                         "employmentType": "Full Time", "description": "<p>HTML CSS</p>", "pubDate": 1757000000, "seniority": ["Entry-level"]}]}
    ctx = make_ctx([("himalayas.app/jobs/api", payload, "application/json")])
    result = feeds.fetch_himalayas({"url": "https://himalayas.app/jobs/api", "params": {"pages": 3, "page_size": 20}}, ctx)
    assert len(result.jobs) == 1 and len(ctx.client.requested) == 1
    assert result.jobs[0].location_restrictions == ["Nepal", "India"]
    assert result.jobs[0].raw["seniority_hint"] == "Entry-level"


def test_weworkremotely_rss(make_ctx):
    rss = """<?xml version="1.0"?><rss version="2.0"><channel><title>WWR</title>
      <item><title>Acme: Frontend Developer</title><region>Anywhere in the World</region><type>Full-Time</type>
      <link>https://weworkremotely.com/remote-jobs/acme-frontend-developer</link>
      <pubDate>Mon, 07 Sep 2026 10:00:00 +0000</pubDate><description>&lt;p&gt;React role&lt;/p&gt;</description></item>
    </channel></rss>"""
    ctx = make_ctx([("weworkremotely.com", rss, "application/rss+xml")])
    job = feeds.fetch_weworkremotely({"url": "https://weworkremotely.com/categories/x.rss"}, ctx).jobs[0]
    assert job.company_name == "Acme" and job.title == "Frontend Developer"
    assert job.location_restrictions == [] and job.employment_type == "full_time"
    assert "React role" in job.description


def test_hackernews(make_ctx):
    search = {"hits": [{"objectID": "999", "title": "Ask HN: Who is hiring? (September 2026)", "created_at": "2026-09-01T15:00:00Z"}]}
    item = {"children": [
        {"id": 1, "created_at": "2026-09-02T10:00:00Z", "author": "a",
         "text": "Acme | Frontend Engineer (React) | REMOTE (Worldwide) | Full-time<p>Apply at https://jobs.lever.co/acme"},
        {"id": 2, "created_at": "2026-09-02T10:00:00Z", "author": "b", "text": "BigCo | Backend Engineer | Onsite NYC"},
    ]}
    ctx = make_ctx([("search_by_date", search, "application/json"), ("items/999", item, "application/json")])
    result = hackernews.fetch_hackernews({"params": {"thread": "who_is_hiring"}}, ctx)
    assert len(result.jobs) == 1
    job = result.jobs[0]
    assert job.company_name == "Acme" and "Frontend Engineer" in job.title and job.remote_type == "remote"
    assert result.links and result.links[0].url.startswith("https://jobs.lever.co/acme")


def test_jsonld_page(make_ctx):
    posting = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Junior React Developer",
               "description": "<p>Work with React and TypeScript. Requirements: HTML, CSS.</p>",
               "datePosted": "2026-09-05", "validThrough": "2026-12-01", "employmentType": "FULL_TIME",
               "hiringOrganization": {"@type": "Organization", "name": "Nimbus Labs", "sameAs": "https://nimbuslabs.example"},
               "jobLocationType": "TELECOMMUTE", "applicantLocationRequirements": {"@type": "Country", "name": "Nepal"},
               "baseSalary": {"@type": "MonetaryAmount", "currency": "USD",
                              "value": {"@type": "QuantitativeValue", "minValue": 400, "maxValue": 700, "unitText": "MONTH"}}}
    html = f"<html><head><script type='application/ld+json'>{json.dumps({'@graph': [posting]})}</script></head><body>Job</body></html>"
    ctx = make_ctx([("nimbuslabs.example/careers/1", html, "text/html; charset=utf-8")])
    result = page.fetch_page({"url": "https://nimbuslabs.example/careers/1"}, ctx)
    job = result.jobs[0]
    assert job.remote_type == "remote" and job.location_restrictions == ["Nepal"]
    assert (job.salary_min, job.salary_max, job.salary_currency, job.salary_period) == (400, 700, "USD", "month")
    assert job.company_name == "Nimbus Labs" and job.company_website == "https://nimbuslabs.example"


def test_page_without_structured_data_needs_extractor(make_ctx):
    html = "<html><body><main><h1>Frontend Developer</h1>" + "<p>Responsibilities: build UI. Requirements: React experience. How to apply: use the form.</p>" * 8 + "</main></body></html>"
    ctx = make_ctx([("example.org/job", html, "text/html")])
    assert page.fetch_page({"url": "https://example.org/job"}, ctx).status == "skipped"
