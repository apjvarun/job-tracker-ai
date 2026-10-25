#!/bin/bash
# ============================================================
# Job Tracker Alert — Deploy to Google Compute Engine
#
# Prerequisites:
#   1. Install gcloud CLI: https://cloud.google.com/sdk/docs/install
#   2. Run: gcloud auth login
#   3. Run: gcloud config set project YOUR_PROJECT_ID
#
# Usage: bash deploy/deploy-gce.sh
# ============================================================
set -euo pipefail

# --- Configuration (edit these) ---
PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
VM_NAME="job-tracker-vm"
ZONE="us-west1-b"          # Close to Santa Clara
MACHINE_TYPE="e2-micro"    # Free tier eligible
# ---

echo ""
echo "============================================"
echo "  Job Tracker Alert — GCE Deployment"
echo "============================================"
echo "  Project:  $PROJECT_ID"
echo "  VM:       $VM_NAME"
echo "  Zone:     $ZONE"
echo "  Machine:  $MACHINE_TYPE (free tier eligible)"
echo "============================================"
echo ""

# Step 1: Create the VM
echo "=== [1/4] Creating VM instance ==="
if gcloud compute instances describe "$VM_NAME" --zone="$ZONE" &>/dev/null; then
    echo "VM '$VM_NAME' already exists. Skipping creation."
else
    gcloud compute instances create "$VM_NAME" \
        --zone="$ZONE" \
        --machine-type="$MACHINE_TYPE" \
        --image-family=ubuntu-2204-lts \
        --image-project=ubuntu-os-cloud \
        --boot-disk-size=10GB \
        --boot-disk-type=pd-standard \
        --tags=http-server,https-server \
        --metadata=startup-script='#!/bin/bash
            apt-get update -qq
            apt-get install -y -qq python3 python3-pip python3-venv nginx git'
    echo "VM created. Waiting 30s for startup..."
    sleep 30
fi

# Step 2: Create firewall rules for HTTP/HTTPS
echo "=== [2/4] Configuring firewall rules ==="
if gcloud compute firewall-rules describe allow-http &>/dev/null 2>&1; then
    echo "HTTP firewall rule already exists."
else
    gcloud compute firewall-rules create allow-http \
        --allow tcp:80 \
        --target-tags http-server \
        --description "Allow HTTP traffic" \
        --direction INGRESS
fi

if gcloud compute firewall-rules describe allow-https &>/dev/null 2>&1; then
    echo "HTTPS firewall rule already exists."
else
    gcloud compute firewall-rules create allow-https \
        --allow tcp:443 \
        --target-tags https-server \
        --description "Allow HTTPS traffic" \
        --direction INGRESS
fi

# Step 3: Upload app files to the VM
echo "=== [3/4] Uploading app files to VM ==="
# Get the script's directory to find the project root
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Uploading from: $PROJECT_DIR"

# Create a tarball excluding unnecessary files
TMPTAR=$(mktemp /tmp/jobtracker-XXXXXX.tar.gz)
tar -czf "$TMPTAR" \
    -C "$(dirname "$PROJECT_DIR")" \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='venv' \
    --exclude='.git' \
    --exclude='data/*.db' \
    --exclude='logs/*' \
    "$(basename "$PROJECT_DIR")"

gcloud compute scp "$TMPTAR" "$VM_NAME:/tmp/jobtracker.tar.gz" --zone="$ZONE"
rm -f "$TMPTAR"

# Extract on the VM
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --command="
    sudo mkdir -p /opt/job-tracker-alert
    sudo tar -xzf /tmp/jobtracker.tar.gz -C /opt/ --strip-components=1
    # The tar extracts as 'Job Tracker Alert', move contents
    if [ -d '/opt/Job Tracker Alert' ]; then
        sudo cp -r '/opt/Job Tracker Alert/'* /opt/job-tracker-alert/
        sudo rm -rf '/opt/Job Tracker Alert'
    fi
    rm /tmp/jobtracker.tar.gz
    echo 'Files uploaded to /opt/job-tracker-alert'
    ls /opt/job-tracker-alert/
"

# Step 4: Run the setup script on the VM
echo "=== [4/4] Running setup on VM ==="
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --command="
    cd /opt/job-tracker-alert
    sudo bash deploy/setup-vm.sh
"

# Get the external IP
EXTERNAL_IP=$(gcloud compute instances describe "$VM_NAME" \
    --zone="$ZONE" \
    --format="get(networkInterfaces[0].accessConfigs[0].natIP)")

echo ""
echo "============================================"
echo "  Deployment complete!"
echo "============================================"
echo ""
echo "  Dashboard: http://$EXTERNAL_IP"
echo "  SSH:       gcloud compute ssh $VM_NAME --zone=$ZONE"
echo ""
echo "  IMPORTANT: SSH in and edit your .env with real API keys:"
echo "    gcloud compute ssh $VM_NAME --zone=$ZONE"
echo "    sudo -u jobtracker nano /opt/job-tracker-alert/.env"
echo ""
echo "  Then test:"
echo "    sudo -u jobtracker /opt/job-tracker-alert/venv/bin/python \\"
echo "        /opt/job-tracker-alert/src/main.py --test-sources"
echo ""
echo "  (Optional) Set up HTTPS with a custom domain:"
echo "    sudo certbot --nginx -d yourdomain.com"
echo ""
