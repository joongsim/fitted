# How to View Your Deployment in AWS Console

## Quick Access Links

**Your AWS Region:** us-west-1 (N. California)

### Direct Console Links

1. **Lambda Function**
   - Go to: https://us-west-1.console.aws.amazon.com/lambda/home?region=us-west-1#/functions
   - Look for: `fitted-wardrobe-api`

2. **API Gateway**
   - Go to: https://us-west-1.console.aws.amazon.com/apigateway/main/apis?region=us-west-1
   - Look for: `ServerlessHttpApi` (ID: e2d6c3y53g)

3. **CloudFormation Stack**
   - Go to: https://us-west-1.console.aws.amazon.com/cloudformation/home?region=us-west-1#/stacks
   - Look for: `fitted-wardrobe`

4. **S3 Bucket**
   - Go to: https://s3.console.aws.amazon.com/s3/buckets?region=us-west-1
   - Look for: `fitted-weather-data-903558039846`

5. **CloudWatch Logs**
   - Go to: https://us-west-1.console.aws.amazon.com/cloudwatch/home?region=us-west-1#logsV2:log-groups
   - Look for: `/aws/lambda/fitted-wardrobe-api`

---

## Step-by-Step Walkthrough

### 1. CloudFormation Stack (Best Starting Point)

**What:** This shows your entire deployment as infrastructure-as-code

**How to Access:**
1. Go to AWS Console: https://console.aws.amazon.com
2. Sign in with your credentials
3. **Make sure you're in us-west-1 region** (top-right, should say "N. California")
4. Search for "CloudFormation" in the top search bar
5. Click on "CloudFormation" service

**What You'll See:**
```
Stack Name: fitted-wardrobe
Status: CREATE_COMPLETE (green checkmark)
```

**Click on the stack name to see:**
- **Stack info:** Overview of your deployment
- **Events:** Timeline of what was created (scrollable)
- **Resources:** All 8 AWS resources created:
  - FittedApi (Lambda function)
  - ServerlessHttpApi (API Gateway)
  - WeatherDataBucket (S3 bucket)
  - FittedApiRole (IAM role)
  - 3x Lambda Permissions
  - 1x API Gateway Stage
- **Outputs:** Your API URL and other important values
- **Template:** The actual SAM/CloudFormation template used
- **Parameters:** Shows your OpenRouter API key (hidden)

---

### 2. Lambda Function (Your Code)

**What:** This is where your actual FastAPI application runs

**How to Access:**
1. From AWS Console, search for "Lambda"
2. Click "Lambda" service
3. Click on "Functions" in left sidebar
4. Click on `fitted-wardrobe-api`

**What You'll See:**

#### Function Overview Tab
- **Function name:** fitted-wardrobe-api
- **Status:** Active (green)
- **Runtime:** Python 3.13
- **Memory:** 512 MB
- **Last modified:** [Your deployment time]
- **Function URL:** (if enabled, or use API Gateway)

#### Code Tab
- Your Lambda function code
- Can edit directly in browser (not recommended)
- Shows file structure:
  ```
  app/
    main.py
    services/
      weather_service.py
      llm_service.py
  (plus all dependencies)
  ```

#### Configuration Tab
**General configuration:**
- Memory: 512 MB
- Timeout: 30 seconds
- Handler: app.main.handler

**Environment variables:**
- OPENROUTER_API_KEY: [hidden, but set]

**Permissions:**
- Execution role: Shows IAM role with S3 access

**Triggers:**
- API Gateway HTTP API (e2d6c3y53g)
- Shows your 3 routes: GET /, POST /suggest-outfit/, ANY /{proxy+}

#### Monitor Tab
- **Metrics:** Invocations, duration, errors, throttles
- **Recent invocations:** Last requests (click to see details)
- **Logs:** Direct link to CloudWatch logs

