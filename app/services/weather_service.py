import httpx
import os
import time
import boto3
import json
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException

from app.services.storage_service import store_raw_weather_data
from app.models.weather import WeatherResponse
from app.core.config import config

BASE_URL = "https://api.weatherapi.com/v1"


# Simple in-memory cache to avoid redundant API calls
_weather_cache = {}
CACHE_TTL = 900  # seconds


async def _get_mock_data(location: str):
    """
    Fetches weather data for a given location.

    This is a placeholder function that simulates fetching weather data
    for testing purposes. In a real application, this would call an external
    weather API.
    """

    print(f"Fetching weather for {location}...")
    return {
        "location": {
            "name": "Tokyo",
            "region": "Tokyo",
            "country": "Japan",
            "lat": 35.6895,
            "lon": 139.6917,
            "tz_id": "Asia/Tokyo",
            "localtime_epoch": 1765056792,
            "localtime": "2025-12-07 06:33",
        },
        "current": {
            "last_updated_epoch": 1765056600,
            "last_updated": "2025-12-07 06:30",
            "temp_c": 5.2,
            "temp_f": 41.4,
            "is_day": 0,
            "condition": {
                "text": "Clear",
                "icon": "//cdn.weatherapi.com/weather/64x64/night/113.png",
                "code": 1000,
            },
            "wind_mph": 4.7,
            "wind_kph": 7.6,
            "humidity": 75,
            "cloud": 0,
            "feelslike_c": 3.5,
            "feelslike_f": 38.3,
            "uv": 0.0,
        },
    }


async def get_weather_data(location: str):
    """
    Fetches real weather data.
    """

    # CHECK MEMORY CACHE FIRST
    if location in _weather_cache:
        data, timestamp = _weather_cache[location]
        if time.time() - timestamp < CACHE_TTL:
            print("Returning cached weather data.")
            return data
        else:
            del _weather_cache[location]  # Cache expired
    # END MEMORY CACHE CHECK
    # Check S3 for cached data
    s3 = boto3.client("s3")
    bucket_name = os.environ.get("WEATHER_BUCKET_NAME")

    # Calculate prefix for today to narrow search
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Sanitize location to match storage format
    safe_location = "".join(
        c for c in location if c.isalnum() or c in ("-", "_")
    ).lower()
    prefix = f"raw/weather/dt={today}/location={safe_location}/"

    try:
        # List objects in the bucket with the prefix
        response = s3.list_objects_v2(
            Bucket=bucket_name,
            Prefix=prefix,
        )
        if "Contents" in response:
            # Sort by LastModified to get the most recent file
            latest_file = sorted(response['Contents'], key=lambda x: x['LastModified'], reverse=True)[0]
            # Check if it's recent enough 
            age = datetime.now(timezone.utc) - latest_file['LastModified']
            if age < timedelta(seconds=CACHE_TTL):
                print(f"Cache hit (S3) Found data from {age.seconds}s ago")
                # Get the object
                obj = s3.get_object(Bucket=bucket_name, Key=latest_file['Key'])
                data = json.loads(obj['Body'].read())
                
                # Update in-memory cache
                _weather_cache[location] = (data, time.time())
                return data
    except Exception as e:
        print(f"Cache miss (S3) or error: {e}")
        
    # Check if we have a key. If not, use mock data (great for testing without using quota).
    try:
        weather_api_key = config.weather_api_key
    except Exception as e:
        print(f"⚠️ No API key found: {e}. Using mock data.")
        return await _get_mock_data(location)

    # Use AsyncClient for non-blocking calls
    async with httpx.AsyncClient() as client:
        try:
            # Construct the request
            response = await client.get(
                f"{BASE_URL}/current.json",
                params={
                    "key": weather_api_key,
                    "q": location,
                    "aqi": "no",  # We don't need air quality index yet
                },
            )

            # Raise an exception for 4xx or 5xx status codes
            response.raise_for_status()
            print("Weather data fetched successfully.", response, flush=True)
            data = response.json()
            # Validate the response structure
            validated_data = WeatherResponse(**data)

            # SIDE EFFECT: Store the raw data in our Data Lake (S3)
            # We use 'await' here, but in a high-scale system, we might want to
            # make this a background task so the user doesn't wait for S3.
            await store_raw_weather_data(location, validated_data.model_dump())

            # CACHE THE RESULT
            _weather_cache[location] = (validated_data.model_dump(), time.time())
            return validated_data.model_dump()

        except httpx.HTTPStatusError as e:
            # Handle specific API errors (e.g., 401 Unauthorized, 404 Not Found)
            print(f"API Error: {e}")
            raise HTTPException(
                status_code=e.response.status_code, detail="Weather service error"
            )
        except Exception as e:
            # Handle network errors or other unexpected issues
            print(f"Unexpected error: {e}")
            raise HTTPException(status_code=503, detail="Service unavailable")

async def get_weather_with_forecast(location: str, days: int = 1) -> dict:
    """
    Fetch current weather plus forecast data.
    
    Args:
        location: Location name or coordinates
        days: Number of forecast days (1-10, default 0)
        
    Returns:
        Dictionary with current weather and forecast
    """
    # Validate days parameter
    if not 1 <= days <= 10:
        days = 1
    
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
    
    # Fetch from API with retries
    headers = {
        "User-Agent": "FittedWardrobe/1.0 (AWS Lambda; Python 3.13)",
        "Accept": "application/json"
    }
    
    async with httpx.AsyncClient(headers=headers) as client:
        last_error = None
        for attempt in range(3):
            try:
                response = await client.get(
                    f"{BASE_URL}/forecast.json",
                    params={
                        "key": weather_api_key,
                        "q": location,
                        "days": days,
                        "aqi": "no"
                    },
                    timeout=15.0
                )
                
                # If we get a 502, 503, or 504, retry after a short delay
                if response.status_code in [502, 503, 504] and attempt < 2:
                    print(f"⚠️ WeatherAPI returned {response.status_code}. Retrying attempt {attempt + 1}...")
                    import asyncio
                    await asyncio.sleep(1 * (attempt + 1))
                    continue
                    
                response.raise_for_status()
                data = response.json()
                
                # Validate with Pydantic
                from app.models.weather import WeatherWithForecast
                validated_data = WeatherWithForecast(**data)
                
                # Store in S3 (with forecast flag in key)
                # We wrap this in a try/except to ensure weather data is returned 
                # even if S3 storage has a permission/config issue
                try:
                    await store_raw_weather_data(
                        location, 
                        validated_data.model_dump(),
                        is_forecast=True
                    )
                except Exception as s3_err:
                    print(f"Non-fatal error storing to S3: {s3_err}")
                
                # Cache the result
                _weather_cache[cache_key] = (validated_data.model_dump(), time.time())
                
                return validated_data.model_dump()
                
            except httpx.HTTPStatusError as e:
                last_error = e
                print(f"API Error (Attempt {attempt + 1}): {e}")
                if attempt == 2: # Last attempt
                    raise HTTPException(
                        status_code=e.response.status_code,
                        detail=f"Weather forecast service error: {e.response.text}"
                    )
            except Exception as e:
                last_error = e
                print(f"Unexpected error (Attempt {attempt + 1}): {e}")
                if attempt == 2:
                    raise HTTPException(status_code=503, detail=f"Service unavailable: {str(e)}")


async def _get_mock_forecast_data(location: str, days: int = 1) -> dict:
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
    
    
    