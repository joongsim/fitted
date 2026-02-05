# Week 3 Implementation Guide: Enhanced Features & Validation

## Overview

Week 3 focuses on adding forecast capabilities, new API endpoints, and improving LLM suggestions with weather trends. This builds on the completed Week 2 foundation.

**Timeline:** 5 days
**Goal:** Production-ready API with forecast data and enhanced outfit suggestions

---

## Prerequisites

✅ Week 2 Complete:
- Real weather API integration working
- S3 data storage functional
- Athena analytics operational
- Pydantic models in place
- Caching strategy implemented

---

## Task Breakdown

### Day 1: Forecast Data Integration

**Objective:** Add weather forecast capabilities to the weather service

#### Step 1.1: Update Weather Models

**File:** `app/models/weather.py`

Add new Pydantic models for forecast data:

```python
from pydantic import BaseModel, Field
from typing import List, Optional

# Existing models...

class ForecastDay(BaseModel):
    """Single day forecast"""
    date: str
    date_epoch: int
    day: dict  # Contains maxtemp_c, mintemp_c, avgtemp_c, condition, etc.
    astro: dict  # Sunrise, sunset, moon phase
    hour: Optional[List[dict]] = None  # Hourly forecast (if needed)

class Forecast(BaseModel):
    """Multi-day forecast"""
    forecastday: List[ForecastDay]

class WeatherWithForecast(BaseModel):
    """Weather response with forecast"""
    location: Location
    current: CurrentWeather
    forecast: Optional[Forecast] = None
```

**Verification:**
```bash
# Test the models
python -c "from app.models.weather import WeatherWithForecast; print('✅ Models imported')"
```

#### Step 1.2: Add Forecast Fetching Function

**File:** `app/services/weather_service.py`

Add new function after `get_weather_data`:

```python
async def get_weather_with_forecast(location: str, days: int = 3) -> dict:
    """
    Fetch current weather plus forecast data.
    
    Args:
        location: Location name or coordinates
        days: Number of forecast days (1-10, default 3)
        
    Returns:
        Dictionary with current weather and forecast
    """
    # Validate days parameter
    if not 1 <= days <= 10:
        days = 3
    
    # Check cache first (cache key includes days)
    cache_key = f"{location}:{days}"
    if cache_key in _weather_cache:
        data, timestamp = _weather_cache[cache_key]
        if time.time() - timestamp < CACHE_TTL:
            print(f"Returning cached forecast data for {location}")
            return data
        else:
            del _weather_cache[cache_key]
    
    # Check if we have API key
    try:
        weather_api_key = config.weather_api_key
    except Exception as e:
        print(f"⚠️ No API key found: {e}. Using mock data.")
        return await _get_mock_forecast_data(location, days)
    
    # Fetch from API
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{BASE_URL}/forecast.json",
                params={
                    "key": weather_api_key,
                    "q": location,
                    "days": days,
                    "aqi": "no"
                },
                timeout=10.0
            )
            
            response.raise_for_status()
            data = response.json()
            
            # Validate with Pydantic
            from app.models.weather import WeatherWithForecast
            validated_data = WeatherWithForecast(**data)
            
            # Store in S3 (with forecast flag in key)
            await store_raw_weather_data(
                location, 
                validated_data.model_dump(),
                is_forecast=True
            )
            
            # Cache the result
            _weather_cache[cache_key] = (validated_data.model_dump(), time.time())
            
            return validated_data.model_dump()
            
        except httpx.HTTPStatusError as e:
            print(f"API Error: {e}")
            raise HTTPException(
                status_code=e.response.status_code,
                detail="Weather forecast service error"
            )
        except Exception as e:
            print(f"Unexpected error: {e}")
            raise HTTPException(status_code=503, detail="Service unavailable")


async def _get_mock_forecast_data(location: str, days: int = 3) -> dict:
    """Mock forecast data for testing without API key"""
    current_data = await _get_mock_data(location)
    
    # Generate simple forecast (temperature variation)
    forecast_days = []
    base_temp = current_data['current']['temp_c']
    
    for i in range(days):
        forecast_days.append({
            "date": f"2024-12-{9+i:02d}",
            "date_epoch": 1733788800 + (i * 86400),
            "day": {
                "maxtemp_c": base_temp + (i * 2),
                "mintemp_c": base_temp - 3,
                "avgtemp_c": base_temp + (i * 0.5),
                "condition": {
                    "text": "Partly cloudy",
                    "icon": "//cdn.weatherapi.com/weather/64x64/day/116.png"
                }
            },
            "astro": {
                "sunrise": "07:00 AM",
                "sunset": "05:00 PM"
            }
        })
    
    return {
        **current_data,
        "forecast": {
            "forecastday": forecast_days
        }
    }
```

