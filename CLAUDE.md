# Job Tracker Alert — Product & Technical Specification

## Overview
AI-powered job monitoring system that scrapes 30+ company career pages, matches jobs against a resume using LLMs (Gemini + Claude), sends instant alerts for top matches, and provides a web dashboard with Google OAuth.

**Infra:** Google Compute Engine (e2-micro, free tier)

---

## CRITICAL: Authentication
- **Google OAuth 2.0** is enabled on the web dashboard via Authlib
- OAuth is gated on `GOOGLE_CLIENT_ID` env var — if missing, dashboard becomes PUBLIC
- **Allowed emails:** configured via `ALLOWED_EMAILS` env var (comma-separated)
- **NEVER deploy without ensuring `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `FLASK_SECRET_KEY` are set in `.env` on the VM**
- `FLASK_SECRET_KEY` MUST be a stable value — Gunicorn runs 2 workers, and without a shared key, sessions break

### Required `.env` Variables
See `.env.example` for the full list.

---

## Architecture

```
User → https (Nginx SSL/443) → Gunicorn (:8080) → Flask app
                                                       ↓
                                               Google OAuth
                                               (login_required)
                                                       ↓
                                               Dashboard/API
                                                       ↓
                                               SQLite (jobs.db)

Systemd Timer (15min) → src/main.py → Sources → Matcher (AI) → DB → Notifier
Systemd Timer (9AM PT) → src/digest.py → DB → Email digest
```

### Services on VM
| Service | Purpose | Restart Policy |
|---------|---------|---------------|
| `jobtracker-web` | Gunicorn dashboard (port 8080) | Always |
| `jobtracker-fetcher.timer` | Fetch jobs every 15 min | N/A (timer) |
| `jobtracker-digest.timer` | Daily digest at 9 AM PT (17:00 UTC) | N/A (timer) |
| `nginx` | Reverse proxy + SSL termination | Always |

---

## Key Features

### 1. Job Matching (src/matcher.py)
- **Primary API:** Google Gemini (`gemini-2.5-flash-lite`)
- **Fallback:** Anthropic Claude (`claude-haiku-4-5`)
- **Tiers:**
  - Tier 1 (Apply Now): Score >= 0.75
  - Tier 2 (Worth Tracking): Score 0.40-0.74
  - Tier 3 (Skip): Score < 0.40

### 2. Notifications (src/notifier.py)
- **Tier 1:** Instant Telegram + HTML email with apply button
- **Tier 2:** Daily digest email at 9 AM PT
- **Deduplication:** `notifications` table tracks (job_id, channel)

### 3. Dashboard (web/app.py)
- **Auth:** Google OAuth with email allowlist
- **Pages:** Fresh Jobs (last 3 days) / Archive (older)
- **Filters:** Tier, company, status, starred, search, sort
- **Actions:** Apply, generate cover letter, mark applied/dismissed, star

### 4. Cover Letter Generator
- AI-generated using Gemini (primary) / Claude (fallback)
- Output: Modal display + copy + download as PDF or DOCX
- Personalized using resume.txt + job description

---

## CLI Commands
```bash
python src/main.py                  # Normal run: fetch → match → notify
python src/main.py --test-sources   # Test job source adapters only
python src/main.py --test-match     # Test AI matching
python src/main.py --test-notify    # Send test notifications
python src/main.py --dry-run        # Fetch + match, skip notifications
python src/digest.py                # Send daily digest manually
python web/app.py                   # Run dashboard locally on :8080
```
