#!/bin/bash
# Simple deployment script using ZIP file (no SAM CLI required)

set -e

echo "🚀 Simple Lambda Deployment (ZIP method)..."

# Check if AWS CLI is installed
if ! command -v aws &> /dev/null; then
    echo "❌ AWS CLI not found. Install from: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    exit 1
fi

# Check environment variables
if [ -z "$OPENROUTER_API_KEY" ]; then
    echo "❌ OPENROUTER_API_KEY not set. Export it: export OPENROUTER_API_KEY='your-key'"
    exit 1
fi

FUNCTION_NAME="fitted-wardrobe-api"
REGION=${AWS_REGION:-us-east-1}
ROLE_NAME="fitted-lambda-role"

echo "📦 Creating deployment package..."

# Create temporary directory
rm -rf lambda-package
mkdir -p lambda-package

# Install dependencies
echo "   Installing dependencies..."
pip install -r requirements-lambda.txt -t lambda-package/ --quiet

# Copy application code
echo "   Copying application code..."
cp -r app lambda-package/

# Create ZIP file
echo "   Creating ZIP file..."
cd lambda-package
zip -r ../lambda-deployment.zip . -q
cd ..

echo "✅ Deployment package created: lambda-deployment.zip"

# Check if Lambda function exists
if aws lambda get-function --function-name $FUNCTION_NAME --region $REGION &> /dev/null; then
    echo "📝 Updating existing Lambda function..."
    aws lambda update-function-code \
        --function-name $FUNCTION_NAME \
        --zip-file fileb://lambda-deployment.zip \
        --region $REGION
    
    echo "⚙️  Updating environment variables..."
    aws lambda update-function-configuration \
        --function-name $FUNCTION_NAME \
        --environment "Variables={OPENROUTER_API_KEY=$OPENROUTER_API_KEY}" \
        --region $REGION
else
    echo "❌ Lambda function '$FUNCTION_NAME' not found."
    echo "   Creating Lambda function via AWS Console or use deploy.sh with SAM"
    echo ""
    echo "   Manual steps:"
    echo "   1. Go to AWS Lambda Console"
    echo "   2. Create new function: $FUNCTION_NAME"
    echo "   3. Runtime: Python 3.11"
    echo "   4. Upload lambda-deployment.zip"
    echo "   5. Set handler: app.main.handler"
    echo "   6. Add environment variable: OPENROUTER_API_KEY"
    echo "   7. Create API Gateway HTTP API trigger"
    exit 1
fi

echo ""
echo "✨ Deployment successful!"
echo ""
echo "🧪 To test:"
echo "   aws lambda invoke --function-name $FUNCTION_NAME --region $REGION response.json"
echo ""

# Cleanup
rm -rf lambda-package