#### Step 1.3: Update Storage Service for Forecast

**File:** `app/services/storage_service.py`

Modify `store_raw_weather_data` to handle forecast flag:

```python
async def store_raw_weather_data(location: str, data: dict, is_forecast: bool = False):
    """
    Store raw weather API response in S3 (Bronze Layer).
    
    Args:
        location: The location name (e.g., "London")
        data: The raw JSON response from the weather API
        is_forecast: If True, includes forecast data
    """
    if IS_LOCAL:
        print(f"ℹ️  Running locally. Skipping S3 upload for {location}.")
        return

    if not s3_client or not WEATHER_BUCKET:
        print("Warning: S3 client or bucket not configured. Skipping S3 storage.")
        return

    try:
        timestamp = datetime.now(timezone.utc)
        date_partition = timestamp.strftime('%Y-%m-%d')
        time_partition = timestamp.strftime('%H-%M-%S')
        
        # Sanitize location for S3 key
        safe_location = "".join(c for c in location if c.isalnum() or c in ('-', '_')).lower()
        
        # Different path for forecast vs current
        data_type = "forecast" if is_forecast else "current"
        key = f"raw/weather/{data_type}/dt={date_partition}/location={safe_location}/{time_partition}.json"
        
        # Run the blocking S3 call in a separate thread
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            partial(
                s3_client.put_object,
                Bucket=WEATHER_BUCKET,
                Key=key,
                Body=json.dumps(data),
                ContentType='application/json',
                Metadata={
                    'data-type': data_type,
                    'location': safe_location
                }
            )
        )
        print(f"Successfully stored {data_type} weather data to s3://{WEATHER_BUCKET}/{key}")
        
    except Exception as e:
        print(f"Error storing data in S3: {e}")
```

**Verification:**
```bash
# Test forecast fetching (requires API key)
curl "https://your-api-url/suggest-outfit?location=London"
# Check S3 for new forecast folder
aws s3 ls s3://your-bucket/raw/weather/forecast/ --recursive
```

---

### Day 2: New API Endpoints

**Objective:** Add dedicated weather and forecast endpoints

#### Step 2.1: Implement Weather Endpoints

**File:** `app/main.py`

Add these new endpoints after the existing ones:

