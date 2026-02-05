# Week 2 Remaining Tasks Guide

This guide details the implementation plan for the remaining Week 2 tasks: Data Validation, Caching Strategy, and Basic Analytics. It includes detailed code templates, line-by-line explanations, and a summary of the architectural decisions.

---

## 1. Data Validation & Error Handling

**Goal:** Ensure weather data received from the API matches our expectations before processing or storing it. This prevents "garbage in, garbage out" and makes debugging significantly easier.

### Chosen Approach: Pydantic Models
We will use **Pydantic** to define strict schemas for our weather data. Pydantic parses JSON and validates types at runtime.

### Implementation Template

**File:** `app/models/weather.py`

```python
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

# 1. Define the nested 'Condition' model first
class WeatherCondition(BaseModel):
    text: str = Field(..., description="Weather condition text, e.g., 'Sunny'")
    icon: str = Field(..., description="URL to weather icon")
    code: int = Field(..., description="Weather condition code")

# 2. Define the 'Current' weather model
class CurrentWeather(BaseModel):
    last_updated_epoch: int
    last_updated: str
    temp_c: float = Field(..., ge=-100, le=60, description="Temperature in Celsius")
    temp_f: float
    is_day: int
    condition: WeatherCondition
    wind_mph: float
    wind_kph: float
    humidity: int = Field(..., ge=0, le=100)
    cloud: int
    feelslike_c: float
    feelslike_f: float
    uv: float

# 3. Define the 'Location' model
class Location(BaseModel):
    name: str
    region: str
    country: str
    lat: float
    lon: float
    tz_id: str
    localtime_epoch: int
    localtime: str

# 4. Define the main Response model
class WeatherResponse(BaseModel):
    location: Location
    current: CurrentWeather
```

### Line-by-Line Explanation

*   **Imports:** `BaseModel` is the core class for creating Pydantic models. `Field` allows us to add metadata and validation constraints (like `ge` for greater than or equal to).
*   **`WeatherCondition` Class:** Represents the nested `condition` object inside the API response. We define strict types (`str`, `int`).
*   **`CurrentWeather` Class:**
    *   `temp_c`: We add validation to ensure the temperature is physically possible (between -100°C and 60°C).
    *   `humidity`: constrained between 0 and 100.
    *   `condition`: This field uses the `WeatherCondition` type defined above, enabling nested validation.
*   **`WeatherResponse` Class:** The top-level model that mirrors the structure of the WeatherAPI JSON response.

### Integration in Service
**File:** `app/services/weather_service.py`

```python
from app.models.weather import WeatherResponse

# ... inside get_weather_data ...
data = response.json()
# Validate data - this will raise a ValidationError if data is invalid
validated_data = WeatherResponse(**data)
return validated_data.dict()
```

---

## 2. Caching Strategy

**Goal:** Reduce API costs and latency by serving frequently requested data from a cache.

### Chosen Approach: Two-Tier Cache (Memory + S3)
1.  **L1 (Memory):** Fast, local to the Lambda instance.
2.  **L2 (S3):** Persistent, shared across Lambda instances.

### Implementation Template

**File:** `app/services/weather_service.py`

```python
import boto3
import json
from datetime import datetime, timedelta, timezone

async def get_weather_with_fallback(location: str):
    # 1. Check Memory Cache (Existing implementation)
    if location in _weather_cache:
        # ... return memory cache ...
        pass

    # 2. Check S3 Fallback (New L2 Cache)
    s3 = boto3.client('s3')
    bucket = "fitted-weather-data-..." # Get from env
    
    # Calculate prefix for today to narrow search
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    # Sanitize location to match storage format
    safe_location = "".join(c for c in location if c.isalnum() or c in ('-', '_')).lower()
    prefix = f"raw/weather/dt={today}/location={safe_location}/"
    
    try:
        # List objects to find the latest file
        response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        
        if 'Contents' in response:
            # Sort by LastModified to get the most recent file
            latest_file = sorted(response['Contents'], key=lambda x: x['LastModified'])[-1]
            
            # Check if it's recent enough (e.g., < 15 mins old)
            age = datetime.now(timezone.utc) - latest_file['LastModified']
            if age < timedelta(minutes=15):
                print(f"Cache Hit (S3): Found data from {age.seconds}s ago")
                
                # Fetch the actual content
                obj = s3.get_object(Bucket=bucket, Key=latest_file['Key'])
                data = json.loads(obj['Body'].read())
                
                # Update L1 Memory Cache
                _weather_cache[location] = (data, time.time())
                return data
                
    except Exception as e:
        print(f"S3 Cache Miss/Error: {e}")

    # 3. Call API (if cache miss)
    # ... existing API call logic ...
```

