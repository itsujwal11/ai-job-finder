"""Text, URL, date and salary normalization shared by all sources."""
from __future__ import annotations

import email.utils
import html
import re
import unicodedata
from datetime import UTC, datetime
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_WS = re.compile(r"[ \t\r\f\v]+")
_BLOCK_TAGS = ["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "ul", "ol", "br"]


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------
def ascii_lower(value: str | None) -> str:
    return unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()


def clean_text(value: str | None) -> str:
    lines = [_WS.sub(" ", line).strip() for line in (value or "").replace("\xa0", " ").splitlines()]
    out: list[str] = []
    for line in lines:
        if line:
            out.append(line)
        elif out and out[-1] != "":
            out.append("")
    return "\n".join(out).strip()


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value) if "&lt;" in value else value
    if "<" not in value:
        return clean_text(html.unescape(value))
    soup = BeautifulSoup(value, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()
    for li in soup.find_all("li"):
        li.insert(0, "- ")
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")
    return clean_text(soup.get_text())


def contains_term(text_lower: str, term: str) -> bool:
    """Whole-term match that also works for terms like 'c++', '.net', 'sr'."""
    pattern = r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])"
    return re.search(pattern, text_lower) is not None


def first_term(text_lower: str, terms: list[str]) -> str | None:
    return next((t for t in terms if contains_term(text_lower, t)), None)


# ---------------------------------------------------------------------------
# Company & title normalization (used for fingerprints and duplicate checks)
# ---------------------------------------------------------------------------
_COMPANY_SUFFIXES = {
    "inc", "llc", "ltd", "limited", "pvt", "private", "gmbh", "corp", "corporation", "co", "company",
    "plc", "sa", "ag", "bv", "srl", "pty", "llp", "oy", "ab", "sas", "kk",
}
_TITLE_REPLACEMENTS = [
    (r"\bfront[\s-]?end\b", "frontend"),
    (r"\bback[\s-]?end\b", "backend"),
    (r"\bfull[\s-]?stack\b", "fullstack"),
    (r"\bsr\b\.?", "senior"),
    (r"\bjr\b\.?", "junior"),
    (r"\bengr\b", "engineer"),
    (r"\bdev\b", "developer"),
    (r"\breact\.?js\b", "react"),
    (r"\bnode\.?js\b", "node"),
    (r"\bnext\.?js\b", "nextjs"),
]
_TITLE_NOISE = re.compile(
    r"\b(100 remote|fully remote|remote first|remote|worldwide|anywhere|work from home|wfh|hybrid|onsite|on site"
    r"|full time|part time|m f d|f m d|m w d|all genders|urgent|hiring|immediate joiner)\b"
)


def norm_company(name: str | None) -> str:
    tokens = re.sub(r"[^a-z0-9]+", " ", ascii_lower(name)).split()
    while tokens and tokens[-1] in _COMPANY_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def norm_title(title: str | None) -> str:
    text = ascii_lower(title)
    text = re.sub(r"\(.*?\)|\[.*?\]", " ", text)
    for pattern, replacement in _TITLE_REPLACEMENTS:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"[^a-z0-9+#]+", " ", text)
    text = _TITLE_NOISE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------
_SECOND_LEVEL = {"co", "com", "org", "net", "edu", "gov", "ac", "ltd", "plc"}


