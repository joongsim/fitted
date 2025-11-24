#!/bin/bash
# Deployment script for Fitted Wardrobe API to AWS Lambda

set -e  # Exit on error

echo "🚀 Deploying Fitted Wardrobe API to AWS Lambda..."

# Check if AWS CLI is installed
if ! command -v aws &> /dev/null; then
    echo "❌ AWS CLI not found. Please install it first:"
    echo "   https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    exit 1
fi

# Check if SAM CLI is installed
if ! command -v sam &> /dev/null; then
    echo "❌ SAM CLI not found. Please install it first:"
    echo "   https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html"
    exit 1
fi

# Check if OpenRouter API key is set
if [ -z "$OPENROUTER_API_KEY" ]; then
    echo "❌ OPENROUTER_API_KEY environment variable not set"
    echo "   Export it first: export OPENROUTER_API_KEY='your-key-here'"
    exit 1
fi

echo "✅ Prerequisites check passed"

# Create .aws-sam directory if it doesn't exist
mkdir -p .aws-sam

echo "📦 Building SAM application..."
sam build --use-container

echo "🚢 Deploying to AWS..."
sam deploy \
    --guided \
    --parameter-overrides OpenRouterApiKey="$OPENROUTER_API_KEY" \
    --tags Project=fitted-wardrobe Environment=production

echo ""
echo "✨ Deployment complete!"
echo ""
echo "📝 To get your API URL, run:"
echo "   aws cloudformation describe-stacks --stack-name fitted-wardrobe --query 'Stacks[0].Outputs[?OutputKey==\`ApiUrl\`].OutputValue' --output text"
echo ""
echo "🧪 To test your API:"
echo "   API_URL=\$(aws cloudformation describe-stacks --stack-name fitted-wardrobe --query 'Stacks[0].Outputs[?OutputKey==\`ApiUrl\`].OutputValue' --output text)"
echo "   curl \$API_URL"
echo ""