```python
@app.get("/weather/{location}")
async def get_current_weather(location: str):
    """
    Get current weather for a location.
    
    Returns:
        Current weather data including temperature, condition, humidity, etc.
    """
    try:
        weather_data = await weather_service.get_weather_data(location)
        return {
            "location": weather_data["location"]["name"],
            "country": weather_data["location"]["country"],
            "current": {
                "temperature_c": weather_data["current"]["temp_c"],
                "temperature_f": weather_data["current"]["temp_f"],
                "condition": weather_data["current"]["condition"]["text"],
                "humidity": weather_data["current"]["humidity"],
                "wind_kph": weather_data["current"]["wind_kph"],
                "feels_like_c": weather_data["current"]["feelslike_c"]
            },
            "last_updated": weather_data["current"]["last_updated"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch weather: {str(e)}")


@app.get("/weather/{location}/forecast")
async def get_weather_forecast(
    location: str,
    days: int = Query(3, ge=1, le=10, description="Number of forecast days")
):
    """
    Get weather forecast for a location.
    
    Args:
        location: City name or coordinates
        days: Number of forecast days (1-10)
        
    Returns:
        Current weather plus multi-day forecast
    """
    try:
        forecast_data = await weather_service.get_weather_with_forecast(location, days)
        
        # Format forecast for easier consumption
        formatted_forecast = []
        for day in forecast_data.get("forecast", {}).get("forecastday", []):
            formatted_forecast.append({
                "date": day["date"],
                "max_temp_c": day["day"]["maxtemp_c"],
                "min_temp_c": day["day"]["mintemp_c"],
                "avg_temp_c": day["day"]["avgtemp_c"],
                "condition": day["day"]["condition"]["text"],
                "chance_of_rain": day["day"].get("daily_chance_of_rain", 0),
                "sunrise": day["astro"]["sunrise"],
                "sunset": day["astro"]["sunset"]
            })
        
        return {
            "location": forecast_data["location"]["name"],
            "current": {
                "temp_c": forecast_data["current"]["temp_c"],
                "condition": forecast_data["current"]["condition"]["text"]
            },
            "forecast": formatted_forecast
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch forecast: {str(e)}")


@app.get("/weather/history/{location}")
async def get_weather_history(
    location: str,
    days: int = Query(7, ge=1, le=30, description="Number of days of history")
):
    """
    Get historical weather data from S3/Athena.
    
    Args:
        location: City name
        days: Number of days to look back
        
    Returns:
        Historical weather trend data
    """
    try:
        from app.services import analysis_service
        
        # Use Athena to get historical data
        trend = analysis_service.get_location_weather_trend(location, days)
        
        return {
            "location": location,
            "days": days,
            "history": trend
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch history: {str(e)}")
```

**Verification:**
```bash
# Test each endpoint
curl "https://your-api-url/weather/London"
curl "https://your-api-url/weather/London/forecast?days=5"
curl "https://your-api-url/weather/history/London?days=7"
```

---

### Day 3: Enhanced LLM Service

**Objective:** Include forecast context in outfit suggestions

#### Step 3.1: Update LLM Prompts

**File:** `app/services/llm_service.py`

Replace the existing `get_outfit_suggestion` function:

