#!/bin/bash
# SAM deployment without Docker (for WSL/environments without container runtime)

set -e

echo "🚀 Deploying Fitted Wardrobe API to AWS Lambda (No Docker)..."

# Check AWS CLI
if ! command -v aws &> /dev/null; then
    echo "❌ AWS CLI not found"
    exit 1
fi

# Check SAM CLI
if ! command -v sam &> /dev/null; then
    echo "❌ SAM CLI not found"
    exit 1
fi

# Check API key
if [ -z "$OPENROUTER_API_KEY" ]; then
    echo "❌ OPENROUTER_API_KEY not set"
    echo "   Run: export OPENROUTER_API_KEY='your-key-here'"
    exit 1
fi

echo "✅ Prerequisites check passed"

# Build WITHOUT Docker (builds locally using your Python environment)
echo "📦 Building SAM application (no Docker)..."
sam build

echo "🚢 Deploying to AWS..."
sam deploy \
    --guided \
    --parameter-overrides OpenRouterApiKey="$OPENROUTER_API_KEY" \
    --tags Project=fitted-wardrobe Environment=production

echo ""
echo "✨ Deployment complete!"
echo ""
echo "📝 Get your API URL:"
echo "   aws cloudformation describe-stacks --stack-name fitted-wardrobe --query 'Stacks[0].Outputs[?OutputKey==\`ApiUrl\`].OutputValue' --output text"
echo ""