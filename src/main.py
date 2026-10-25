#!/usr/bin/env python3
"""
Instant Job Alert System — Main Orchestrator

This script is the entry point for the cron job. It:
1. Loads config and resume
2. Fetches new jobs from all enabled company sources
3. Deduplicates against the database
4. Matches new jobs against the resume using Claude API
5. Sends instant notifications for Tier 1 matches
6. Stores everything in the database

Usage:
    python src/main.py                  # Normal run (poll + match + notify)
    python src/main.py --test-sources   # Test job sourcing only
    python src/main.py --test-match     # Test matching on a sample job
    python src/main.py --test-notify    # Send test notifications
    python src/main.py --dry-run        # Fetch + match but don't notify
"""

import os
import sys
import yaml
import logging
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.database import JobDatabase, Job
from src.sources.base import RawJob
from src.sources.lever import LeverSource
from src.sources.greenhouse import GreenhouseSource
from src.sources.workday import GoogleCareersSource, MetaCareersSource
from src.sources.netflix import NetflixSource
from src.sources.stripe_source import StripeSource
from src.sources.ashby import AshbySource
from src.sources.smartrecruiters import SmartRecruitersSource
from src.sources.workday_ats import WorkdaySource
from src.matcher import JobMatcher, MatchResult
from src.notifier import TelegramNotifier, EmailNotifier


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(log_level: str = "INFO"):
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_dir / "job_alerts.log"),
        ]
    )


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config() -> Dict[str, Any]:
    """Load config.yaml and .env."""
    load_dotenv(PROJECT_ROOT / ".env")

    config_path = PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        logger.error(f"Config file not found: {config_path}")
        sys.exit(1)

    with open(config_path) as f:
        config = yaml.safe_load(f)

    return config


def load_resume(config: Dict[str, Any]) -> str:
    """Load the resume text file."""
    resume_path = PROJECT_ROOT / config["settings"]["resume_path"]
    if not resume_path.exists():
        logger.error(f"Resume not found: {resume_path}")
        sys.exit(1)

    return resume_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Source factory
# ---------------------------------------------------------------------------

SOURCE_MAP = {
    "lever": LeverSource,
    "greenhouse": GreenhouseSource,
    "ashby": AshbySource,
    "smartrecruiters": SmartRecruitersSource,
    "workday": WorkdaySource,
    "google_careers": GoogleCareersSource,
    "meta_careers": MetaCareersSource,
    "netflix_eightfold": NetflixSource,
    "stripe_careers": StripeSource,
}


def create_sources(config: Dict[str, Any]) -> List:
    """Create job source adapters from config."""
    sources = []
    for company in config.get("companies", []):
        if not company.get("enabled", True):
            continue

        source_type = company["source_type"]
        cls = SOURCE_MAP.get(source_type)

        if cls is None:
            logger.warning(f"Unknown source type '{source_type}' for {company['name']}")
            continue

        source = cls(
            company_name=company["name"],
            config=company.get("source_config", {}),
            search_keywords=company.get("search_keywords", ["product manager"]),
            target_locations=company.get("target_locations", []),
        )
        sources.append(source)

    return sources


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def fetch_all_jobs(sources: List) -> List[RawJob]:
    """Fetch jobs from all sources."""
    all_jobs = []
    for source in sources:
        try:
            jobs = source.fetch_jobs()
            logger.info(f"[{source.company_name}] Fetched {len(jobs)} matching jobs")
            all_jobs.extend(jobs)
        except Exception as e:
            logger.error(f"[{source.company_name}] Source error: {e}")
    return all_jobs