```python
async def get_outfit_suggestion(
    location: str,
    temp_c: float,
    condition: str,
    forecast: Optional[List[dict]] = None,
    user_context: Optional[dict] = None
) -> str:
    """
    Generate outfit suggestion using LLM with forecast context.
    
    Args:
        location: Location name
        temp_c: Current temperature in Celsius
        condition: Current weather condition
        forecast: Optional forecast data for next few days
        user_context: Optional user preferences (for future use)
        
    Returns:
        Outfit suggestion text
    """
    
    # Build context-aware prompt
    prompt_parts = [
        f"Location: {location}",
        f"Current temperature: {temp_c}°C",
        f"Current condition: {condition}"
    ]
    
    # Add forecast context if available
    if forecast and len(forecast) > 0:
        prompt_parts.append("\nForecast for the next few days:")
        for day in forecast[:3]:  # Only use first 3 days
            prompt_parts.append(
                f"- {day['date']}: {day['min_temp_c']}°C to {day['max_temp_c']}°C, "
                f"{day['condition']}, {day.get('chance_of_rain', 0)}% chance of rain"
            )
    
    # Add time of day context
    from datetime import datetime
    current_hour = datetime.now().hour
    if 6 <= current_hour < 12:
        time_context = "morning"
    elif 12 <= current_hour < 17:
        time_context = "afternoon"
    elif 17 <= current_hour < 21:
        time_context = "evening"
    else:
        time_context = "night"
    
    prompt_parts.append(f"\nTime of day: {time_context}")
    
    # Add user preferences if available (for future use)
    if user_context:
        prompt_parts.append(f"\nUser preferences: {user_context}")
    
    # Build final prompt
    weather_context = "\n".join(prompt_parts)
    
    full_prompt = f"""You are a professional fashion stylist. Based on the weather conditions below, suggest a complete, practical outfit.

Weather Information:
{weather_context}

Please provide:
1. A complete outfit (top, bottom, shoes, outerwear if needed)
2. Brief explanation of why each piece is appropriate
3. Any accessories recommendations (umbrella, sunglasses, etc.)
4. Tips for staying comfortable throughout the day

Keep the suggestion concise (3-4 sentences) and practical."""

    try:
        # Call OpenRouter API
        async with httpx.AsyncClient() as client:
            response = await client.post(
                OPENROUTER_BASE_URL,
                headers={
                    "Authorization": f"Bearer {openrouter_api_key}",
                    "HTTP-Referer": "https://github.com/yourusername/fitted",
                    "X-Title": "Fitted Wardrobe Assistant",
                    "Content-Type": "application/json"
                },
                json={
                    "model": MODEL_NAME,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a helpful fashion stylist who provides practical outfit suggestions based on weather conditions."
                        },
                        {
                            "role": "user",
                            "content": full_prompt
                        }
                    ],
                    "temperature": 0.7,
                    "max_tokens": 300
                },
                timeout=30.0
            )
            
            response.raise_for_status()
            result = response.json()
            
            suggestion = result["choices"][0]["message"]["content"]
            return suggestion.strip()
            
    except Exception as e:
        print(f"Error calling LLM: {e}")
        # Fallback to rule-based suggestion
        return _get_fallback_suggestion(temp_c, condition, forecast is not None)


def _get_fallback_suggestion(temp_c: float, condition: str, has_forecast: bool) -> str:
    """Rule-based fallback when LLM fails"""
    
    # Temperature-based clothing
    if temp_c < 10:
        outfit = "warm layers (sweater + jacket), long pants, closed shoes, scarf"
    elif temp_c < 20:
        outfit = "light jacket or cardigan, jeans or chinos, comfortable shoes"
    else:
        outfit = "t-shirt or light top, shorts or light pants, breathable shoes"
    
    # Weather condition additions
    if "rain" in condition.lower():
        outfit += ", waterproof jacket, umbrella"
    elif "snow" in condition.lower():
        outfit += ", winter coat, warm boots"
    elif temp_c > 25:
        outfit += ", sunglasses, light hat"
    
    forecast_note = " Consider checking the forecast for temperature changes." if has_forecast else ""
    
    return f"Based on {temp_c}°C and {condition} conditions, wear: {outfit}.{forecast_note}"
```

#### Step 3.2: Update Suggest Outfit Endpoint

**File:** `app/main.py`

Update the `/suggest-outfit` endpoint to use forecast:

```python
@app.post("/suggest-outfit")
async def suggest_outfit(
    location: str,
    include_forecast: bool = Query(True, description="Include forecast in suggestion")
):
    """
    Suggest outfit based on current weather and optional forecast.
    
    Args:
        location: Location name
        include_forecast: Whether to include forecast data
    """
    try:
        # Get weather data (with or without forecast)
        if include_forecast:
            weather_data = await weather_service.get_weather_with_forecast(location, days=3)
            forecast = weather_data.get("forecast", {}).get("forecastday", [])
            formatted_forecast = [
                {
                    "date": day["date"],
                    "min_temp_c": day["day"]["mintemp_c"],
                    "max_temp_c": day["day"]["maxtemp_c"],
                    "condition": day["day"]["condition"]["text"],
                    "chance_of_rain": day["day"].get("daily_chance_of_rain", 0)
                }
                for day in forecast
            ]
        else:
            weather_data = await weather_service.get_weather_data(location)
            formatted_forecast = None

        # Extract current weather
        temp_c = weather_data["current"]["temp_c"]
        condition = weather_data["current"]["condition"]["text"]

        # Get LLM suggestion with forecast context
        outfit_suggestion = await llm_service.get_outfit_suggestion(
            location=location,
            temp_c=temp_c,
            condition=condition,
            forecast=formatted_forecast
        )

        return {
            "location": location,
            "weather": {
                "current": {
                    "temp_c": temp_c,
                    "temp_f": weather_data["current"]["temp_f"],
                    "condition": condition,
                    "humidity": weather_data["current"]["humidity"]
                },
                "forecast": formatted_forecast if include_forecast else None
            },
            "outfit_suggestion": outfit_suggestion
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating outfit: {str(e)}")
```

