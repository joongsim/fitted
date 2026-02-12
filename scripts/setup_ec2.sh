#!/bin/bash
set -euo pipefail

# Install Caddy (not in AL2023 default repos)
# Idempotent -- skips if already installed by UserData
if ! command -v caddy &> /dev/null; then
    sudo dnf install -y 'dnf-command(copr)'
    sudo dnf copr enable -y @caddy/caddy
    sudo dnf install -y caddy
fi

# Clone the repo
cd /home/ec2-user
if [ ! -d "fitted" ]; then
    # Replace <your-user> with the actual repo URL or handle via git credentials
    # For now, we assume public or SSH key is set up
    echo "Cloning repository..."
    git clone https://github.com/joshua-simpson/fitted.git
else
    echo "Repository already exists, pulling latest..."
    cd fitted
    git pull
    cd ..
fi

cd fitted

# Create virtualenv with Python 3.11
if [ ! -d ".venv" ]; then
    python3.11 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-ec2.txt

# Create .env file (populate with actual values)
# Note: You will need to edit this file manually after running the script to add secrets!
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
    chmod 600 .env  # restrict to owner only -- contains secrets
    echo "IMPORTANT: Please edit .env with actual secrets!"
fi

# Run database migration
# (Will fail if DATABASE_URL is not set in .env yet)
if grep -q "<password>" .env; then
    echo "Skipping migration - .env needs to be configured first."
else
    python scripts/db_migrate.py
fi

# Install systemd services
sudo cp infra/systemd/fitted-backend.service /etc/systemd/system/
sudo cp infra/systemd/fitted-frontend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable fitted-backend fitted-frontend
sudo systemctl restart fitted-backend fitted-frontend

# Configure Caddy
sudo cp infra/Caddyfile /etc/caddy/Caddyfile
sudo systemctl restart caddy

IPV6_ADDR=$(curl -6 -s ifconfig.me || echo "unknown")
echo "Setup complete. App should be running at http://[$IPV6_ADDR]/"
