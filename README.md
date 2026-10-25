# Job Tracker Alert

AI-powered job monitoring system that scrapes 30+ company career pages, matches jobs against your resume using LLMs, and delivers real-time alerts with a web dashboard.

**Live at:** [jobai.live](https://jobai.live) | **Stack:** Python, Flask, Gemini + Claude APIs, SQLite, GCE

![Dashboard](https://img.shields.io/badge/dashboard-Flask%20%2B%20OAuth-blue) ![AI](https://img.shields.io/badge/AI-Gemini%20%2B%20Claude-orange) ![Scrapers](https://img.shields.io/badge/sources-30%2B%20companies-green)

## What It Does

1. **Scrapes** career pages from 30+ companies every 15 minutes using purpose-built adapters for 7 different ATS platforms (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Netflix Eightfold, Stripe)
2. **Matches** each job against your resume using AI (Gemini primary, Claude fallback) with a structured prompt that returns tier classification, confidence score, and actionable reasoning
3. **Alerts** you instantly via Telegram for top matches (Tier 1) and sends a daily email digest for worth-tracking roles (Tier 2)
4. **Dashboard** with Google OAuth, filters, search, cover letter generation, and PDF/DOCX export

## Architecture

```
Systemd Timer (15min) → main.py → ATS Scrapers → LLM Matcher → SQLite → Notifier
                                                                          ├── Telegram (Tier 1)
                                                                          └── Email (Tier 1 + Digest)

Browser → Nginx (SSL) → Gunicorn → Flask App (OAuth) → SQLite
                                       ├── Dashboard (filters, stats, search)
                                       ├── Cover Letter Generator (Gemini/Claude)
                                       └── Resume Upload (txt/pdf)
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **AI/ML** | Google Gemini (`gemini-2.5-flash-lite`), Anthropic Claude (`claude-haiku-4-5`) — dual-LLM with automatic failover |
| **Scraping** | Python `requests` + `BeautifulSoup4` — custom adapters per ATS platform |
| **Backend** | Flask, Gunicorn, SQLite |
| **Auth** | Google OAuth 2.0 via Authlib with email allowlist |
| **Notifications** | Telegram Bot API, Gmail SMTP (HTML emails) |
| **Infra** | Google Compute Engine (e2-micro free tier), Nginx, systemd timers, Let's Encrypt SSL |
| **Cover Letters** | AI-generated, exportable as PDF (ReportLab) or DOCX (python-docx / docx-js) |

## ATS Platform Adapters

Each adapter handles a different Applicant Tracking System's API or page structure:

| Adapter | Companies | Method |
|---------|-----------|--------|
| **Greenhouse** | Anthropic, Databricks, Figma, Airbnb, Coinbase, Scale AI, Cloudflare, Datadog, MongoDB, Robinhood, Twilio, Vercel, Okta, Airtable, Zscaler | Public JSON API |
| **Lever** | Palantir, Plaid | Public JSON API |
| **Ashby** | OpenAI, Notion, Ramp | GraphQL API |
| **Workday** | CrowdStrike, Salesforce/Slack | REST API |
| **SmartRecruiters** | Palo Alto Networks, Block | REST API |
| **Netflix (Eightfold)** | Netflix | Custom scraper |
| **Stripe** | Stripe | Next.js page scraper |

## AI Matching System

The matcher uses a structured prompt with explicit tier definitions:

- **Tier 1 (Apply Now):** Score >= 0.75 — strong title + seniority + responsibility fit
- **Tier 2 (Worth Tracking):** Score 0.40-0.74 — adjacent roles or seniority mismatch
- **Tier 3 (Skip):** Score < 0.40 — not relevant

Each match returns `{tier, score, reason}` as structured JSON. The prompt enforces selectivity — only ~25-30% of PM roles should be Tier 1.

## Setup

```bash
# Clone and install
git clone https://github.com/apjvarun/job-tracker-ai.git
cd job-tracker-ai
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your API keys

# Add your resume
cp your-resume.txt resume.txt

# Run
python src/main.py              # Fetch + match + notify
python web/app.py               # Dashboard at http://localhost:8080
```

## Project Structure

```
├── src/
│   ├── main.py              # Orchestrator: fetch → match → notify
│   ├── matcher.py           # Dual-LLM matching with structured JSON output
│   ├── database.py          # SQLite with deduplication (MD5 hashing)
│   ├── notifier.py          # Telegram + Email (instant + digest)
│   ├── digest.py            # Daily Tier 2 digest emailer
│   └── sources/             # ATS platform adapters
│       ├── base.py          # Abstract base + RawJob dataclass
│       ├── greenhouse.py    # Greenhouse API (15 companies)
│       ├── lever.py         # Lever API
│       ├── ashby.py         # Ashby GraphQL
│       ├── workday_ats.py   # Workday API
│       ├── smartrecruiters.py
│       ├── netflix.py       # Eightfold scraper
│       └── stripe_source.py # Next.js scraper
├── web/
│   ├── app.py               # Flask dashboard + OAuth + cover letter gen
│   └── templates/           # Dashboard + login HTML
├── deploy/                  # Systemd services, Nginx config, deploy scripts
├── config.yaml              # Company sources + matching config
├── .env.example             # Environment variable template
└── requirements.txt
```

## License

MIT
