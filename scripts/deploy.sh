#!/bin/bash
set -euo pipefail

# Configuration
PROJECT_DIR="/home/ec2-user/fitted"
BRANCH="${1:-dev}"

echo "🚀 Starting deployment to EC2..."

# Navigate to project directory
cd "$PROJECT_DIR"

# Pull latest changes, then re-exec so the updated script version runs.
# (Bash may buffer the old file in memory before git pull rewrites it.)
if [[ "${_FITTED_UPDATED:-false}" != "true" ]]; then
    echo "📥 Pulling latest changes from $BRANCH..."
    git fetch origin
    git checkout "$BRANCH"
    git pull origin "$BRANCH"
    exec env _FITTED_UPDATED=true bash "$0" "$@"
fi

# Ensure uv is installed
if ! command -v uv &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source $HOME/.cargo/env
fi

# Activate/Update virtual environment
echo "🐍 Updating virtual environment with uv..."
[ -d .venv ] || uv venv --python 3.11 --quiet
source .venv/bin/activate
uv pip install -r requirements-ec2.txt --quiet

# NOTE: Database migrations are intentionally excluded from automated deploy.
# Run manually when needed: ./.venv/bin/python scripts/db_migrate.py

# Sync systemd service files
echo "⚙️  Syncing systemd service files..."
sudo cp "$PROJECT_DIR/infra/systemd/fitted-backend.service" /etc/systemd/system/
sudo cp "$PROJECT_DIR/infra/systemd/fitted-frontend.service" /etc/systemd/system/
sudo systemctl daemon-reload

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
