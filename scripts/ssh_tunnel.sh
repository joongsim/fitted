#!/bin/bash

# --- Configuration ---
# These are fetched from your CloudFormation outputs
EC2_IP="13.52.19.182"
RDS_ENDPOINT="fitted-infra-fitteddb-iqqx1aiplhxv.cle6qaquk5at.us-west-1.rds.amazonaws.com"
SSH_KEY="~/.ssh/fitted-key-v2.pem"
LOCAL_PORT=5432

echo "🌉 Establishing SSH Tunnel to RDS..."
echo "📍 EC2 Jump Host: $EC2_IP"
echo "🗄️ RDS Endpoint: $RDS_ENDPOINT"
echo "🔌 Local Port: $LOCAL_PORT"

# Check if the SSH key exists
if [ ! -f $(eval echo $SSH_KEY) ]; then
    echo "❌ SSH Key not found at $SSH_KEY"
    exit 1
fi

# Check if port 5432 is already in use
if lsof -Pi :$LOCAL_PORT -sTCP:LISTEN -t >/dev/null ; then
    echo "⚠️ Port $LOCAL_PORT is already in use. Is another tunnel or local Postgres running?"
    exit 1
fi

echo "🚀 Tunneling... (Press Ctrl+C to stop)"
echo "💡 Once running, set DATABASE_URL=postgresql://fitted_admin:<password>@localhost:5432/fitted"

# -N: Do not execute a remote command (just forward ports)
# -L: Local port forwarding
ssh -i $SSH_KEY -N -L $LOCAL_PORT:$RDS_ENDPOINT:5432 ec2-user@$EC2_IP