### Line-by-Line Explanation

*   **Prefix Calculation:** We construct the S3 path `raw/weather/dt=YYYY-MM-DD/location=...` to look for files created today.
*   **`list_objects_v2`:** This is efficient because we are only listing a specific folder (prefix). It returns metadata about the files, not the content.
*   **Sorting:** We sort the files by `LastModified` timestamp to find the newest one.
*   **Age Check:** We calculate `age` to ensure we don't return stale data. If the data is older than 15 minutes, we ignore it and call the API.
*   **`get_object`:** Only if the file is recent do we download the full content.
*   **L1 Update:** We populate the memory cache so subsequent requests on this same Lambda instance are even faster.

---

## 3. Basic Analytics Queries

**Goal:** Gain immediate insights into the collected weather data using S3 Select.

### Chosen Approach: S3 Select
Allows running SQL queries directly on a single JSON object in S3 without downloading the whole file.

### Implementation Template

**File:** `scripts/analyze_weather.py`

```python
import boto3

def query_weather_file(bucket, key):
    s3 = boto3.client('s3')
    
    # SQL Query to extract specific fields
    query = """
        SELECT 
            s.location.name, 
            s.current.temp_c, 
            s.current.condition.text 
        FROM S3Object s 
        WHERE s.current.temp_c > 15
    """
    
    print(f"Querying {key}...")
    
    resp = s3.select_object_content(
        Bucket=bucket,
        Key=key,
        ExpressionType='SQL',
        Expression=query,
        InputSerialization={'JSON': {'Type': 'Document'}},
        OutputSerialization={'JSON': {'RecordDelimiter': '\n'}}
    )
    
    for event in resp['Payload']:
        if 'Records' in event:
            # Records are returned as a byte stream
            print("Match Found:", event['Records']['Payload'].decode('utf-8'))

# Usage Example
# query_weather_file('my-bucket', 'raw/weather/dt=2024-11-29/location=london/12-00-00.json')
```

### Line-by-Line Explanation

*   **`query`:** Standard SQL syntax. `S3Object s` refers to the root of the JSON document. We access nested fields using dot notation (`s.current.temp_c`).
*   **`select_object_content`:** The core API call.
    *   `InputSerialization`: Tells S3 the file is a JSON document.
    *   `OutputSerialization`: Tells S3 to output the results as a stream of JSON objects separated by newlines.
*   **Event Loop:** The response is a stream of events. We listen for the `Records` event which contains the actual data.

---

## 4. Testing Strategy

**Goal:** Verify that our data validation logic correctly identifies valid and invalid data, ensuring robustness.

### Implementation Template

**File:** `tests/test_weather_validation.py`

