import httpx
import os
import time
import boto3
import json
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException

from app.services.storage_service import store_raw_weather_data
from app.models.weather import WeatherResponse

# Try to load .env file for local development (optional - won't exist in Lambda)
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    # dotenv not installed (Lambda environment)
    pass


def get_api_key():
    """Get API key from environment variables."""
    return os.environ.get("WEATHER_API_KEY")


# Get the key from the environment.
# In local dev, you might set this in your terminal or a .env file.
# In AWS Lambda, this will be in the function configuration.
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY")
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
    if not WEATHER_API_KEY:
        print("⚠️ No API key found. Using mock data.")
        return await _get_mock_data(location)

    # Use AsyncClient for non-blocking calls
    async with httpx.AsyncClient() as client:
        try:
            # Construct the request
            response = await client.get(
                f"{BASE_URL}/current.json",
                params={
                    "key": WEATHER_API_KEY,
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
