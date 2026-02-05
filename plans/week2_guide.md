# Week 2: Real Weather Data Integration Guide

This guide walks you through the process of replacing mock data with real weather data, explaining the *why* and *how* of each step.

## Part 1: Weather API Integration

**Goal:** Replace the static dictionary in `weather_service.py` with a live call to WeatherAPI.com.

### Key Concepts

1.  **Environment Variables:** Never hardcode API keys. We'll use `os.environ.get()` to retrieve them safely.
2.  **Asynchronous HTTP:** Since we are using FastAPI (which is async), we should use an async HTTP client like `httpx` to avoid blocking the server while waiting for the weather API to respond.
3.  **Graceful Degradation:** If the API fails or the key is missing (like in a dev environment), the system should fall back to mock data or a helpful error message, rather than crashing.

### The Code Pattern

Here is the proposed structure for `app/services/weather_service.py`.

**Step 1: Imports and Setup**

```python
import httpx
import os
from fastapi import HTTPException
from app.services.storage_service import store_raw_weather_data

# Get the key from the environment. 
# In local dev, you might set this in your terminal or a .env file.
# In AWS Lambda, this will be in the function configuration.
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY")
BASE_URL = "https://api.weatherapi.com/v1"
```

**Step 2: The Main Function**

```python
async def get_weather_data(location: str):
    """
    Fetches real weather data.
    """
    # Check if we have a key. If not, use mock data (great for testing without using quota).
    if not WEATHER_API_KEY:
        print("⚠️ No API key found. Using mock data.")
        return _get_mock_data(location)

    # Use AsyncClient for non-blocking calls
    async with httpx.AsyncClient() as client:
        try:
            # Construct the request
            response = await client.get(
                f"{BASE_URL}/current.json",
                params={
                    "key": WEATHER_API_KEY, 
                    "q": location,
                    "aqi": "no" # We don't need air quality index yet
                }
            )
            
            # Raise an exception for 4xx or 5xx status codes
            response.raise_for_status()
            
            data = response.json()
            
            # SIDE EFFECT: Store the raw data in our Data Lake (S3)
            # We use 'await' here, but in a high-scale system, we might want to 
            # make this a background task so the user doesn't wait for S3.
            await store_raw_weather_data(location, data)
            
            return data
            
        except httpx.HTTPStatusError as e:
            # Handle specific API errors (e.g., 401 Unauthorized, 404 Not Found)
            print(f"API Error: {e}")
            raise HTTPException(status_code=e.response.status_code, detail="Weather service error")
        except Exception as e:
            # Handle network errors or other unexpected issues
            print(f"Unexpected error: {e}")
            raise HTTPException(status_code=503, detail="Service unavailable")
```

**Step 3: The Mock Data Fallback**

Keep your existing mock data function but rename it (e.g., `_get_mock_data`) so it's clear it's an internal helper.

### Action Items for You

1.  **Get an API Key:** Sign up for a free account at [WeatherAPI.com](https://www.weatherapi.com/).
2.  **Modify `weather_service.py`:** Implement the pattern above.
3.  **Test Locally:** Run the app and try to hit the endpoint. You can set the env var in your terminal:
    ```bash
    export WEATHER_API_KEY=your_key_here
    uvicorn app.main:app --reload
    ```

## Part 2: Data Lake Storage (S3)

**Goal:** Save every API response to S3. This builds our "Bronze" layer—raw, immutable history.

### Key Concepts

1.  **Partitioning:** We organize files by date (`dt=2023-10-27`) and location. This makes it much faster and cheaper to query later (e.g., "Give me all weather data for London in October").
2.  **Fire-and-Forget:** We want to save the data, but we don't want to fail the user's request if S3 is down. We should handle S3 errors gracefully.

### The Code Pattern

(This matches the `storage_service.py` file we created earlier)

```python
# ... inside store_raw_weather_data ...

# Create a partition structure
date_partition = timestamp.strftime('%Y-%m-%d')
# ...
key = f"raw/weather/dt={date_partition}/location={safe_location}/{time_partition}.json"
```

### Action Items for You

1.  **Review `app/services/storage_service.py`:** Ensure you understand how the S3 key is constructed.
2.  **Check Permissions:** The `template.yaml` already includes `S3Access` policy, so Lambda has permission. For local testing, you'll need AWS credentials configured (e.g., via `aws configure`).

---

**Next Steps:** Once you've implemented the API integration, we'll look at **Caching** to save money and improve speed.

## Part 3: Caching Strategy

**Goal:** Reduce API costs and improve speed by caching weather data.

### Key Concepts

1.  **TTL (Time To Live):** How long we keep data before considering it "stale". For weather, 15-30 minutes is usually fine.
2.  **In-Memory Caching:** Storing data in the RAM of the running application. It's the fastest but disappears if the app restarts (or Lambda shuts down).
3.  **LRU (Least Recently Used):** A strategy to keep the cache from growing forever. When full, it throws away the oldest items.

### The Code Pattern

We can use Python's built-in `functools.lru_cache` for simple synchronous functions, or `async_lru` for async ones. Since we don't want to add more dependencies if we don't have to, we can implement a simple dictionary-based cache with expiration.

**Proposed Implementation in `weather_service.py`:**

```python
import time

# Simple in-memory cache: {location: (data, timestamp)}
_weather_cache = {}
CACHE_TTL = 900  # 15 minutes in seconds

async def get_weather_data(location: str):
    # 1. Check Cache
    if location in _weather_cache:
        data, timestamp = _weather_cache[location]
        if time.time() - timestamp < CACHE_TTL:
            print(f"⚡ Serving {location} from cache")
            return data
        else:
            # Expired
            del _weather_cache[location]

    # 2. Fetch from API (existing code)
    # ... (API call logic) ...
    
    # 3. Save to Cache
    _weather_cache[location] = (data, time.time())
    
    return data
```

### Action Items for You

1.  **Modify `weather_service.py`:** Add the caching logic around your API call.
2.  **Test:** Call the API twice for the same city. The second call should be instant and not print "Fetching weather...".

---

## Part 4: Data Lake Structure (Review)

**Goal:** Understand how our S3 data is organized for future analytics.

### The "Medallion" Architecture

We are building the **Bronze Layer** right now.

*   **Bronze (Raw):** `s3://bucket/raw/weather/dt=2023-10-27/location=london/12-00-00.json`
    *   **What:** Exact copy of API response.
    *   **Why:** If we make a mistake in processing later, we can always go back to the source.
    *   **Format:** JSON (because that's what the API gives us).

*   **Silver (Cleaned):** *Coming in Week 4*
    *   **What:** Deduplicated, converted to tabular format (Parquet), types fixed (strings to numbers).
    *   **Why:** Faster to query.
    *   **Format:** Parquet / Delta Lake.

*   **Gold (Aggregated):** *Coming in Week 4*
    *   **What:** Daily averages, "Best Outfit" scores.
    *   **Why:** Ready for dashboards.

### Why Partitioning Matters?

We used `dt=YYYY-MM-DD` in `storage_service.py`.
When you run a SQL query like:
`SELECT * FROM weather WHERE dt = '2023-10-27'`

The query engine (Athena/Databricks) looks at the folder names and **skips** every folder that isn't `dt=2023-10-27`. This is called "Partition Pruning" and it's the secret to fast big data queries.