```python
import pytest
from pydantic import ValidationError
from app.models.weather import WeatherResponse, CurrentWeather, Location, WeatherCondition

# 1. Define valid mock data
VALID_WEATHER_DATA = {
    "location": {
        "name": "London",
        "region": "City of London, Greater London",
        "country": "United Kingdom",
        "lat": 51.52,
        "lon": -0.11,
        "tz_id": "Europe/London",
        "localtime_epoch": 1634666400,
        "localtime": "2021-10-19 19:00"
    },
    "current": {
        "last_updated_epoch": 1634665500,
        "last_updated": "2021-10-19 18:45",
        "temp_c": 16.0,
        "temp_f": 60.8,
        "is_day": 0,
        "condition": {
            "text": "Partly cloudy",
            "icon": "//cdn.weatherapi.com/weather/64x64/night/116.png",
            "code": 1003
        },
        "wind_mph": 10.5,
        "wind_kph": 16.9,
        "wind_degree": 220,
        "wind_dir": "SW",
        "pressure_mb": 1012.0,
        "pressure_in": 29.88,
        "precip_mm": 0.0,
        "precip_in": 0.0,
        "humidity": 77,
        "cloud": 25,
        "feelslike_c": 16.0,
        "feelslike_f": 60.8,
        "vis_km": 10.0,
        "vis_miles": 6.0,
        "uv": 1.0,
        "gust_mph": 14.8,
        "gust_kph": 23.8
    }
}

def test_valid_weather_response():
    """Test that valid data is correctly parsed."""
    weather = WeatherResponse(**VALID_WEATHER_DATA)
    assert weather.location.name == "London"
    assert weather.current.temp_c == 16.0
    assert weather.current.condition.text == "Partly cloudy"

def test_invalid_temperature():
    """Test that physically impossible temperatures raise an error."""
    invalid_data = VALID_WEATHER_DATA.copy()
    # Deep copy needed for nested dicts in real usage, but for this simple test:
    invalid_data["current"] = VALID_WEATHER_DATA["current"].copy()
    invalid_data["current"]["temp_c"] = 1000.0  # Too hot!

    with pytest.raises(ValidationError) as excinfo:
        WeatherResponse(**invalid_data)
    
    # Verify the error message points to the correct field
    assert "temp_c" in str(excinfo.value)
    assert "less than or equal to 60" in str(excinfo.value)

def test_invalid_humidity():
    """Test that humidity outside 0-100 range raises an error."""
    invalid_data = VALID_WEATHER_DATA.copy()
    invalid_data["current"] = VALID_WEATHER_DATA["current"].copy()
    invalid_data["current"]["humidity"] = -5  # Impossible humidity

    with pytest.raises(ValidationError) as excinfo:
        WeatherResponse(**invalid_data)
    
    assert "humidity" in str(excinfo.value)
    assert "greater than or equal to 0" in str(excinfo.value)

def test_missing_required_field():
    """Test that missing required fields raise an error."""
    invalid_data = VALID_WEATHER_DATA.copy()
    del invalid_data["location"] # Remove entire location block

    with pytest.raises(ValidationError) as excinfo:
        WeatherResponse(**invalid_data)
    
    assert "location" in str(excinfo.value)
    assert "Field required" in str(excinfo.value)
```

### Explanation of Tests

1.  **`test_valid_weather_response`**: This is the "happy path". We feed the model a dictionary that perfectly matches the expected schema. We assert that the model is created successfully and that we can access the data using dot notation (e.g., `weather.current.temp_c`).
2.  **`test_invalid_temperature`**: We intentionally set `temp_c` to `1000.0`, which violates the `le=60` constraint in our model. We use `pytest.raises(ValidationError)` to assert that Pydantic catches this and raises an exception. We also check the error message to ensure it's complaining about the right thing.
3.  **`test_invalid_humidity`**: Similar to the temperature test, but checking the `ge=0` constraint for humidity.
4.  **`test_missing_required_field`**: We remove a required field (the entire `location` object) and verify that Pydantic flags it as missing.

---

## Summary

By implementing these three components, we significantly harden the application:

1.  **Pydantic Models** provide a robust defense layer, ensuring our application code never has to deal with malformed data.
2.  **S3 Fallback Caching** creates a resilient system that saves money and works even if the external Weather API has a brief outage.
3.  **S3 Select** gives us a lightweight tool to peek into our data lake immediately, verifying that our ingestion pipeline is working correctly before we build complex ETL jobs.
4.  **Comprehensive Testing** ensures that our validation logic is correct and protects against regressions as the API evolves.