**Verification:**
```bash
# Test with forecast
curl -X POST "https://your-api-url/suggest-outfit?location=London&include_forecast=true"

# Test without forecast
curl -X POST "https://your-api-url/suggest-outfit?location=London&include_forecast=false"
```

---

### Day 4: Testing & Validation

**Objective:** Comprehensive testing of all new features

#### Step 4.1: Create Test Suite

**File:** `tests/test_week3_features.py`

```python
import pytest
from app.services import weather_service, llm_service
from app.models.weather import WeatherWithForecast


@pytest.mark.asyncio
async def test_forecast_data_structure():
    """Test forecast data returns correct structure"""
    data = await weather_service.get_weather_with_forecast("London", days=3)
    
    assert "location" in data
    assert "current" in data
    assert "forecast" in data
    assert "forecastday" in data["forecast"]
    assert len(data["forecast"]["forecastday"]) == 3


@pytest.mark.asyncio
async def test_forecast_validation():
    """Test Pydantic validation of forecast data"""
    data = await weather_service.get_weather_with_forecast("Paris", days=5)
    
    # Should not raise validation error
    validated = WeatherWithForecast(**data)
    assert validated.forecast is not None
    assert len(validated.forecast.forecastday) == 5


@pytest.mark.asyncio
async def test_llm_with_forecast():
    """Test LLM suggestions include forecast context"""
    forecast = [
        {
            "date": "2024-12-10",
            "min_temp_c": 12,
            "max_temp_c": 18,
            "condition": "Partly cloudy",
            "chance_of_rain": 20
        }
    ]
    
    suggestion = await llm_service.get_outfit_suggestion(
        location="Tokyo",
        temp_c=15,
        condition="Clear",
        forecast=forecast
    )
    
    assert len(suggestion) > 50  # Should be a meaningful suggestion
    assert isinstance(suggestion, str)


@pytest.mark.asyncio
async def test_weather_endpoints_exist():
    """Test that new endpoints are registered"""
    from app.main import app
    from fastapi.testclient import TestClient
    
    client = TestClient(app)
    
    # Test endpoint availability
    response = client.get("/weather/London")
    assert response.status_code in [200, 500]  # May fail without API key
    
    response = client.get("/weather/London/forecast")
    assert response.status_code in [200, 500]
```

**Run tests:**
```bash
pytest tests/test_week3_features.py -v
```

#### Step 4.2: Integration Testing

Test the complete flow:

```bash
# 1. Test current weather endpoint
curl "https://your-api-url/weather/London"

# 2. Test forecast endpoint
curl "https://your-api-url/weather/London/forecast?days=5"

# 3. Test historical data
curl "https://your-api-url/weather/history/London?days=7"

# 4. Test enhanced outfit suggestion
curl -X POST "https://your-api-url/suggest-outfit?location=London&include_forecast=true"

# 5. Verify S3 storage
aws s3 ls s3://your-bucket/raw/weather/forecast/ --recursive | tail -5
```

---

### Day 5: Documentation & Deployment

**Objective:** Update documentation and deploy to production

#### Step 5.1: Update API Documentation

**File:** `README.md`

Add new endpoints section:

```markdown
## API Endpoints (Updated)

### Weather Endpoints

#### Get Current Weather
```
GET /weather/{location}
```
Returns current weather conditions for a location.

#### Get Weather Forecast
```
GET /weather/{location}/forecast?days=3
```
Returns multi-day weather forecast (1-10 days).

#### Get Weather History
```
GET /weather/history/{location}?days=7
```
Returns historical weather data from S3/Athena.

### Outfit Suggestions

#### Suggest Outfit (Enhanced)
```
POST /suggest-outfit?location={location}&include_forecast=true
```
Generate outfit suggestion with forecast context.

### Analytics Endpoints

[Existing analytics endpoints...]
```

#### Step 5.2: Create Deployment Checklist

