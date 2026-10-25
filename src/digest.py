#!/usr/bin/env python3
"""
Daily Digest Sender

Sends a summary email of Tier 2 (and optionally Tier 3) jobs
detected in the last 24 hours.

Run via cron at 9 AM daily:
    0 9 * * * cd /path/to/project && ./venv/bin/python src/digest.py
"""

import os
import sys
import yaml
import logging
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.database import JobDatabase
from src.notifier import EmailNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(PROJECT_ROOT / "logs" / "digest.log"),
    ]
)
logger = logging.getLogger(__name__)


def main():
    load_dotenv(PROJECT_ROOT / ".env")

    with open(PROJECT_ROOT / "config.yaml") as f:
        config = yaml.safe_load(f)

    db_path = str(PROJECT_ROOT / config["settings"]["database_path"])
    db = JobDatabase(db_path)

    gmail = os.getenv("GMAIL_ADDRESS")
    app_pw = os.getenv("GMAIL_APP_PASSWORD")

    if not gmail or not app_pw:
        logger.error("GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set")
        return

    recipient = config.get("notifications", {}).get("email", {}).get("recipient", gmail)
    email = EmailNotifier(gmail, app_pw, recipient)

    # Get undigested Tier 2 jobs from the last 24 hours
    jobs = db.get_digest_jobs(hours=24)
    logger.info(f"Found {len(jobs)} jobs for digest")

    if not jobs:
        logger.info("No new Tier 2/3 jobs — skipping digest")
        return

    success = email.send_digest(jobs)
    if success:
        # Mark all these jobs as digested
        for job in jobs:
            db.record_notification(job["id"], "digest")
        logger.info(f"Digest sent with {len(jobs)} jobs")
    else:
        logger.error("Failed to send digest email")


if __name__ == "__main__":
    main()