def process_new_jobs(raw_jobs: List[RawJob], db: JobDatabase,
                     matcher: Optional[JobMatcher] = None,
                     dry_run: bool = False) -> List[Dict]:
    """Process raw jobs: deduplicate, match, and store."""
    new_matched_jobs = []

    for raw in raw_jobs:
        job = Job(
            company=raw.company,
            title=raw.title,
            url=raw.url,
            location=raw.location,
            description=raw.description,
            salary=raw.salary,
        )

        # Skip if already seen
        if db.job_exists(job.description_hash):
            continue

        # Insert into database
        job_id = db.insert_job(job)
        if not job_id:
            continue

        logger.info(f"NEW JOB: [{job.company}] {job.title} — {job.location}")

        # Match against resume
        if matcher:
            result = matcher.match(
                company=job.company,
                title=job.title,
                location=job.location,
                description=job.description,
                salary=job.salary,
            )
            db.update_match(job_id, result.tier, result.score, result.reason)
            logger.info(f"  → Tier {result.tier} | Score {result.score:.2f} | {result.reason}")

            job_dict = {
                "id": job_id,
                "company": job.company,
                "title": job.title,
                "url": job.url,
                "location": job.location,
                "match_tier": result.tier,
                "match_score": result.score,
                "match_reason": result.reason,
            }
            new_matched_jobs.append(job_dict)
        else:
            logger.info("  → Skipping match (no matcher configured)")

    return new_matched_jobs


def send_notifications(jobs: List[Dict], telegram: Optional[TelegramNotifier],
                       email: Optional[EmailNotifier], db: JobDatabase,
                       dry_run: bool = False):
    """Send instant notifications for Tier 1 jobs."""
    tier1_jobs = [j for j in jobs if j.get("match_tier") == 1]

    if not tier1_jobs:
        logger.info("No Tier 1 jobs to notify about")
        return

    logger.info(f"🎯 {len(tier1_jobs)} Tier 1 jobs to notify!")

    for job in tier1_jobs:
        if dry_run:
            logger.info(f"  [DRY RUN] Would notify: {job['title']} at {job['company']}")
            continue

        # Send Telegram
        if telegram:
            if not db.was_notified(job["id"], "telegram"):
                success = telegram.send_tier1_alert(job)
                if success:
                    db.record_notification(job["id"], "telegram")

        # Send Email
        if email:
            if not db.was_notified(job["id"], "email"):
                success = email.send_tier1_alert(job)
                if success:
                    db.record_notification(job["id"], "email")


# ---------------------------------------------------------------------------
# Test modes
# ---------------------------------------------------------------------------

def test_sources(config: Dict[str, Any]):
    """Test job sourcing from all enabled companies."""
    sources = create_sources(config)
    print(f"\n{'='*60}")
    print(f"Testing {len(sources)} job sources...")
    print(f"{'='*60}\n")

    for source in sources:
        try:
            jobs = source.fetch_jobs()
            print(f"✅ {source.company_name}: {len(jobs)} PM jobs found")
            for job in jobs[:3]:  # Show first 3
                print(f"   • {job.title} — {job.location}")
                print(f"     {job.url}")
            if len(jobs) > 3:
                print(f"   ... and {len(jobs) - 3} more")
        except Exception as e:
            print(f"❌ {source.company_name}: Error — {e}")
        print()


def test_match(config: Dict[str, Any]):
    """Test the matching engine with a sample job."""
    api_key = os.getenv("GOOGLE_API_KEY")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key and not anthropic_key:
        print("❌ Neither GOOGLE_API_KEY nor ANTHROPIC_API_KEY set in .env")
        return

    resume = load_resume(config)
    model = config.get("matching", {}).get("model", "gemini-2.5-flash-lite")
    matcher = JobMatcher(api_key=api_key, resume_text=resume, model=model,
                         anthropic_api_key=anthropic_key)

    # Test with a sample PM job
    sample = {
        "company": "TestCorp",
        "title": "Senior Product Manager, Cloud Platform",
        "location": "San Francisco, CA",
        "description": (
            "We're looking for a Senior Product Manager to lead our cloud platform strategy. "
            "You'll own the product roadmap for our multi-cloud data protection platform, "
            "working with engineering teams across AWS, Azure, and GCP. "
            "Requirements: 7+ years of product management experience, "
            "experience with cloud infrastructure or data management products, "
            "strong technical background, MBA preferred."
        ),
    }

    print(f"\n{'='*60}")
    print("Testing matching engine...")
    print(f"{'='*60}\n")
    print(f"Sample job: {sample['title']} at {sample['company']}")
    print(f"Location: {sample['location']}\n")

    result = matcher.match(**sample)
    print(f"Result:")
    print(f"  Tier:   {result.tier}")
    print(f"  Score:  {result.score:.2f}")
    print(f"  Reason: {result.reason}")


