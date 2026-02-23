#!/bin/bash
set -euo pipefail

# # --- 1. SSH Pre-flight Check ---
# # Ensure we can talk to GitHub via SSH. 
# # This also adds github.com to known_hosts so the script doesn't hang.
# echo "Checking GitHub SSH connectivity..."
# ssh-keyscan -t ed25519 github.com >> ~/.ssh/known_hosts 2>/dev/null
# if ssh -T git@github.com 2>&1 | grep -q "successfully authenticated"; then
#     echo "✅ GitHub SSH connection verified."
# else
#     echo "❌ Error: SSH authentication to GitHub failed."
#     exit 1
# fi

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
if ! command -v uv &> /dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source "$HOME/.local/bin/env"
fi

uv venv
uv pip install -r requirements-ec2.txt

# --- 5. Systemd & Caddy ---
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