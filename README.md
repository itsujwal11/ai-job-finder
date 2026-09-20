# AI Job Discovery (phase 1: discovery & filtering)

Finds remote and Nepal-eligible junior tech jobs every day from remote job APIs, company ATS boards, free public job APIs, search engines and the Nepali boards (merojob, froxjob, jobaxle), filters them with free rules, scores the best ones 0–100 against `profile/cv.md` with an AI model, prepares a tailored CV + cover letter for strong matches, and notifies you. **It never submits applications.**

## Install (Windows, once)

1. Install and start **Docker Desktop**.
2. In PowerShell, from this folder:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
   ```
   The first run creates `.env` with random secrets.
3. Open `.env` and set your AI key. The default is **free Gemini**:
   ```env
   AI_PROVIDER=openai_compatible
   OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
   OPENAI_API_KEY=your-google-ai-studio-key
   OPENAI_MODEL=gemini-2.5-flash-lite
   ```
   Get a free key at [aistudio.google.com](https://aistudio.google.com/apikey).

4. *(Recommended)* Set a free **Tavily** key for internet-wide job search:
   ```env
   TAVILY_API_KEY=tvly-your-key-here
   ```
   Sign up free at [tavily.com](https://tavily.com/) — 1,000 searches/month, no credit card.

5. Apply the changes: `docker compose up -d --build`.
6. Open the dashboard at **http://localhost:8080** and press **Run now**.
7. *Optional, for the daily 06:00 schedule:* open **http://localhost:5679** (n8n) and create the
   owner account. The **Job Discovery - Daily** workflow is already imported and published.
   Everything works without n8n — you just start runs yourself from the dashboard.

## Use it

| What | How |
|---|---|
| Automatic daily run | n8n runs it at **06:00 Nepal time**. Your PC and Docker must be on. |
| Run now | Dashboard → **Run now** (top right of Overview). Or `docker compose exec engine python -m app.cli run`, or n8n → *Job Discovery - Daily* → **Execute workflow**. |
| Review jobs | **http://localhost:8080** → *Opportunities* → the *Ready to apply* and *Needs approval* tabs. |
| Decide | Open a job, then choose **Approve**, **Reject**, or **I applied** (duplicate applications are blocked). |
| Send your CV | When a job has an email, you'll see a **📧 Send your CV here** section with the company email, a pre-filled mailto link, and instructions. Download your tailored CV + cover letter from the materials section below. |
| CV & cover letter | These are generated for jobs scoring 70+. On the job page: copy the text or download `.docx`. Files are also in `output/applications/`. Send only documents marked *Fact-checked*. |
| Health check | `docker compose exec engine python -m app.cli check` |
| AI quota left | `docker compose exec engine python -m app.cli quota` |
| Check every source | Dashboard → **Sources**. Shows what each of the 16 sources delivered in the last 7 days, and names any that are failing. |
| Learn what it all means | Dashboard → **How it works**. |
| Stop / start | `docker compose stop` / `docker compose up -d` |

**Scores:** 85–100 is a top match. When the application is a web form, it goes to *Ready to apply*. 70–84 needs your approval. Below 70 is ignored. Senior roles, jobs closed to Nepal, scam-like listings and duplicates are blocked at any score.

## Where the jobs come from

The system searches the **entire internet** for jobs using a combination of structured APIs and search engines:

| Kind | Sources |
|---|---|
| Remote job APIs & feeds | Remotive, RemoteOK, Jobicy, Himalayas, We Work Remotely, Working Nomads, Hacker News *Who is hiring* |
| Free public job APIs | **The Muse** (curated tech/startup, filtered to entry level and internships — no key needed). **FindWork.dev** is also wired up but needs a free key: set `FINDWORK_API_KEY` in `.env` or it is skipped. |
| Company ATS boards | Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee — any board seen anywhere is added automatically and crawled through its official public API |
| Nepali job boards | merojob.com, froxjob.com, jobaxle.com, kumarijob.com, jobsnepal.com, merorojgari.com — read from each board's sitemap, then parsed from the schema.org `JobPosting` block every detail page carries, so they cost no AI credit. This is where onsite Kathmandu roles come from. |
| Search engines (internet-wide) | **Tavily** (free, 1000 searches/month) / Brave / Google CSE — searches the entire internet with 30 targeted queries like `"junior frontend developer" remote worldwide`, `site:jobs.lever.co remote junior frontend` and `"frontend developer" job vacancy Kathmandu Nepal`. |

Add or remove any of them in `config/config.yaml` under `sources`, `local_boards.boards` and
`search.queries`.

### How internet-wide search works

When you set `TAVILY_API_KEY` in `.env`, every run fires up to 20 search queries against Tavily's search engine. These queries search the **entire public internet** for job postings — including company career pages, niche job boards, forums, and aggregators. The results are then crawled, parsed, filtered, and scored just like every other source.

The search queries are configured in `config/config.yaml` under `search.queries`. You can add your own! Examples:
```yaml
search:
  queries:
    - '"junior frontend developer" remote worldwide'
    - 'site:boards.greenhouse.io remote junior react'
    - 'remote developer jobs Nepal eligible'
