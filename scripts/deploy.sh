#!/bin/bash
set -euo pipefail

# Configuration
PROJECT_DIR="/home/ec2-user/fitted"
BRANCH="${1:-dev}"

echo "🚀 Starting deployment to EC2..."

# Navigate to project directory
cd "$PROJECT_DIR"

# Pull latest changes
echo "📥 Pulling latest changes from $BRANCH..."
git fetch origin
git checkout "$BRANCH"
git pull origin "$BRANCH"

# Ensure uv is installed
if ! command -v uv &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source $HOME/.cargo/env
fi

# Activate/Update virtual environment
echo "🐍 Updating virtual environment with uv..."
uv venv --python 3.11 --quiet
source .venv/bin/activate
uv pip install -r requirements-ec2.txt --quiet

# NOTE: Database migrations are intentionally excluded from automated deploy.
# Run manually when needed: ./.venv/bin/python scripts/db_migrate.py

# Restart services
echo "🔄 Restarting systemd services..."
sudo systemctl restart fitted-backend
sudo systemctl restart fitted-frontend

# Restart Caddy (if Caddyfile changed)
echo "🌐 Restarting Caddy..."
sudo systemctl restart caddy

echo "✅ Deployment complete!"
sudo systemctl status fitted-backend --no-pager
sudo systemctl status fitted-frontend --no-pager
