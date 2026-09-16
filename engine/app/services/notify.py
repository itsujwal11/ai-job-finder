"""Notifications: Telegram and/or SMTP email. Every attempt is logged in the notifications table."""
from __future__ import annotations

import html
import logging
import smtplib
from email.message import EmailMessage
from typing import Any

import httpx

from .. import db
from ..config import Env, get_env, load_config

log = logging.getLogger(__name__)

GROUPS = [
    ("auto_apply_candidate", "🟢 Top matches (would auto-apply - automatic applications are OFF)"),
    ("manual_apply", "🔵 Ready to apply manually - materials prepared"),
    ("approval_required", "🟡 Needs your approval"),
]


def _record(run_id: int | None, channel: str, kind: str, status: str, subject: str, body: str, error: str | None = None) -> dict[str, Any]:
    db.execute(
        "INSERT INTO notifications (run_id, channel, kind, status, subject, body, error) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (run_id, channel, kind, status, subject[:300], body[:20000], error),
    )
    return {"channel": channel, "status": status, "error": error}


def send_telegram(env: Env, text_html: str) -> None:
    chunks: list[str] = []
    current = ""
    for line in text_html.splitlines(keepends=True):
        if len(current) + len(line) > 3800:
            chunks.append(current)
            current = ""
        current += line
    if current.strip():
        chunks.append(current)
    for chunk in chunks:
        response = httpx.post(
            f"https://api.telegram.org/bot{env.telegram_bot_token}/sendMessage",
            json={"chat_id": env.telegram_chat_id, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=20,
        )
        payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        if response.status_code != 200 or not payload.get("ok"):
            raise RuntimeError(f"Telegram API error {response.status_code}: {payload.get('description', response.text[:200])}")


def send_email(env: Env, subject: str, text: str, html_body: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = env.smtp_from or env.smtp_username
    message["To"] = env.notify_email_to
    message.set_content(text)
    message.add_alternative(html_body, subtype="html")
    if env.smtp_port == 465:
        server: smtplib.SMTP = smtplib.SMTP_SSL(env.smtp_host, env.smtp_port, timeout=30)
    else:
        server = smtplib.SMTP(env.smtp_host, env.smtp_port, timeout=30)
        if env.smtp_starttls:
            server.starttls()
    with server:
        if env.smtp_username and env.smtp_password:
            server.login(env.smtp_username, env.smtp_password)
        server.send_message(message)


def _deliver(kind: str, subject: str, telegram_html: str, text: str, email_html: str, run_id: int | None) -> list[dict[str, Any]]:
    env = get_env()
    results = []
    if env.telegram_enabled:
        try:
            send_telegram(env, telegram_html)
            results.append(_record(run_id, "telegram", kind, "sent", subject, text))
        except Exception as exc:
            log.warning("Telegram notification failed: %s", exc)
            results.append(_record(run_id, "telegram", kind, "failed", subject, text, str(exc)[:500]))
    if env.email_enabled:
        try:
            send_email(env, subject, text, email_html)
            results.append(_record(run_id, "email", kind, "sent", subject, text))
        except Exception as exc:
            log.warning("Email notification failed: %s", exc)
            results.append(_record(run_id, "email", kind, "failed", subject, text, str(exc)[:500]))
    if not results:
        results.append(_record(run_id, "none", kind, "skipped", subject, text, "No notification channel configured"))
    return results


def _salary(item: dict[str, Any]) -> str:
    low, high = item.get("salary_npr_monthly_min"), item.get("salary_npr_monthly_max")
    if low is None and high is None:
        return ""
    if low is not None and high is not None and low != high:
        return f"NPR {int(low):,}-{int(high):,}/mo"
    return f"NPR {int(low or high):,}/mo"


def send_run_digest(run_id: int, stats: dict[str, Any]) -> list[dict[str, Any]]:
    cfg, env = load_config(), get_env()
    items = db.fetch_all(
        """
        SELECT id, title, company_name, match_score, recommendation, nepal_eligibility, remote_type, employment_type,
               salary_npr_monthly_min, salary_npr_monthly_max, source_url, apply_method, materials_status
        FROM opportunities
        WHERE notified_at IS NULL AND pipeline_status = 'analyzed'
          AND recommendation IN ('auto_apply_candidate', 'manual_apply', 'approval_required')
          AND review_status NOT IN ('rejected', 'dismissed', 'applied')
        ORDER BY match_score DESC
        LIMIT %s
        """,
        (int(cfg.get("notifications.digest_max_items", 25)),),
    )
    errors = int(db.fetch_value("SELECT count(*) AS n FROM events WHERE run_id = %s AND level = 'error'", (run_id,)) or 0)
    if not items and not errors and not cfg.get("notifications.send_when_nothing_new", False):
        return [_record(run_id, "none", "digest", "skipped", f"Run #{run_id}", "Nothing new to report")]

    dashboard = env.public_base_url
    subject = f"Job discovery: {len(items)} new match{'es' if len(items) != 1 else ''} (run #{run_id})"
    tg: list[str] = [f"<b>🔎 Job discovery - run #{run_id}</b>"]
    text: list[str] = [subject]
    mail: list[str] = [f"<h2>Job discovery - run #{run_id}</h2>"]

    for key, heading in GROUPS:
        group = [i for i in items if i["recommendation"] == key]
        if not group:
            continue
        tg.append(f"\n<b>{html.escape(heading)}</b>")
        text.append(f"\n{heading}")
        mail.append(f"<h3>{html.escape(heading)}</h3><ul>")
        for item in group:
            link = f"{dashboard}/#/opportunities/{item['id']}"
            details = " · ".join(x for x in (
                item["remote_type"], (item["nepal_eligibility"] or "").replace("_", " "), _salary(item),
                "materials ✓" if item["materials_status"] == "generated" else "materials need review" if item["materials_status"] == "needs_review" else "",
            ) if x)
            company = item["company_name"] or "Unknown company"
            tg.append(f"• <b>{item['match_score']}</b> <a href=\"{html.escape(link)}\">{html.escape(item['title'])}</a> - {html.escape(company)}\n  {html.escape(details)}")
            text.append(f"- [{item['match_score']}] {item['title']} - {company} ({details})\n  {link}\n  Posting: {item['source_url']}")
            mail.append(
                f"<li><b>{item['match_score']}</b> <a href=\"{html.escape(link)}\">{html.escape(item['title'])}</a> - "
                f"{html.escape(company)}<br><small>{html.escape(details)} · <a href=\"{html.escape(item['source_url'])}\">posting</a></small></li>"
            )
        mail.append("</ul>")

    summary = (
        f"Discovered {stats.get('discovered_new', 0)} new · {stats.get('duplicates', 0)} duplicates · "
        f"{stats.get('filtered_out', 0)} filtered · {stats.get('analyzed', 0)} analysed · "
        f"AI ${stats.get('ai_cost_usd', 0):.2f}"
    )
    failed = (stats.get("tasks") or {}).get("failed", 0) + (stats.get("tasks") or {}).get("blocked", 0)
    if failed or errors:
        summary += f" · {failed} source problems · {errors} errors"
    tg.append(f"\n<i>{html.escape(summary)}</i>\n<a href=\"{html.escape(dashboard)}\">Open dashboard</a>")
    text.append(f"\n{summary}\nDashboard: {dashboard}")
    mail.append(f"<p><i>{html.escape(summary)}</i></p><p><a href=\"{html.escape(dashboard)}\">Open dashboard</a></p>")

    results = _deliver("digest", subject, "\n".join(tg), "\n".join(text), "".join(mail), run_id)
    if items and any(r["status"] == "sent" for r in results):
        db.execute("UPDATE opportunities SET notified_at = now() WHERE id = ANY(%s)", ([i["id"] for i in items],))
    return results


def send_alert(message: str, run_id: int | None = None) -> list[dict[str, Any]]:
    return _deliver("error", message.split("\n", 1)[0][:120], html.escape(message), message, f"<pre>{html.escape(message)}</pre>", run_id)


def send_test() -> list[dict[str, Any]]:
    message = "✅ Test notification from your AI job discovery system."
    return _deliver("test", "Job discovery test notification", message, message, f"<p>{message}</p>", None)
