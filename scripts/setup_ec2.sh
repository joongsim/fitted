#!/bin/bash
set -euo pipefail

# --- 1. SSH Pre-flight Check ---
# Ensure we can talk to GitHub via SSH. 
# This also adds github.com to known_hosts so the script doesn't hang.
echo "Checking GitHub SSH connectivity..."
ssh-keyscan -t ed25519 github.com >> ~/.ssh/known_hosts 2>/dev/null
if ! ssh -T git@github.com 2>&1 | grep -q "successfully authenticated"; then
    echo "❌ Error: SSH authentication to GitHub failed."
    echo "Please ensure your Deploy Key is added to GitHub and the private key is on this EC2."
    exit 1
fi

# --- 2. Install Caddy ---
if ! command -v caddy &> /dev/null; then
    sudo dnf install -y 'dnf-command(copr)'
    sudo dnf copr enable -y @caddy/caddy epel-9-aarch64
    sudo dnf install -y caddy
fi

# --- 3. Clone/Update Repo (Using SSH URL) ---
cd /home/ec2-user
SSH_REPO_URL="git@github.com:joongsim/fitted.git"

if [ ! -d "fitted" ]; then
    echo "Cloning repository via SSH..."
    git clone "$SSH_REPO_URL"
    cd fitted
else
    echo "Repository already exists, pulling latest..."
    cd fitted
    # Ensure the remote is set to SSH in case it was previously HTTPS
    git remote set-url origin "$SSH_REPO_URL"
    git pull
fi

# --- 4. Python Environment ---
if [ ! -d ".venv" ]; then
    python3.11 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-ec2.txt

# --- 5. Environment Variables ---
if [ ! -f ".env" ]; then
    echo "Creating .env template..."
    cat > .env << 'ENVFILE'
DATABASE_URL=postgresql://fitted_admin:<password>@<rds-endpoint>:5432/fitted
WEATHER_BUCKET_NAME=<bucket-name>
OPENROUTER_API_KEY=<key>
WEATHER_API_KEY=<key>
IS_LOCAL=false
API_BASE_URL=http://localhost:8000
ENVFILE
    chmod 600 .env
    echo "IMPORTANT: Please edit .env with actual secrets!"
fi

# --- 6. Database Migration ---
if grep -q "<password>" .env; then
    echo "Skipping migration - .env needs to be configured first."
else
    python scripts/db_migrate.py
fi

# --- 7. Systemd & Caddy ---
# Note: Ensure these files exist in your repo at these paths!
sudo cp infra/systemd/fitted-backend.service /etc/systemd/system/
sudo cp infra/systemd/fitted-frontend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable fitted-backend fitted-frontend
sudo systemctl restart fitted-backend fitted-frontend

sudo cp infra/Caddyfile /etc/caddy/Caddyfile
sudo systemctl restart caddy

IPV4_ADDR=$(curl -4 -s ifconfig.me || echo "unknown")
echo "Setup complete. App should be running at http://$IPV4_ADDR/"