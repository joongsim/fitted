#!/bin/bash
set -euo pipefail

# --- Configuration ---
RDS_ENDPOINT="fitted-infra-fitteddb-iqqx1aiplhxv.cle6qaquk5at.us-west-1.rds.amazonaws.com"
LOCAL_DB_PORT=5432

# You can override these by setting them in your shell before running the script
export DATABASE_URL="${DATABASE_URL:-postgresql://fitted_admin:password@localhost:$LOCAL_DB_PORT/fitted}"
export JWT_SECRET_KEY="${JWT_SECRET_KEY:-dev-secret-key-change-me}"
export DEV_MODE="${DEV_MODE:-true}"
export API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
export DEV="${DEV:-true}"

echo "🚀 Starting Fitted App locally..."
echo "📂 Project Root: $(pwd)"
echo "🗄️ Database: $DATABASE_URL"
echo "🛠️ Dev Mode: $DEV_MODE"

# Ensure virtual environment is active
if [ -d ".venv" ]; then
    source .venv/bin/activate
else
    echo "❌ Virtual environment (.venv) not found. Please run 'uv venv' first."
    exit 1
fi

# Function to kill all background processes on exit
cleanup() {
    echo ""
    echo "🛑 Shutting down..."
    kill $TUNNEL_PID $BACKEND_PID $FRONTEND_PID 2>/dev/null
    wait $TUNNEL_PID $BACKEND_PID $FRONTEND_PID 2>/dev/null
    exit
}

trap cleanup SIGINT SIGTERM

# 1. Start SSH Tunnel to RDS
if lsof -Pi :$LOCAL_DB_PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "⚠️ Port $LOCAL_DB_PORT already in use. Assuming SSH tunnel or local Postgres is running."
else
    echo "🌉 Starting SSH tunnel to RDS..."
    ssh -N -L $LOCAL_DB_PORT:$RDS_ENDPOINT:5432 fitted &
    TUNNEL_PID=$!
    sleep 2

    # Verify tunnel is up
    if ! kill -0 $TUNNEL_PID 2>/dev/null; then
        echo "❌ SSH tunnel failed to start. Is your SSH key configured? (see ~/.ssh/config)"
        exit 1
    fi
    echo "✅ SSH tunnel established."
fi

# 2. Start Backend (FastAPI)
echo "🔌 Starting Backend on port 8000..."
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

sleep 2

# 3. Start Frontend (FastHTML)
echo "🎨 Starting Frontend on port 5001..."
python frontend/app.py &
FRONTEND_PID=$!

echo ""
echo "✅ All services running!"
echo "🌉 SSH Tunnel: localhost:$LOCAL_DB_PORT -> RDS"
echo "🔗 Backend API: http://localhost:8000/docs"
echo "🔗 Frontend UI: http://localhost:5001"
echo "💡 Press Ctrl+C to stop everything."

wait
