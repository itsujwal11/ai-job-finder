# AI Job Discovery — Complete Guide

> A step-by-step guide to install, run, and use the AI Job Discovery system.
> This system finds remote and Nepal-eligible tech jobs, scores them against your CV with AI, and prepares tailored CVs and cover letters for the best matches. **It never submits applications for you.**

---

## Table of Contents

1. [What This Is](#what-this-is)
2. [Prerequisites](#prerequisites)
3. [Installation (First Time)](#installation-first-time)
4. [Running the System](#running-the-system)
5. [Daily Workflow](#daily-workflow)
6. [Understanding Scores](#understanding-scores)
7. [AI Quota Management](#ai-quota-management)
8. [Configuration](#configuration)
9. [Troubleshooting](#troubleshooting)
10. [File Reference](#file-reference)

---

## What This Is

This system does **one thing well**: it finds job postings that match your skills from 20+ sources, scores them 0–100 against your CV using AI, and gives you a short list of the best ones with tailored application materials.

**What it does:**
- 🔍 Searches remote job APIs, company career boards, Nepali job boards, and search engines
- 🧹 Filters out irrelevant jobs (wrong role, too senior, closed to Nepal, scams)
- 🤖 Scores surviving jobs 0–100 against your CV using AI (free Gemini by default)
- 📝 Generates tailored CVs and cover letters for strong matches (85+)
- 📊 Shows everything in a dashboard at `http://localhost:8080`

**What it does NOT do:**
- ❌ Never submits applications — you always decide and apply yourself
- ❌ Never tricks websites — respects robots.txt, never bypasses logins/CAPTCHAs

---

## Prerequisites

| Requirement | How to Get It |
|---|---|
| **Docker Desktop** | Download from [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/). Install and start it. |
| **PowerShell** | Already on Windows. |
| **AI API key** | Free Gemini key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey) — no credit card needed. |
| **Tavily key** (recommended) | Free from [tavily.com](https://tavily.com/) — 1,000 searches/month, no credit card. |

---

## Installation (First Time)

### Step 1: Run the setup script

Open PowerShell in the project folder and run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

This creates a `.env` file with random secrets.

### Step 2: Set your AI key

Open `.env` and set the Gemini key (the default, free option):

```env
AI_PROVIDER=openai_compatible
OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
OPENAI_API_KEY=your-google-ai-studio-key-here
OPENAI_MODEL=gemini-2.5-flash-lite
```

### Step 3: Set a Tavily key (recommended)

This enables internet-wide job search across the entire web:

```env
TAVILY_API_KEY=tvly-your-key-here
```

### Step 4: Update your CV

Edit `profile/cv.md` with your real CV. This is the **only source of facts** — everything the AI says about you is verified against this file.

Edit `profile/preferences.yaml` to set your target roles, LinkedIn/GitHub URLs, and salary preferences.

### Step 5: Build and start

```powershell
docker compose up -d --build
```

### Step 6: Open the dashboard

Go to **http://localhost:8080** and click **Run scan now**.

---

## Running the System

### Start everything
```powershell
docker compose up -d
```

### Stop everything
```powershell
docker compose stop
```

### Rebuild after code changes
```powershell
docker compose up -d --build
```

### Run a scan manually
- **Dashboard**: Click **▸ Run scan now** in the top bar
- **Command line**: `docker compose exec engine python -m app.cli run`

### Automatic daily runs
If you set up n8n (at `http://localhost:5679`), scans run automatically at 06:00 Nepal time. Your PC and Docker must be on.

---

## Daily Workflow

1. **Run a scan** — Click "Run scan now" or let the 06:00 schedule do it
2. **Check the dashboard** at `http://localhost:8080`
3. **Open "Opportunities"** → look at **Ready to apply** and **Needs approval** tabs
4. **Open a job** → read the score breakdown and AI reasoning
5. **Decide**:
   - ✅ **Approve** — good fit, you'll apply later
   - 🚀 **I applied** — you already applied (prevents duplicates)
   - ❌ **Reject** — not for you
   - 🔇 **Dismiss** — not interested right now
6. **Apply** on the company's own website
7. **Track** in Application Tracker — update status as you hear back

### When a job has an email

You'll see a **📧 Send your CV here** section on the job detail page with:
- The company's application email
- A mailto link that pre-fills the subject line
- Your tailored CV and cover letter ready to download

---

## Understanding Scores

| Score | Meaning | What happens |
|---|---|---|
| **85–100** | Top match | Goes to "Ready to apply". Tailored CV + cover letter generated automatically. |
| **70–84** | Needs your approval | Worth a look, but check the reasoning yourself before applying. |
| **Below 70** | Ignored | Not shown in the main views but visible under "Analysed". |

The score weighs: skills match, experience fit, role fit, Nepal eligibility, pay, and growth value.

**Hard blocks** (applied at any score):
- Senior roles
- Jobs closed to Nepal
- Scam-shaped listings
- Duplicate postings

---

## AI Quota Management

### The free tier

The default setup uses **free Gemini** (`gemini-2.5-flash-lite`). Free tier quotas are per-model, per-day:

| Model | Daily Limit |
|---|---|
| `gemini-2.5-flash-lite` | Generous (hundreds/day) |
| `gemini-2.5-flash` | 20 requests/day |

### Check your quota

```powershell
docker compose exec engine python -m app.cli quota
```

Example output:
```
[ok]   gemini-2.5-flash-lite    quota available
[warn] gemini-2.5-flash         out of quota (limit = 20/day)
[info] 1 of 2 model(s) usable right now
```

### If AI quota runs out

**Nothing is lost.** Jobs that couldn't be scored are parked as "Awaiting AI" and picked up on the next run, in best-first order.

### Ways to avoid quota issues

| Solution | How |
|---|---|
| **Wait** | Quota resets at midnight. Run again tomorrow. |
| **Add more free models** | Add them to `ai.model_fallbacks` in `config/config.yaml`. The engine automatically falls back through the list. |
| **Analyse fewer per run** | Lower `pipeline.max_ai_analyses_per_run` in `config/config.yaml`. Best candidates are analysed first, so a smaller number still surfaces the good ones. |
| **Pay for Gemini** | Enable billing on your Google AI Studio key. Per-day caps disappear; costs pennies per day. |
| **Use Claude instead** | Set in `.env`: `AI_PROVIDER=claude`, `ANTHROPIC_API_KEY=...`, `ANTHROPIC_MODEL=claude-haiku-4-5` |

### Budget safety

The `ai.daily_budget_usd` setting in `config/config.yaml` is a hard cap. Default is `$0.00` for the free tier (Gemini free API costs $0.00 per call). If you switch to a paid key, set it to something like `0.50` to prevent runaway costs.

> **Common error: "Daily AI budget reached ($0.00)"**
> This happens because the free Gemini API reports $0.00 cost. Fix: set `ai.daily_budget_usd: 0.01` in `config/config.yaml`.

---

## Configuration

### Key files

| File | What it controls |
|---|---|
| `.env` | API keys, passwords, notification settings |
| `config/config.yaml` | Thresholds, sources, search queries, AI budget, limits |
| `profile/cv.md` | Your CV (the only source of facts) |
| `profile/preferences.yaml` | Target roles, salary, LinkedIn/GitHub URLs |

### Important settings in `config/config.yaml`

```yaml
# How many jobs to score per run (lower = less quota used)
pipeline:
  max_ai_analyses_per_run: 20

# Score thresholds
thresholds:
  auto_apply_min: 85     # 85+ = Ready to apply
  approval_min: 70       # 70-84 = Needs approval

# AI budget cap (per day)
ai:
  daily_budget_usd: 0.00  # raise for paid keys

# Fallback models (tried in order)
ai:
  model_fallbacks:
    - gemini-2.5-flash-lite
    - gemini-2.5-flash
```

### Adding search queries

Edit `search.queries` in `config/config.yaml`:

```yaml
search:
  queries:
    - '"junior frontend developer" remote worldwide'
    - 'site:boards.greenhouse.io remote junior react'
    - '"frontend developer" job vacancy Kathmandu Nepal'
```

Changes apply on the next run — no restart needed.

---

## Troubleshooting

### Nothing shows up after a run

1. Check **Awaiting AI** tab — if it's full, the AI quota ran out. Jobs are safe and will be scored on the next run.
2. Check **Filtered out** — your target titles may be too narrow. Edit `relevance.include_title_terms` in `config/config.yaml`.
3. Check **Runs & logs** — every fetch, error, and AI call is recorded.

### Docker won't start

```powershell
# Check Docker is running
docker info

# Rebuild everything from scratch
docker compose down
docker compose up -d --build
```

### Dashboard won't load

Make sure Docker containers are running:
```powershell
docker compose ps
```

The dashboard is at `http://localhost:8080` (port set by `DASHBOARD_PORT` in `.env`).

### A source is "blocked"

This is **normal and expected**. The system never bypasses logins, CAPTCHAs, or anti-bot protection. Sites like LinkedIn, Indeed, and Upwork appear only as leads for you to open yourself.

### Scores seem wrong

1. Update `profile/cv.md` with accurate, detailed information
2. Update `profile/preferences.yaml` with correct target roles
3. Run another scan — the AI will re-score with the updated profile

### View container logs

```powershell
# Engine logs
docker compose logs engine

# All service logs
docker compose logs

# Follow logs in real-time
docker compose logs -f engine
```

### Health check

```powershell
docker compose exec engine python -m app.cli check
```

---

## File Reference

```
project/
├── .env                    # Your API keys and secrets (never commit this)
├── .env.example            # Template for .env
├── config/
│   └── config.yaml         # All settings — thresholds, sources, queries, limits
├── profile/
│   ├── cv.md               # Your CV (the only source of facts for the AI)
│   └── preferences.yaml    # Target roles, salary, links
├── dashboard/              # React dashboard (built automatically by Docker)
├── engine/                 # Python backend (runs in Docker)
├── output/
│   └── applications/       # Generated CVs and cover letters
├── docker-compose.yml      # Container orchestration
└── scripts/
    └── setup.ps1           # First-time setup script
```

---

## Quick Reference Card

| Task | Command / Action |
|---|---|
| Start system | `docker compose up -d` |
| Stop system | `docker compose stop` |
| Run a scan | Dashboard → "Run scan now" |
| Check AI quota | `docker compose exec engine python -m app.cli quota` |
| View logs | `docker compose logs engine` |
| Health check | `docker compose exec engine python -m app.cli check` |
| Open dashboard | http://localhost:8080 |
| Open n8n | http://localhost:5679 |
| Edit your CV | Edit `profile/cv.md` |
| Change settings | Edit `config/config.yaml` (applies next run) |
| Change API keys | Edit `.env`, then `docker compose up -d` |
