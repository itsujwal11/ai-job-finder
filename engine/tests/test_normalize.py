from datetime import UTC, datetime

from app.pipeline.normalize import (
    detect_remote_type,
    html_to_text,
    map_employment_type,
    norm_company,
    norm_title,
    parse_datetime,
    parse_salary_text,
    registrable_domain,
    to_npr_monthly,
)


def test_html_to_text_lists_and_escaped_html():
    text = html_to_text("<p>Hello</p><ul><li>One</li><li>Two</li></ul>")
    assert "Hello" in text and "- One" in text and "- Two" in text
    assert html_to_text("&lt;p&gt;Build UIs&lt;/p&gt;") == "Build UIs"


def test_title_and_company_normalization():
    assert norm_title("Sr. Front-End Developer (Remote)") == "senior frontend developer"
    assert norm_title("Senior Frontend Developer - Remote") == "senior frontend developer"
    assert norm_title("React.js Developer") == "react developer"
    assert norm_company("Datum Systems Pvt. Ltd.") == "datum systems"
    assert norm_company("ACME, Inc.") == "acme"


def test_salary_parsing():
    assert parse_salary_text("$50,000 - $70,000 per year") == {"min": 50000, "max": 70000, "currency": "USD", "period": "year"}
    assert parse_salary_text("NPR 25,000 per month") == {"min": 25000, "max": None, "currency": "NPR", "period": "month"}
    assert parse_salary_text("Rs. 30000/month")["currency"] == "NPR"
    assert parse_salary_text("$25/hour")["period"] == "hour"
    euro = parse_salary_text("€3k-4k monthly")
    assert (euro["min"], euro["max"], euro["currency"], euro["period"]) == (3000, 4000, "EUR", "month")
    assert parse_salary_text("$60k")["period"] == "year"
    assert parse_salary_text("Competitive salary") is None


def test_npr_conversion():
    fx = {"USD": 140, "NPR": 1}
    assert to_npr_monthly(60000, "USD", "year", fx) == 700000
    assert to_npr_monthly(25000, "NPR", "month", fx) == 25000
    assert to_npr_monthly(10, "XYZ", "hour", fx) is None


def test_dates():
    assert parse_datetime(1700000000000).year == 2023
    assert parse_datetime("2026-09-01T10:00:00Z") == datetime(2026, 9, 1, 10, tzinfo=UTC)
    assert parse_datetime("Mon, 07 Sep 2026 10:00:00 +0000").day == 7
    assert parse_datetime("not a date") is None


def test_remote_and_employment_type():
    assert detect_remote_type("Frontend Developer", "Remote - Worldwide") == "remote"
    assert detect_remote_type("Hybrid - Kathmandu") == "hybrid"
    assert detect_remote_type("On-site in Berlin") == "onsite"
    assert detect_remote_type("Berlin") == "unknown"
    assert map_employment_type("FULL_TIME") == "full_time"
    assert map_employment_type(["Contract"]) == "contract"
    assert map_employment_type("Internship") == "internship"
    assert map_employment_type(None) == "unspecified"


def test_registrable_domain():
    assert registrable_domain("careers.example.co.uk") == "example.co.uk"
    assert registrable_domain("https://jobs.lever.co/acme") == "lever.co"
    assert registrable_domain("example.com.np") == "example.com.np"
