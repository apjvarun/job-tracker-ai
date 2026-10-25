#!/bin/bash
# ============================================================
# Job Tracker Alert — Cron Installation
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON="$PROJECT_DIR/venv/bin/python"

echo "============================================"
echo "  Job Tracker Alert — Cron Setup"
echo "============================================"
echo ""

# Check if venv exists
if [ ! -f "$PYTHON" ]; then
    echo "❌ Virtual environment not found. Run setup.sh first."
    exit 1
fi

# Cron can't handle spaces in paths directly, so use a wrapper approach
# Escape spaces in paths for cron
ESCAPED_DIR=$(echo "$PROJECT_DIR" | sed 's/ /\\ /g')
ESCAPED_PYTHON=$(echo "$PYTHON" | sed 's/ /\\ /g')

# Define cron entries with properly escaped paths
POLL_CRON="*/15 7-22 * * * cd \"$PROJECT_DIR\" && \"$PYTHON\" src/main.py >> \"$PROJECT_DIR/logs/cron.log\" 2>&1"
DIGEST_CRON="0 9 * * * cd \"$PROJECT_DIR\" && \"$PYTHON\" src/digest.py >> \"$PROJECT_DIR/logs/digest.log\" 2>&1"

# Tag for identification
TAG="# JOB_TRACKER_ALERT"

# Remove old entries if they exist
CURRENT_CRON=$(crontab -l 2>/dev/null || echo "")
CLEAN_CRON=$(echo "$CURRENT_CRON" | grep -v "JOB_TRACKER_ALERT" | grep -v "job_tracker" || true)

# Add new entries
NEW_CRON="$CLEAN_CRON
$TAG — Poll every 15 min (7 AM - 10 PM)
$POLL_CRON
$TAG — Daily digest at 9 AM
$DIGEST_CRON"

echo "$NEW_CRON" | crontab -

echo "✅ Cron jobs installed!"
echo ""
echo "Verify with: crontab -l"
echo ""
echo "To remove: crontab -l | grep -v JOB_TRACKER_ALERT | crontab -"
echo ""