def host_of(url: str | None) -> str:
    if not url:
        return ""
    try:
        host = (urlsplit(url.strip()).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def registrable_domain(host_or_url: str | None) -> str:
    host = host_of(host_or_url) if "://" in (host_or_url or "") else (host_or_url or "").lower()
    labels = [label for label in host.split(".") if label]
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def host_matches(host: str, domains: list[str]) -> str | None:
    host = host.lower()
    return next((d for d in domains if host == d or host.endswith("." + d)), None)


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------
def parse_datetime(value: object) -> datetime | None:
    if value in (None, "", 0):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.isdigit():
        return parse_datetime(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
        try:
            parsed = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d %b %Y", "%B %d, %Y", "%b %d, %Y"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Remote / employment type
# ---------------------------------------------------------------------------
def detect_remote_type(*texts: str | None) -> str:
    blob = ascii_lower(" ".join(t for t in texts if t))
    if not blob:
        return "unknown"
    if re.search(r"\bhybrid\b", blob):
        return "hybrid"
    if re.search(r"\b(remote|work from home|wfh|telecommut\w*|work from anywhere|distributed team|anywhere in the world)\b", blob):
        return "remote"
    if re.search(r"\b(on[\s-]?site|in[\s-]office|office[\s-]based|in[\s-]person)\b", blob):
        return "onsite"
    return "unknown"


def map_employment_type(value: str | list[str] | None) -> str:
    if isinstance(value, list):
        value = " ".join(str(v) for v in value)
    text = ascii_lower(value)
    if not text:
        return "unspecified"
    if re.search(r"intern", text):
        return "internship"
    if re.search(r"part[\s_-]?time", text):
        return "part_time"
    if re.search(r"freelanc", text):
        return "freelance"
    if re.search(r"contract|contractor|consult", text):
        return "contract"
    if re.search(r"\btemp", text):
        return "temporary"
    if re.search(r"full[\s_-]?time|permanent|employee|regular", text):
        return "full_time"
    return "unspecified"


# ---------------------------------------------------------------------------
# Salary
# ---------------------------------------------------------------------------
_CURRENCY_TOKENS = {
    "$": "USD", "usd": "USD", "us$": "USD", "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP",
    "₹": "INR", "inr": "INR", "rs": "NPR", "rs.": "NPR", "npr": "NPR", "aud": "AUD", "cad": "CAD",
    "sgd": "SGD", "aed": "AED",
}
_CUR = r"(?:us\$|[$€£₹]|\brs\.?|\b(?:usd|eur|gbp|inr|npr|aud|cad|sgd|aed)\b)"
_NUM = r"\d[\d,]*(?:\.\d+)?"
_SALARY_RE = re.compile(
    rf"(?P<cur>{_CUR})\s*(?P<a>{_NUM})\s*(?P<ak>k\b)?"
    rf"(?:\s*(?:-|–|—|to)\s*(?:{_CUR})?\s*(?P<b>{_NUM})\s*(?P<bk>k\b)?)?"
    r"(?P<tail>[^\n]{0,40})",
    re.IGNORECASE,
)
_PERIODS = [
    ("hour", r"per hour|/\s*h(?:ou)?r\b|hourly|an hour|/h\b"),
    ("day", r"per day|/\s*day|daily|a day"),
    ("week", r"per week|/\s*w(?:ee)?k\b|weekly"),
    ("month", r"per month|/\s*mo(?:nth)?\b|monthly|a month|p\.?m\.?\b"),
    ("year", r"per year|/\s*y(?:ea)?r\b|annual|annually|per annum|a year|yearly|p\.?a\.?\b"),
]
_MONTHLY_FACTOR = {"hour": 160.0, "day": 21.67, "week": 4.33, "month": 1.0, "year": 1 / 12}


def _num(value: str | None, thousands: str | None) -> float | None:
    if not value:
        return None
    number = float(value.replace(",", ""))
    return number * 1000 if thousands else number


def parse_salary_text(text: str | None) -> dict | None:
    if not text:
        return None
    match = _SALARY_RE.search(text)
    if not match:
        return None
    low = _num(match.group("a"), match.group("ak"))
    high = _num(match.group("b"), match.group("bk") or (match.group("ak") if match.group("b") else None))
    if low is None or low <= 0:
        return None
    currency = _CURRENCY_TOKENS.get(match.group("cur").lower().rstrip(), "USD")
    tail = ascii_lower(match.group("tail"))
    period = next((name for name, pattern in _PERIODS if re.search(pattern, tail)), None)
    if period is None:
        reference = high or low
        if currency == "NPR":
            period = "year" if reference >= 600_000 else "month"
        elif reference < 250:
            period = "hour"
        elif reference < 20_000:
            period = "month"
        else:
            period = "year"
    return {"min": low, "max": high, "currency": currency, "period": period}


def to_npr_monthly(amount: float | None, currency: str | None, period: str | None, fx: dict[str, float]) -> float | None:
    if amount is None or not currency:
        return None
    rate = fx.get(currency.upper())
    factor = _MONTHLY_FACTOR.get(period or "year")
    if rate is None or factor is None:
        return None
    return round(amount * rate * factor)