```

### Company emails

The system extracts company application emails from job postings in two ways:

1. **Structured data**: Many job pages include an email in their `schema.org/JobPosting` data
2. **AI extraction**: When the AI analyses a posting, it looks for application email addresses explicitly stated in the description

When an email is found, you'll see:
- A **✉** icon in the Opportunities table
- A prominent **📧 Send your CV here** section on the job detail page with a mailto link that pre-fills the subject line
- Instructions on how to apply (extracted from the posting)

### Nepali boards and the stack filters

`relevance.exclude_title_terms` filters out stacks far from your CV (PHP, WordPress, .NET, C#,
…). That list is right for worldwide-remote listings, where there are thousands of results and
you can afford to be strict — but it would throw away most of the Kathmandu market, which runs
on exactly those stacks.

So those exclusions are **lifted for Nepal-market postings only** — anything from a board in
`local_boards.sources`, or with a Nepal location. The terms that get relaxed are in
`relevance.nepal_relaxed_exclude_terms`, and extra local titles (IT officer, MIS officer,
Laravel developer, …) are in `relevance.nepal_extra_include_terms`. Worldwide listings still get
the full strict filter.

Local boards are also filtered **before** the crawl: each board publishes thousands of vacancies,
so the job title is read out of the URL slug and checked against the same relevance and seniority
rules first. Only the pages that pass are fetched, which keeps the crawl to a few dozen requests.

## The AI model and its quota

Every posting that survives the free rule-based filters costs one AI call to score. The system uses
**free Gemini** by default:

```
AI_PROVIDER=openai_compatible
OPENAI_MODEL=gemini-2.5-flash-lite
```

`config/config.yaml` lists `ai.model_fallbacks`. The engine starts at `OPENAI_MODEL`, and when a
model says its **per-day** quota is gone it marks that model spent for the rest of the day and
continues on the next one in the list. It does not keep retrying a daily quota, because that
never clears before midnight.

**Check what you have left at any time:**

```powershell
docker compose exec engine python -m app.cli quota
```

```
[ok]   gemini-2.5-flash-lite    quota available
[warn] gemini-2.5-flash         out of quota (GenerateRequestsPerDayPerProjectPerModel-FreeTier = 20)
[info] 1 of 2 model(s) usable right now
```

### Free Gemini quotas are small

The free tier is metered **per model, per day**, and the numbers are low — `gemini-2.5-flash`
allows only **20 requests a day**, which one run uses up immediately. `gemini-2.5-flash-lite` is
far more generous and is the default here for that reason.

If you run out of quota on everything, nothing is lost: postings are parked as *awaiting AI* and
picked up on the next run, in best-first order. They are never dropped.

**To stop worrying about quota**, pick one of these:

| Option | What to do |
|---|---|
| Add more free models | Append them to `ai.model_fallbacks`. Any OpenAI-compatible endpoint works. |
| Analyse fewer jobs per run | Lower `pipeline.max_ai_analyses_per_run`. The best candidates are analysed first, so a smaller number still surfaces the good ones. |
| Pay for Gemini | Enable billing on the same key. Per-day caps disappear; ~20 analyses/day costs a few cents. Nothing in the config changes. |
| Use Claude instead | Set `AI_PROVIDER=claude` and `ANTHROPIC_API_KEY=...`, then `ANTHROPIC_MODEL=claude-haiku-4-5` for the cheapest option. |

The `ai.daily_budget_usd` guard applies to every provider, so a paid key cannot run away with
your money: once the day's spend hits the budget, AI calls stop until tomorrow. The default is
`$0.00` for the free tier — set it higher when using a paid key.

### Which jobs get the AI budget

Because AI calls are limited, every posting gets a free rule-based **pre-score** the moment it is
stored (`engine/app/pipeline/prescore.py`), from its title, stack keywords, seniority wording,
location and source. The analysis queue is ordered by that pre-score, so a scarce quota is spent
on the postings most likely to fit — not on whatever arrived last. The pre-score is only a queue
order; the real 0–100 match score always comes from the AI analysis.

## Change things

- `profile/cv.md` is your CV and the only source of facts. Update it when your CV changes.
- `profile/preferences.yaml` holds target roles, minimum pay, and your LinkedIn / GitHub / portfolio URLs.
- `config/config.yaml` holds thresholds, sources, search queries, filters, limits and the **daily AI budget** (`ai.daily_budget_usd`, default $0). Edits apply on the next run.
- To change what the internet search looks for, edit `search.queries` in `config/config.yaml`.
- To save credit, set `ANTHROPIC_MODEL=claude-sonnet-5` (cheaper) or `claude-haiku-4-5` (cheapest) in `.env`, or lower `pipeline.max_ai_analyses_per_run` or `materials.max_per_run`. Then run `docker compose up -d`.

## Troubleshooting

- `docker compose logs engine` / `docker compose logs n8n`
- Dashboard → **Runs & logs** shows every source fetch, error and AI cost.
- If a site blocks access, the task shows as *blocked*. That is expected: the system never bypasses logins, CAPTCHAs or anti-bot protection. Jobs on LinkedIn, Indeed, Upwork and similar sites appear only as leads for you to open yourself.
- **AI budget error with free tier**: If you see "Daily AI budget reached ($0.00)", don't worry — the free Gemini API costs $0.00 per call, so the budget is never actually spent. If this happens, set `ai.daily_budget_usd: 0.01` in `config/config.yaml`.
