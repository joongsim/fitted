# AWS Lambda Deployment Guide
## Fitted Wardrobe Assistant - FastAPI on AWS Lambda

This guide will walk you through deploying your FastAPI application to AWS Lambda using either AWS SAM (recommended) or a simple ZIP file approach.

---

## Prerequisites

### Required
1. **AWS Account** - [Sign up here](https://aws.amazon.com/free/)
2. **AWS CLI** - [Installation guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
3. **Python 3.11** - Already installed
4. **OpenRouter API Key** - From your `.env` file

### Optional (for SAM deployment)
5. **AWS SAM CLI** - [Installation guide](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
6. **Docker** - Required for SAM builds

---

## Quick Start: Choose Your Deployment Method

### Option 1: AWS SAM (Recommended for Learning) ⭐

**Pros:**
- Infrastructure as Code
- Easy updates and rollbacks
- Includes API Gateway setup
- Creates S3 bucket automatically
- Best for learning AWS

**Time:** 15-20 minutes first time, 5 minutes after

[Jump to SAM deployment →](#method-1-aws-sam-deployment)

---

### Option 2: Simple ZIP Upload (Fastest Start)

**Pros:**
- No additional tools needed
- Deploy in 10 minutes
- Good for quick testing
- Minimal learning curve

**Cons:**
- Manual API Gateway setup
- No infrastructure versioning

**Time:** 10 minutes

[Jump to ZIP deployment →](#method-2-simple-zip-deployment)

---

## Method 1: AWS SAM Deployment

### Step 1: Configure AWS Credentials

```bash
# Run AWS configure (one-time setup)
aws configure

# Enter:
# AWS Access Key ID: [Your key from IAM]
# AWS Secret Access Key: [Your secret]
# Default region: us-east-1 (or your preferred region)
# Default output format: json
```

**Getting AWS Credentials:**
1. Go to [AWS IAM Console](https://console.aws.amazon.com/iam/)
2. Click "Users" → "Add users"
3. Create user with "AdministratorAccess" (for learning)
4. Create access key → Download credentials

### Step 2: Install SAM CLI

**macOS:**
```bash
brew install aws-sam-cli
```

**Linux:**
```bash
# Download
wget https://github.com/aws/aws-sam-cli/releases/latest/download/aws-sam-cli-linux-x86_64.zip

# Install
unzip aws-sam-cli-linux-x86_64.zip -d sam-installation
sudo ./sam-installation/install
```

**Verify:**
```bash
sam --version
# Should show: SAM CLI, version 1.x.x
```

### Step 3: Set Environment Variables

```bash
# Export your OpenRouter API key
export OPENROUTER_API_KEY='your-openrouter-api-key-here'

# Verify it's set
echo $OPENROUTER_API_KEY
```

### Step 4: Deploy with SAM

```bash
# Make deploy script executable (if not already)
chmod +x deploy.sh

# Run deployment
./deploy.sh
```

**What happens:**
1. ✅ Validates SAM template
2. ✅ Builds Lambda package in Docker container
3. ✅ Creates S3 bucket for deployment artifacts
4. ✅ Uploads code to S3
5. ✅ Creates CloudFormation stack with:
   - Lambda function
   - API Gateway
   - IAM roles
   - S3 bucket for weather data
6. ✅ Outputs your API URL

**Interactive prompts:**
- Stack Name: `fitted-wardrobe` (default)
- AWS Region: `us-east-1` (or your choice)
- Confirm changes: `y`
- Allow SAM CLI IAM role creation: `y`
- Save arguments to config: `y`

### Step 5: Get Your API URL

```bash
# SAM will output it, or run:
aws cloudformation describe-stacks \
  --stack-name fitted-wardrobe \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' \
  --output text
```

### Step 6: Test Your API

```bash
# Get the API URL
API_URL=$(aws cloudformation describe-stacks \
  --stack-name fitted-wardrobe \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' \
  --output text)

# Test root endpoint
curl $API_URL

# Expected response:
# {"message":"Welcome to the Fitted Wardrobe Assistant!"}

# Test outfit suggestion (with mock data)
curl -X POST "${API_URL}suggest-outfit/?location=San%20Francisco"
```

### Updating Your Deployment

```bash
# After making code changes, simply run:
./deploy.sh

# SAM will automatically update only what changed
```

---

## Method 2: Simple ZIP Deployment

### Step 1: Configure AWS CLI

```bash
aws configure
# Enter your credentials as described above
```

### Step 2: Create Lambda Function via Console

1. Go to [AWS Lambda Console](https://console.aws.amazon.com/lambda/)
2. Click "Create function"
3. Choose "Author from scratch"
4. Function name: `fitted-wardrobe-api`
5. Runtime: `Python 3.11`
6. Architecture: `x86_64`
7. Click "Create function"

### Step 3: Build and Upload Code

```bash
# Set your API key
export OPENROUTER_API_KEY='your-key-here'

# Run the simple deployment script
./deploy-simple.sh
```

**What this does:**
1. Creates `lambda-package/` directory
2. Installs dependencies from `requirements-lambda.txt`
3. Copies your `app/` code
4. Creates `lambda-deployment.zip`
5. Uploads to Lambda

### Step 4: Configure Lambda

**In the AWS Console:**

1. **Handler:** Set to `app.main.handler`
   - Configuration → General configuration → Edit → Handler

2. **Environment Variables:**
   - Configuration → Environment variables → Edit
   - Add: `OPENROUTER_API_KEY` = `your-key`

3. **Timeout:** Increase to 30 seconds
   - Configuration → General configuration → Edit → Timeout

4. **Memory:** Set to 512 MB
   - Configuration → General configuration → Edit → Memory

### Step 5: Create API Gateway

1. Go to [API Gateway Console](https://console.aws.amazon.com/apigateway/)
2. Click "Create API" → "HTTP API" → "Build"
3. Add integration:
   - Type: Lambda
   - Function: `fitted-wardrobe-api`
4. API name: `fitted-wardrobe-api`
5. Click "Next" → "Next" → "Create"
6. Note your API endpoint URL

### Step 6: Test

```bash
# Replace with your API Gateway URL
API_URL="https://abc123.execute-api.us-east-1.amazonaws.com"

curl $API_URL
```

---

## Local Testing Before Deployment

### Test FastAPI Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally with uvicorn
uvicorn app.main:app --reload

# Test in browser: http://localhost:8000
# Or with curl:
curl http://localhost:8000
curl -X POST "http://localhost:8000/suggest-outfit/?location=San%20Francisco"
```

### Test Lambda Handler Locally

```bash
# Install sam local
sam local start-api

# Test at: http://localhost:3000
```

---

## Monitoring and Debugging

### View Logs

```bash
# Recent logs
sam logs --stack-name fitted-wardrobe --tail

# Or via AWS CLI
aws logs tail /aws/lambda/fitted-wardrobe-api --follow
```

### Check Lambda Metrics

```bash
# In AWS Console
# CloudWatch → Metrics → Lambda → fitted-wardrobe-api
```

### Common Issues

#### Issue: "Module not found" error
**Solution:** Rebuild deployment package
```bash
./deploy.sh  # or ./deploy-simple.sh
```

#### Issue: "Task timed out after 3.00 seconds"
**Solution:** Increase Lambda timeout
```bash
aws lambda update-function-configuration \
  --function-name fitted-wardrobe-api \
  --timeout 30
```

#### Issue: Cold starts are slow
**Solution:** This is normal for Lambda. Options:
- Use Provisioned Concurrency (costs money)
- Accept 1-2 second cold starts
- Keep function warm with scheduled pings

---

## Cost Breakdown

### AWS Free Tier (First 12 Months)
- **Lambda:** 1M requests/month FREE
- **API Gateway:** 1M requests/month FREE  
- **CloudWatch:** 5GB logs/month FREE
- **S3:** 5GB storage FREE

### Expected Costs (Learning Project)
- **Month 1-12:** $0-1/month
- **After Free Tier:** $1-3/month for low traffic

### Cost Optimization Tips
1. Delete unused Lambda versions
2. Set S3 lifecycle policies
3. Use CloudWatch Logs retention (7 days)
4. Monitor with billing alerts

---

## Next Steps

After successful deployment:

1. ✅ **Week 1 Complete!** - You have FastAPI on Lambda
2. 📝 **Week 2:** Add real Weather API integration
3. 🔄 **Week 3:** Deploy Airflow on EC2
4. 🗄️ **Week 4:** Connect to Databricks

---

## Cleanup (When Done Learning)

### Delete SAM Deployment

```bash
sam delete --stack-name fitted-wardrobe
```

### Manual Cleanup

```bash
# Delete Lambda function
aws lambda delete-function --function-name fitted-wardrobe-api

# Delete API Gateway (note the API ID from console)
aws apigatewayv2 delete-api --api-id <your-api-id>

# Empty and delete S3 bucket
aws s3 rm s3://fitted-weather-data-<account-id> --recursive
aws s3 rb s3://fitted-weather-data-<account-id>
```

---

## Troubleshooting Reference

### Verify Prerequisites

```bash
# Check AWS CLI
aws --version

# Check SAM CLI
sam --version

# Check Python
python3 --version

# Check AWS credentials
aws sts get-caller-identity
```

### Test Template Validation

```bash
sam validate --template template.yaml
```

### Build Without Deploy

```bash
sam build --use-container
```

---

## Additional Resources

- [AWS Lambda Documentation](https://docs.aws.amazon.com/lambda/)
- [AWS SAM Documentation](https://docs.aws.amazon.com/serverless-application-model/)
- [FastAPI on Lambda Guide](https://mangum.io/)
- [AWS Free Tier Details](https://aws.amazon.com/free/)

---

## Questions?

Common questions addressed in [`aws_learning_architecture.md`](aws_learning_architecture.md) and [`build_vs_migrate_strategy.md`](build_vs_migrate_strategy.md).

**Ready to deploy?** Start with [Method 1: SAM Deployment](#method-1-aws-sam-deployment) ⬆️