#### Test Tab
- Can test your function with sample events
- Try this test event:
```json
{
  "httpMethod": "GET",
  "path": "/",
  "headers": {}
}
```

---

### 3. API Gateway (Your Public Endpoint)

**What:** This is the public URL that routes requests to your Lambda

**How to Access:**
1. Search for "API Gateway" in AWS Console
2. Click "API Gateway" service
3. Look for `ServerlessHttpApi`
4. Click on it

**What You'll See:**

#### API Overview
- **Name:** ServerlessHttpApi
- **API ID:** e2d6c3y53g
- **Protocol:** HTTP
- **Invoke URL:** https://e2d6c3y53g.execute-api.us-west-1.amazonaws.com/

#### Routes
You'll see your 3 configured routes:
- `GET /` → FittedApi Lambda
- `POST /suggest-outfit/` → FittedApi Lambda
- `ANY /{proxy+}` → FittedApi Lambda (catch-all)

#### Stages
- **$default** stage (active)
- Auto-deploy enabled

#### Integrations
- Shows Lambda function integrations
- Click to see details of how API Gateway connects to Lambda

#### Monitoring
- Request count
- Latency metrics
- 4xx/5xx errors (should be 0!)

**Test Your API Right Here:**
1. Click on a route (e.g., GET /)
2. Click "Test" tab
3. Click "Test" button
4. See response!

---

### 4. S3 Bucket (Data Storage)

**What:** Storage for weather data and other files (currently empty)

**How to Access:**
1. Search for "S3" in AWS Console
2. Click "S3" service
3. Find bucket: `fitted-weather-data-903558039846`
4. Click on it

**What You'll See:**

#### Objects Tab
- Currently empty
- In Week 2, you'll store weather API responses here

#### Properties Tab
- **Versioning:** Enabled (can recover old versions)
- **Encryption:** Enabled by default
- **Public access:** Blocked (good for security)

#### Permissions Tab
- Bucket policy: Shows IAM permissions
- Your Lambda function has read/write access

#### Metrics Tab
- Storage usage (currently ~0 MB)
- Request metrics

---

### 5. CloudWatch Logs (Debugging)

**What:** All Lambda console.log() and print() statements go here

**How to Access:**
1. Search for "CloudWatch" in AWS Console
2. Click "CloudWatch" service
3. Click "Logs" → "Log groups" in left sidebar
4. Find: `/aws/lambda/fitted-wardrobe-api`
5. Click on it

**What You'll See:**

#### Log Streams
- Each Lambda "warm instance" gets its own stream
- Click on most recent stream (top one)

#### Recent Logs
You'll see logs like:
```
INIT_START Runtime Version: python:3.13...
START RequestId: bf5cbaff-3a6e-...
Fetching weather for San Francisco...
Error calling LLM service: Error code: 401...
END RequestId: bf5cbaff-3a6e-...
REPORT RequestId: ... Duration: 89ms Memory: 103MB
```

**Useful Features:**
- **Filter events:** Search for specific errors
- **Export:** Download logs as CSV
- **Live tail:** Real-time log streaming (like `sam logs --tail`)
- **Insights:** Query logs with SQL-like syntax

---

### 6. IAM Role (Permissions)

**What:** Defines what your Lambda function is allowed to do

**How to Access:**
1. Search for "IAM" in AWS Console
2. Click "IAM" service
3. Click "Roles" in left sidebar
4. Search for: `fitted-wardrobe-FittedApiRole`
5. Click on it

**What You'll See:**

#### Permissions Tab
Shows two policies:
1. **AWSLambdaBasicExecutionRole** (AWS managed)
   - Allows writing to CloudWatch Logs
   
2. **S3Access** (Custom inline policy)
   - Allows reading/writing to your S3 bucket
   - Allows listing bucket contents

#### Trust Relationships
- Shows Lambda service can assume this role

---

## Monitoring Your Deployment

### Real-Time Metrics Dashboard

