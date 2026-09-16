# AI Job Discovery (phase 1: discovery & filtering)

Finds remote / Nepal-eligible junior tech jobs every day, filters them, scores them 0–100 against `profile/cv.md` with Claude, prepares a tailored CV + cover letter for good matches, and notifies you. **It never submits applications.**

## Install (Windows, once)

1. Install and start **Docker Desktop**.
2. In PowerShell, from this folder:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
   ```
   The first run creates `.env` with random secrets.
3. Open `.env` and set `ANTHROPIC_API_KEY=...`. Optionally also set `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` or the `SMTP_*` settings for notifications, and `BRAVE_API_KEY` or `TAVILY_API_KEY` for search engines.
4. Apply the changes: `docker compose up -d`.
5. Open **http://localhost:5679** (n8n) and create the owner account. The **Job Discovery - Daily** workflow is already imported and published.

## Use it

| What | How |
|---|---|
| Automatic daily run | n8n runs it at **06:00 Nepal time**. Your PC and Docker must be on. |
| Run now | n8n → *Job Discovery - Daily* → **Execute workflow**. Or run `docker compose exec engine python -m app.cli run`. |
| Review jobs | **http://localhost:8080** → *Opportunities* → the *Ready to apply* and *Needs approval* tabs. |
| Decide | Open a job, then choose **Approve**, **Reject**, or **I applied** (duplicate applications are blocked). |
| CV & cover letter | These are generated for jobs scoring 70+. On the job page: copy the text or download `.docx`. Files are also in `output/applications/`. Send only documents marked *Fact-checked*. |
| Health check | `docker compose exec engine python -m app.cli check` |
| Stop / start | `docker compose stop` / `docker compose up -d` |

**Scores:** 85–100 is a top match. When the application is a web form, it goes to *Ready to apply*. 70–84 needs your approval. Below 70 is ignored. Senior roles, jobs closed to Nepal, scam-like listings and duplicates are blocked at any score.

## Change things

- `profile/cv.md` is your CV and the only source of facts. Update it when your CV changes.
- `profile/preferences.yaml` holds target roles, minimum pay, and your LinkedIn / GitHub / portfolio URLs.
- `config/config.yaml` holds thresholds, sources, search queries, filters, limits and the **daily AI budget** (`ai.daily_budget_usd`, default $3). Edits apply on the next run.
- To save credit, set `ANTHROPIC_MODEL=claude-sonnet-5` (cheaper) or `claude-haiku-4-5` (cheapest) in `.env`, or lower `pipeline.max_ai_analyses_per_run` or `materials.max_per_run`. Then run `docker compose up -d`.

## Troubleshooting

- `docker compose logs engine` / `docker compose logs n8n`
- Dashboard → **Runs & logs** shows every source fetch, error and AI cost.
- If a site blocks access, the task shows as *blocked*. That is expected: the system never bypasses logins, CAPTCHAs or anti-bot protection. Jobs on LinkedIn, Indeed, Upwork and similar sites appear only as leads for you to open yourself.
