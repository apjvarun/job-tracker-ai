#!/bin/bash
# ============================================================
# Job Tracker Alert — Setup Script
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "============================================"
echo "  Job Tracker Alert — Setup"
echo "============================================"
echo ""

cd "$PROJECT_DIR"

# 1. Check Python
echo "Checking Python..."
if command -v python3 &> /dev/null; then
    PYTHON=python3
elif command -v python &> /dev/null; then
    PYTHON=python
else
    echo "❌ Python 3 not found. Please install Python 3.9+."
    exit 1
fi
echo "✅ Using $($PYTHON --version)"

# 2. Create virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    $PYTHON -m venv venv
    echo "✅ Virtual environment created"
else
    echo "✅ Virtual environment already exists"
fi

# 3. Install dependencies
echo "Installing dependencies..."
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "✅ Dependencies installed"

# 4. Create directories
mkdir -p data logs

# 5. Set up .env
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "⚠️  Please edit .env with your API keys:"
    echo "   $PROJECT_DIR/.env"
    echo ""
    echo "   You'll need:"
    echo "   1. Anthropic API key (console.anthropic.com)"
    echo "   2. Telegram bot token (@BotFather on Telegram)"
    echo "   3. Telegram chat ID"
    echo "   4. Gmail address + app password"
    echo ""
else
    echo "✅ .env already exists"
fi

# 6. Initialize database
echo "Initializing database..."
source venv/bin/activate
$PYTHON -c "
import sys; sys.path.insert(0, '.')
from src.database import JobDatabase
db = JobDatabase('data/jobs.db')
print('✅ Database initialized')
"

echo ""
echo "============================================"
echo "  Setup complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Edit .env with your API keys"
echo "  2. Edit config.yaml to customize companies"
echo "  3. Test sources:  venv/bin/python src/main.py --test-sources"
echo "  4. Test matching: venv/bin/python src/main.py --test-match"
echo "  5. Test notify:   venv/bin/python src/main.py --test-notify"
echo "  6. First run:     venv/bin/python src/main.py"
echo "  7. Dashboard:     venv/bin/python web/app.py"
echo "  8. Install cron:  bash scripts/install_cron.sh"
echo ""