def test_notify(config: Dict[str, Any]):
    """Send test notifications via Telegram and Email."""
    print(f"\n{'='*60}")
    print("Testing notifications...")
    print(f"{'='*60}\n")

    # Test Telegram
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if token and chat_id:
        tg = TelegramNotifier(token, chat_id)
        success = tg.send_test_message()
        print(f"{'✅' if success else '❌'} Telegram: {'sent' if success else 'failed'}")
    else:
        print("⏭️  Telegram: skipped (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set)")

    # Test Email
    gmail = os.getenv("GMAIL_ADDRESS")
    app_pw = os.getenv("GMAIL_APP_PASSWORD")
    recipient = config.get("notifications", {}).get("email", {}).get("recipient", gmail)
    if gmail and app_pw:
        em = EmailNotifier(gmail, app_pw, recipient)
        success = em.send_test_email()
        print(f"{'✅' if success else '❌'} Email: {'sent to ' + recipient if success else 'failed'}")
    else:
        print("⏭️  Email: skipped (GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Instant Job Alert System")
    parser.add_argument("--test-sources", action="store_true", help="Test job sourcing")
    parser.add_argument("--test-match", action="store_true", help="Test matching engine")
    parser.add_argument("--test-notify", action="store_true", help="Test notifications")
    parser.add_argument("--dry-run", action="store_true", help="Fetch + match, but don't notify")
    args = parser.parse_args()

    config = load_config()
    setup_logging(config.get("settings", {}).get("log_level", "INFO"))

    # Handle test modes
    if args.test_sources:
        test_sources(config)
        return
    if args.test_match:
        test_match(config)
        return
    if args.test_notify:
        test_notify(config)
        return

    # Normal run
    logger.info("=" * 50)
    logger.info(f"Job Alert System — Run at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 50)

    # Initialize components
    db_path = str(PROJECT_ROOT / config["settings"]["database_path"])
    db = JobDatabase(db_path)

    resume = load_resume(config)

    # Matcher
    api_key = os.getenv("GOOGLE_API_KEY")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    matcher = None
    if api_key or anthropic_key:
        model = config.get("matching", {}).get("model", "gemini-2.5-flash-lite")
        matcher = JobMatcher(api_key=api_key or "", resume_text=resume, model=model,
                             anthropic_api_key=anthropic_key)
    else:
        logger.warning("No API keys set — matching disabled")

    # Notifiers
    telegram = None
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if token and chat_id and config.get("notifications", {}).get("telegram", {}).get("enabled"):
        telegram = TelegramNotifier(token, chat_id)

    email = None
    gmail = os.getenv("GMAIL_ADDRESS")
    app_pw = os.getenv("GMAIL_APP_PASSWORD")
    if gmail and app_pw and config.get("notifications", {}).get("email", {}).get("enabled"):
        recipient = config["notifications"]["email"].get("recipient", gmail)
        email = EmailNotifier(gmail, app_pw, recipient)

    # Pipeline: fetch → deduplicate → match → notify
    sources = create_sources(config)
    raw_jobs = fetch_all_jobs(sources)
    logger.info(f"Total jobs fetched: {len(raw_jobs)}")

    new_jobs = process_new_jobs(raw_jobs, db, matcher, dry_run=args.dry_run)
    logger.info(f"New jobs processed: {len(new_jobs)}")

    if not args.dry_run:
        send_notifications(new_jobs, telegram, email, db)

    # Summary
    tier_counts = {1: 0, 2: 0, 3: 0}
    for j in new_jobs:
        tier = j.get("match_tier", 3)
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    logger.info(f"Summary: Tier 1={tier_counts[1]}, Tier 2={tier_counts[2]}, Tier 3={tier_counts[3]}")
    logger.info("Done.")


if __name__ == "__main__":
    main()