**File:** `docs/DEPLOYMENT_CHECKLIST.md`

```markdown
# Deployment Checklist - Week 3 Features

## Pre-Deployment

- [ ] All tests passing (`pytest tests/ -v`)
- [ ] Code reviewed
- [ ] Environment variables set:
  - [ ] `WEATHER_API_KEY` in SSM
  - [ ] `OPENROUTER_API_KEY` in SSM
  - [ ] `WEATHER_BUCKET_NAME` in template
- [ ] S3 bucket has proper permissions
- [ ] Athena database exists

## Deployment Steps

1. **Build and test locally:**
   ```bash
   sam build
   sam local start-api
   # Test endpoints locally
   ```

2. **Deploy to AWS:**
   ```bash
   sam deploy
   ```

3. **Verify deployment:**
   ```bash
   # Get API URL
   aws cloudformation describe-stacks \
     --stack-name fitted-wardrobe-dev \
     --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' \
     --output text
   
   # Test new endpoints
   curl "https://YOUR-API-URL/weather/London"
   curl "https://YOUR-API-URL/weather/London/forecast"
   ```

4. **Monitor:**
   - Check CloudWatch Logs
   - Verify S3 forecast data being stored
   - Test outfit suggestions with forecast

## Post-Deployment

- [ ] All endpoints responding correctly
- [ ] Forecast data in S3
- [ ] LLM suggestions include forecast context
- [ ] Error rates < 1%
- [ ] Response times < 2s

## Rollback Plan

If issues occur:
```bash
# Rollback to previous version
aws cloudformation describe-stack-events \
  --stack-name fitted-wardrobe-dev | grep "UPDATE_COMPLETE"

# Or delete and redeploy previous version
```
```

#### Step 5.3: Deploy

```bash
# 1. Build
sam build

# 2. Deploy
sam deploy

# 3. Test
API_URL=$(aws cloudformation describe-stacks \
  --stack-name fitted-wardrobe-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' \
  --output text)

# Test new features
curl "${API_URL}weather/London"
curl "${API_URL}weather/London/forecast?days=5"
curl -X POST "${API_URL}suggest-outfit?location=London&include_forecast=true"
```

---

## Success Criteria

✅ **Functional Requirements:**
- [ ] Forecast data fetched and cached correctly
- [ ] New endpoints return valid responses
- [ ] LLM suggestions improved with forecast context
- [ ] Historical data accessible via Athena
- [ ] All endpoints documented

✅ **Performance Requirements:**
- [ ] Response time < 2s for all endpoints
- [ ] Caching reduces API calls by 80%
- [ ] Error rate < 2%

✅ **Cost Requirements:**
- [ ] Monthly cost < $10 (including forecast calls)
- [ ] Within API free tier limits

---

## Troubleshooting

### Issue: Forecast not returning data

**Solution:**
```python
# Check API key permissions
curl "https://api.weatherapi.com/v1/forecast.json?key=YOUR_KEY&q=London&days=3"

# Verify forecast endpoint
from app.services import weather_service
data = await weather_service.get_weather_with_forecast("London", 3)
print(data.keys())
```

### Issue: LLM suggestions don't mention forecast

**Solution:**
- Check that `forecast` parameter is being passed
- Verify forecast data structure
- Test with mock forecast data

### Issue: S3 storage errors

**Solution:**
```bash
# Check bucket permissions
aws s3api get-bucket-policy --bucket your-bucket-name

# Verify IAM role has PutObject permission
aws iam get-role-policy --role-name FittedApiRole --policy-name S3Access
```

---

## Next Steps

After Week 3 completion:
- **Week 4:** Airflow orchestration for scheduled data collection
- **Week 5:** dbt transformations and Databricks integration
- **Week 6-8:** User profiles and authentication

---

## Resources

- [WeatherAPI Forecast Documentation](https://www.weatherapi.com/docs/#apis-forecast)
- [FastAPI Path Parameters](https://fastapi.tiangolo.com/tutorial/path-params/)
- [Pydantic Models](https://docs.pydantic.dev/latest/concepts/models/)