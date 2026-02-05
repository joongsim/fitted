# Best Practices for Testing AWS Lambda Functions

Testing serverless applications requires a multi-layered approach because you cannot easily replicate the entire AWS cloud environment on your laptop.

## 1. The Testing Pyramid for Lambda

### Layer 1: Unit Tests (Fast & Cheap)
*   **What:** Test individual functions and logic in isolation.
*   **How:** Use `pytest` and `unittest.mock`.
*   **Key Strategy:** **Separate Logic from Infrastructure.**
    *   *Bad:* Writing code that calls `boto3` directly inside your business logic.
    *   *Good:* Passing a storage service or repository interface to your business logic.
*   **Mocking:** Mock all external services (S3, DynamoDB, APIs).
    *   Use `moto` to mock AWS services realistically in memory.

### Layer 2: Integration Tests (Local)
*   **What:** Test how your code interacts with "real-ish" services.
*   **How:**
    *   **Docker:** Use `localstack` to spin up a fake AWS environment locally.
    *   **SAM Local:** Use `sam local invoke` to run your Lambda function in a Docker container that mimics the AWS Lambda environment.
    *   **Direct Invocation:** Run your FastAPI app with `uvicorn` (like we did with `run_local.sh`).

### Layer 3: End-to-End (E2E) Tests (Cloud)
*   **What:** Test the deployed application in a real AWS environment.
*   **How:** Deploy to a `dev` or `staging` stack and run tests against the real API Gateway URL.
*   **Why:** This catches IAM permission errors, timeout issues, and service limit quotas that local tests miss.

## 2. Practical Examples

### A. Using `moto` for S3 Tests
Instead of mocking `boto3` calls with `MagicMock` (which can be brittle), use `moto` to create a fake S3 bucket in memory.

```python
import boto3
import pytest
from moto import mock_aws
from app.services.storage_service import store_raw_weather_data

@mock_aws
def test_s3_storage():
    # 1. Setup fake S3
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="test-bucket")
    
    # 2. Run your code
    store_raw_weather_data("London", {"temp": 15})
    
    # 3. Verify object exists in fake S3
    obj = s3.get_object(Bucket="test-bucket", Key="...")
    assert obj['Body'].read() == b'{"temp": 15}'
```

### B. Testing the Lambda Handler Locally
You can simulate a Lambda event (like an API Gateway request) locally.

```bash
# Create a test event file (event.json)
{
  "resource": "/",
  "path": "/suggest-outfit/",
  "httpMethod": "POST",
  "queryStringParameters": {
    "location": "London"
  }
}

# Invoke with SAM
sam local invoke FittedApi -e event.json
```

## 3. Common Pitfalls
1.  **Hardcoding Credentials:** Never use real AWS keys in tests. `moto` handles this automatically.
2.  **Ignoring Timeouts:** Lambda has a hard timeout (e.g., 30s). Your local tests might run forever, but the cloud function will die. Use `pytest-timeout` to enforce limits.
3.  **Global State:** Lambda containers are reused. If you modify a global variable in one request, it persists to the next. Ensure your tests check for state leakage.

## 4. Recommended Tooling
*   **Test Runner:** `pytest`
*   **AWS Mocking:** `moto`
*   **Local Lambda:** `aws-sam-cli`
*   **Local AWS Stack:** `localstack` (optional, for complex setups)