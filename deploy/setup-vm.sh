#!/bin/bash
# ============================================================
# Job Tracker Alert — VM Setup Script
# Run this ON the GCE VM after SSH'ing in.
# Usage: bash setup-vm.sh
# ============================================================
set -euo pipefail

APP_DIR="/opt/job-tracker-alert"
APP_USER="jobtracker"

echo "=== [1/7] Installing system packages ==="
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-pip python3-venv nginx certbot python3-certbot-nginx git

echo "=== [2/7] Creating app user ==="
if ! id "$APP_USER" &>/dev/null; then
    sudo useradd -r -m -s /bin/bash "$APP_USER"
    echo "Created user: $APP_USER"
else
    echo "User $APP_USER already exists"
fi

echo "=== [3/7] Setting up app directory ==="
sudo mkdir -p "$APP_DIR"
sudo chown "$APP_USER:$APP_USER" "$APP_DIR"

# If the code is in the current directory, copy it
if [ -f "config.yaml" ]; then
    echo "Copying app files to $APP_DIR..."
    sudo cp -r ./* "$APP_DIR/"
    sudo chown -R "$APP_USER:$APP_USER" "$APP_DIR"
else
    echo "NOTE: No app files found in current directory."
    echo "You'll need to copy your project files to $APP_DIR manually."
    echo "Example: scp -r ~/Job\\ Tracker\\ Alert/* your-vm-ip:$APP_DIR/"
fi

echo "=== [4/7] Creating Python venv and installing deps ==="
sudo -u "$APP_USER" bash -c "
    cd $APP_DIR
    python3 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
    pip install gunicorn
"

echo "=== [5/7] Creating data directory ==="
sudo -u "$APP_USER" mkdir -p "$APP_DIR/data"
sudo -u "$APP_USER" mkdir -p "$APP_DIR/logs"

echo "=== [6/7] Setting up .env file ==="
if [ ! -f "$APP_DIR/.env" ]; then
    if [ -f "$APP_DIR/.env.example" ]; then
        sudo -u "$APP_USER" cp "$APP_DIR/.env.example" "$APP_DIR/.env"
        echo "Created .env from .env.example — EDIT IT with your real keys!"
        echo "  sudo -u $APP_USER nano $APP_DIR/.env"
    else
        echo "WARNING: No .env.example found. Create $APP_DIR/.env manually."
    fi
else
    echo ".env already exists"
fi

echo "=== [7/7] Installing systemd services ==="
# Copy service files
sudo cp "$APP_DIR/deploy/jobtracker-web.service" /etc/systemd/system/
sudo cp "$APP_DIR/deploy/jobtracker-fetcher.service" /etc/systemd/system/
sudo cp "$APP_DIR/deploy/jobtracker-fetcher.timer" /etc/systemd/system/
sudo cp "$APP_DIR/deploy/jobtracker-digest.service" /etc/systemd/system/
sudo cp "$APP_DIR/deploy/jobtracker-digest.timer" /etc/systemd/system/

# Copy nginx config
sudo cp "$APP_DIR/deploy/nginx-jobtracker.conf" /etc/nginx/sites-available/jobtracker
sudo ln -sf /etc/nginx/sites-available/jobtracker /etc/nginx/sites-enabled/jobtracker
sudo rm -f /etc/nginx/sites-enabled/default

# Reload and start services
sudo systemctl daemon-reload
sudo systemctl enable --now jobtracker-web
sudo systemctl enable --now jobtracker-fetcher.timer
sudo systemctl enable --now jobtracker-digest.timer
sudo systemctl restart nginx

echo ""
echo "============================================"
echo "  Setup complete!"
echo "============================================"
echo ""
echo "Services running:"
echo "  - jobtracker-web     (Flask dashboard via Gunicorn)"
echo "  - jobtracker-fetcher (runs every 15 min)"
echo "  - jobtracker-digest  (runs daily at 9 AM PT)"
echo ""
echo "Next steps:"
echo "  1. Edit your .env:  sudo -u $APP_USER nano $APP_DIR/.env"
echo "  2. Test fetcher:    sudo -u $APP_USER $APP_DIR/venv/bin/python $APP_DIR/src/main.py --test-sources"
echo "  3. Check dashboard: curl http://localhost"
echo "  4. (Optional) Set up HTTPS: sudo certbot --nginx -d your-domain.com"
echo ""
echo "Useful commands:"
echo "  sudo systemctl status jobtracker-web"
echo "  sudo journalctl -u jobtracker-fetcher -f"
echo "  sudo journalctl -u jobtracker-web -f"
echo ""
