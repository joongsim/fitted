#!/bin/bash
set -euo pipefail

# Configuration
PROJECT_DIR="/home/ec2-user/fitted"
BRANCH="feature/add-users"

echo "🚀 Starting deployment to EC2..."

# Navigate to project directory
cd "$PROJECT_DIR"

# Pull latest changes
echo "📥 Pulling latest changes from $BRANCH..."
git fetch origin
git checkout "$BRANCH"
git pull origin "$BRANCH"

# Activate virtual environment
echo "🐍 Activating virtual environment..."
if [ -d ".venv" ]; then
    source .venv/bin/activate
else
    echo "❌ Virtual environment not found. Please run setup_ec2.sh first."
    exit 1
fi

# Install/Update dependencies
echo "📦 Updating dependencies..."
pip install -r requirements-ec2.txt --quiet

# Run database migrations
echo "🗄️ Running database migrations..."
# Load environment variables from .env if it exists
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | xargs)
fi

python scripts/db_migrate.py

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
