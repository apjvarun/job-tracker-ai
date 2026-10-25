# Deploy Job Tracker Alert to Google Compute Engine

## Overview

This deploys the entire Job Tracker Alert system to a single GCE VM (e2-micro, free tier eligible). The VM runs:

- **Gunicorn + Nginx** — serves the Flask dashboard on port 80
- **Systemd timer** — fetches jobs every 15 minutes (replaces cron)
- **Systemd timer** — sends daily digest at 9 AM PT

Estimated cost: **$0/month** on GCP free tier (e2-micro in us-west1).

---

## Prerequisites

### 1. Install the Google Cloud CLI

```bash
# macOS (Homebrew)
brew install --cask google-cloud-sdk

# Or download from: https://cloud.google.com/sdk/docs/install
```

### 2. Authenticate and set your project

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```

If you don't have a GCP project yet:

```bash
gcloud projects create job-tracker-2026 --name="Job Tracker"
gcloud config set project job-tracker-2026

# Enable billing (required for Compute Engine)
# Go to: https://console.cloud.google.com/billing
# Link your project to a billing account

# Enable Compute Engine API
gcloud services enable compute.googleapis.com
```

---

## Deploy (Automated)

From your project directory on your Mac:

```bash
cd ~/path/to/Job\ Tracker\ Alert
bash deploy/deploy-gce.sh
```

This script will:
1. Create an e2-micro VM in us-west1-b
2. Open HTTP/HTTPS firewall ports
3. Upload your app code
4. Install Python, Nginx, and all dependencies
5. Set up systemd services for the dashboard, fetcher, and digest
6. Print the external IP for your dashboard

---

## Deploy (Step-by-Step Manual)

If you prefer to do it yourself:

### Step 1: Create the VM

```bash
gcloud compute instances create job-tracker-vm \
    --zone=us-west1-b \
    --machine-type=e2-micro \
    --image-family=ubuntu-2204-lts \
    --image-project=ubuntu-os-cloud \
    --boot-disk-size=10GB \
    --tags=http-server,https-server
```

### Step 2: Open firewall for HTTP

```bash
gcloud compute firewall-rules create allow-http \
    --allow tcp:80 --target-tags http-server

gcloud compute firewall-rules create allow-https \
    --allow tcp:443 --target-tags https-server
```

### Step 3: Upload your code

```bash
# From your Mac
cd ~/path/to/Job\ Tracker\ Alert

# Create a tarball (excluding venv, db, etc.)
tar -czf /tmp/jobtracker.tar.gz \
    --exclude='venv' --exclude='__pycache__' \
    --exclude='data/*.db' --exclude='.git' \
    -C .. "Job Tracker Alert"

# Upload to VM
gcloud compute scp /tmp/jobtracker.tar.gz job-tracker-vm:/tmp/ --zone=us-west1-b
```

### Step 4: SSH in and set up

```bash
gcloud compute ssh job-tracker-vm --zone=us-west1-b
```

Then on the VM:

```bash
# Extract
sudo mkdir -p /opt/job-tracker-alert
sudo tar -xzf /tmp/jobtracker.tar.gz -C /opt/ --strip-components=1
sudo cp -r '/opt/Job Tracker Alert/'* /opt/job-tracker-alert/
sudo rm -rf '/opt/Job Tracker Alert'

# Run setup
cd /opt/job-tracker-alert
sudo bash deploy/setup-vm.sh
```

### Step 5: Configure your .env

```bash
sudo -u jobtracker nano /opt/job-tracker-alert/.env
```

Fill in your real keys:
- `ANTHROPIC_API_KEY` — from console.anthropic.com
- `TELEGRAM_BOT_TOKEN` — from @BotFather
- `TELEGRAM_CHAT_ID` — your chat ID
- `GMAIL_ADDRESS` — your Gmail
- `GMAIL_APP_PASSWORD` — 16-char app password (no spaces)

### Step 6: Test

```bash
# Test job fetching
sudo -u jobtracker /opt/job-tracker-alert/venv/bin/python \
    /opt/job-tracker-alert/src/main.py --test-sources

# Check dashboard
curl http://localhost

# Check services
sudo systemctl status jobtracker-web
sudo systemctl status jobtracker-fetcher.timer
```

### Step 7: Get your external IP

```bash
# From your Mac
gcloud compute instances describe job-tracker-vm \
    --zone=us-west1-b \
    --format="get(networkInterfaces[0].accessConfigs[0].natIP)"
```

Visit `http://THAT_IP` in your browser.

---

## Optional: Custom Domain + HTTPS

If you want `https://jobs.yourdomain.com`:

1. Point your domain's A record to the VM's external IP
2. SSH into the VM and run:

```bash
sudo certbot --nginx -d jobs.yourdomain.com
```

Certbot will auto-configure Nginx for HTTPS and set up auto-renewal.

---

## Managing the VM

```bash
# SSH in
gcloud compute ssh job-tracker-vm --zone=us-west1-b

# View fetcher logs
sudo journalctl -u jobtracker-fetcher --since "1 hour ago"

# View web server logs
sudo journalctl -u jobtracker-web -f

# Manually trigger a fetch
sudo -u jobtracker /opt/job-tracker-alert/venv/bin/python \
    /opt/job-tracker-alert/src/main.py

# Restart web server
sudo systemctl restart jobtracker-web

# Check timer schedules
systemctl list-timers --all | grep jobtracker

# Stop everything
sudo systemctl stop jobtracker-web jobtracker-fetcher.timer jobtracker-digest.timer

# Start everything
sudo systemctl start jobtracker-web jobtracker-fetcher.timer jobtracker-digest.timer
```

## Updating the App

When you make code changes locally:

```bash
# From your Mac — re-upload and restart
tar -czf /tmp/jobtracker.tar.gz \
    --exclude='venv' --exclude='__pycache__' \
    --exclude='data/*.db' --exclude='.git' \
    -C .. "Job Tracker Alert"

gcloud compute scp /tmp/jobtracker.tar.gz job-tracker-vm:/tmp/ --zone=us-west1-b

gcloud compute ssh job-tracker-vm --zone=us-west1-b --command="
    sudo tar -xzf /tmp/jobtracker.tar.gz -C /opt/ --strip-components=1
    sudo cp -r '/opt/Job Tracker Alert/'* /opt/job-tracker-alert/
    sudo rm -rf '/opt/Job Tracker Alert'
    sudo chown -R jobtracker:jobtracker /opt/job-tracker-alert
    sudo systemctl restart jobtracker-web
    echo 'Updated and restarted!'
"
```

## Cost

- **e2-micro** in us-west1: Free tier (1 instance, 30 GB disk)
- **Egress**: 1 GB/month free (dashboard traffic is tiny)
- **Total**: $0/month for typical usage

If you exceed free tier or want a static IP, costs are roughly $3-5/month.

## Stopping / Deleting

```bash
# Stop (no charges while stopped, disk charges still apply)
gcloud compute instances stop job-tracker-vm --zone=us-west1-b

# Start again
gcloud compute instances start job-tracker-vm --zone=us-west1-b

# Delete entirely (all data lost)
gcloud compute instances delete job-tracker-vm --zone=us-west1-b
```
