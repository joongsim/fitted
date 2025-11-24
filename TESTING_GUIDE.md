# Testing Guide - Fitted Wardrobe API

## Quick Test Commands

### 1. Get Your API URL

```bash
aws cloudformation describe-stacks \
  --stack-name fitted-wardrobe \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' \
  --output text
```

Save it as an environment variable:
```bash
export API_URL=$(aws cloudformation describe-stacks \
  --stack-name fitted-wardrobe \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' \
  --output text)

echo $API_URL
```

### 2. Test Root Endpoint

```bash
curl $API_URL
```

**Expected Response:**
```json
{"message":"Welcome to the Fitted Wardrobe Assistant!"}
```

### 3. Test Outfit Suggestion (Mock Data)

```bash
curl -X POST "${API_URL}suggest-outfit/?location=San%20Francisco"
```

**Expected Response:**
```json
{
  "location": "San Francisco",
  "weather": {
    "location": {
      "name": "San Francisco",
      "region": "California",
      "country": "USA"
    },
    "current": {
      "temp_c": 14.0,
      "temp_f": 57.2,
      "condition": {
        "text": "Partly cloudy"
      },
      "wind_mph": 5.0,
      "wind_dir": "W",
      "precip_mm": 0.0,
      "humidity": 82,
      "cloud": 50
    }
  },
  "outfit_suggestion": "For partly cloudy weather at 14°C (57°F) in San Francisco..."
}
```

### 4. Test with Different Locations

```bash
# Los Angeles
curl -X POST "${API_URL}suggest-outfit/?location=Los%20Angeles"

# New York
curl -X POST "${API_URL}suggest-outfit/?location=New%20York"

# Seattle
curl -X POST "${API_URL}suggest-outfit/?location=Seattle"
```

---

## View Logs

### Real-time Logs

```bash
# Follow logs as they come in
sam logs --stack-name fitted-wardrobe --tail

# Or with AWS CLI
aws logs tail /aws/lambda/fitted-wardrobe-api --follow
```

### Recent Logs

```bash
# Last 10 minutes
sam logs --stack-name fitted-wardrobe

# Last hour
aws logs tail /aws/lambda/fitted-wardrobe-api --since 1h
```

---

## Check Lambda Function

### Function Info

```bash
aws lambda get-function \
  --function-name fitted-wardrobe-api \
  --query 'Configuration.[FunctionName,Runtime,MemorySize,Timeout,LastModified]' \
  --output table
```

### Test Lambda Directly

```bash
# Create test event
echo '{
  "httpMethod": "GET",
  "path": "/",
  "headers": {}
}' > test-event.json

# Invoke function
aws lambda invoke \
  --function-name fitted-wardrobe-api \
  --payload file://test-event.json \
  response.json

# View response
cat response.json
```

---

## Monitor Performance

### CloudWatch Metrics

```bash
# Get invocation count (last hour)
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Invocations \
  --dimensions Name=FunctionName,Value=fitted-wardrobe-api \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 3600 \
  --statistics Sum
```

### Check for Errors

```bash
aws logs filter-log-events \
  --log-group-name /aws/lambda/fitted-wardrobe-api \
  --filter-pattern ERROR \
  --max-items 10
```

---

## Local Testing (Before Deployment)

### Run FastAPI Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Run with uvicorn
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Test locally:**
```bash
# Root endpoint
curl http://localhost:8000

# Outfit suggestion
curl -X POST "http://localhost:8000/suggest-outfit/?location=San%20Francisco"
```

---

## Performance Benchmarks

### Test Cold Start Time

```bash
# Stop function (by not calling it for 15+ minutes)
# Then test:
time curl $API_URL
```

**Expected:**
- Cold start: 1-2 seconds
- Warm invocation: 100-300ms

### Load Testing (Simple)

```bash
# Install Apache Bench (if not installed)
# On Linux: sudo apt-get install apache2-utils

# Run 100 requests, 10 concurrent
ab -n 100 -c 10 $API_URL
```

---

## Troubleshooting

### Issue: "Internal Server Error"

**Check logs:**
```bash
sam logs --stack-name fitted-wardrobe --tail
```

**Common causes:**
- Missing environment variables
- Python dependencies not installed
- Handler path incorrect

### Issue: "Timeout"

**Increase timeout:**
```bash
aws lambda update-function-configuration \
  --function-name fitted-wardrobe-api \
  --timeout 60
```

### Issue: "Out of Memory"

**Increase memory:**
```bash
aws lambda update-function-configuration \
  --function-name fitted-wardrobe-api \
  --memory-size 1024
```

---

## Update Deployed Code

### After Making Changes

```bash
# Simply run deploy again
./deploy-no-docker.sh

# SAM will automatically update only what changed
```

### Quick Update (Code Only)

```bash
# Build new package
sam build

# Deploy without prompts
sam deploy
```

---

## Clean Up Resources

### Delete Everything

```bash
# This deletes:
# - Lambda function
# - API Gateway
# - S3 buckets
# - CloudWatch logs
# - IAM roles

sam delete --stack-name fitted-wardrobe
```

### Keep Logs, Delete Function

```bash
# Delete just the CloudFormation stack
aws cloudformation delete-stack --stack-name fitted-wardrobe

# Logs will be retained based on retention policy
```

---

## Cost Monitoring

### Check S3 Storage

```bash
aws s3 ls s3://fitted-weather-data-903558039846 --recursive --human-readable --summarize
```

### Estimate Monthly Lambda Cost

```bash
# Get invocation count for last 30 days
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Invocations \
  --dimensions Name=FunctionName,Value=fitted-wardrobe-api \
  --start-time $(date -u -d '30 days ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 2592000 \
  --statistics Sum
```

**Calculation:**
- First 1M requests/month: FREE
- After: $0.20 per 1M requests
- Duration: $0.0000166667 per GB-second

---

## Next Steps After Deployment

1. ✅ Verify deployment successful
2. ✅ Test all endpoints
3. 📝 Week 2: Add real Weather API integration
4. 🗄️ Week 3: Deploy Airflow on EC2
5. 🔄 Week 4: Connect to Databricks

See [`aws_learning_architecture.md`](aws_learning_architecture.md) for full roadmap.