**CloudWatch Dashboard (Optional):**
1. Go to CloudWatch
2. Click "Dashboards" → "Create dashboard"
3. Add widgets for:
   - Lambda invocations
   - API Gateway requests
   - Error rates
   - Duration

### Cost Monitoring

**Cost Explorer:**
1. Search for "Cost Explorer"
2. View current month spending
3. Set up billing alerts

**Free Tier Usage:**
1. Go to "Billing Dashboard"
2. Click "Free tier" in left menu
3. See Lambda usage (should show 4-5 invocations)

---

## Quick Checks

### Verify Everything is Working

**✅ Lambda Function:**
```
Status: Active
Last modified: [Recent]
No errors in Monitor tab
```

**✅ API Gateway:**
```
Routes: 3 configured
Invoke URL works (test with curl)
No 4xx/5xx errors
```

**✅ CloudWatch Logs:**
```
Recent log streams exist
Shows successful requests
Can see your print() statements
```

**✅ S3 Bucket:**
```
Created successfully
Versioning enabled
Public access blocked
```

**✅ CloudFormation:**
```
Stack status: CREATE_COMPLETE
All resources created
No drift detected
```

---

## Common Console Tasks

### Update Lambda Code
1. Go to Lambda function
2. Click "Code" tab
3. Make changes (NOT RECOMMENDED - use SAM instead)
4. Click "Deploy"

**Better way:**
```bash
# Edit code locally
sam build
sam deploy
```

### View Recent API Calls
1. Go to Lambda function
2. Click "Monitor" tab
3. Click "View CloudWatch logs"
4. See recent requests

### Check API Gateway Metrics
1. Go to API Gateway
2. Select your API
3. Click "Monitor" tab
4. See request counts, latency

### Download Logs
1. Go to CloudWatch Logs
2. Select log stream
3. Click "Actions" → "Export to S3"

### Test Lambda Directly
1. Go to Lambda function
2. Click "Test" tab
3. Create test event (GET / request)
4. Click "Test"
5. See response

---

## Troubleshooting

### Can't Find Resources?

**Check Region:**
- Top-right corner should say "N. California" or "us-west-1"
- If it says something else, switch to us-west-1

**Resources Not Visible:**
- Wait ~30 seconds after deployment
- Try refreshing the page
- Search by exact name: `fitted-wardrobe-api`

### Want to See Everything at Once?

**Use CloudFormation Stack View:**
1. Go to CloudFormation
2. Click on `fitted-wardrobe` stack
3. Click "Resources" tab
4. This shows ALL resources with direct links

---

## Pro Tips

1. **Bookmark Your Lambda Function**
   - Direct link: https://us-west-1.console.aws.amazon.com/lambda/home?region=us-west-1#/functions/fitted-wardrobe-api

2. **Use CloudWatch Insights for Advanced Queries**
   ```
   fields @timestamp, @message
   | filter @message like /Error/
   | sort @timestamp desc
   ```

3. **Enable X-Ray for Detailed Tracing**
   - See exactly how long each part of your request takes
   - Lambda → Configuration → Monitoring tools → Enable X-Ray

4. **Set Up CloudWatch Alarms**
   - Get email when errors occur
   - CloudWatch → Alarms → Create alarm

5. **Tag Everything**
   - Your resources are tagged with `Project: fitted-wardrobe`
   - Use Cost Allocation Tags to track spending

---

## Summary: Your Console Checklist

After deployment, check these in order:

1. ✅ **CloudFormation:** Stack status is CREATE_COMPLETE
2. ✅ **Lambda:** Function exists and shows no errors
3. ✅ **API Gateway:** Has your 3 routes configured
4. ✅ **CloudWatch Logs:** Shows recent invocations
5. ✅ **S3 Bucket:** Created and accessible
6. ✅ **IAM Role:** Has correct permissions

**Your main dashboard:** CloudFormation stack → Resources tab

This gives you one-click access to everything!