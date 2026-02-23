import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import boto3
import httpx
from fastapi import HTTPException

from app.core.config import config
from app.models.weather import WeatherResponse
from app.services.storage_service import store_raw_weather_data

logger = logging.getLogger(__name__)

BASE_URL = "https://api.weatherapi.com/v1"

# Simple in-memory cache to avoid redundant API calls
_weather_cache: dict = {}
CACHE_TTL = 900  # seconds


async def _get_mock_data(location: str) -> dict:
    """
    Return static mock weather data for testing without consuming API quota.

    Args:
        location: Location string (used only for logging; mock data is fixed).

    Returns:
        Dictionary matching the WeatherAPI current-weather response shape.
    """
    logger.debug("Returning mock weather data for location=%s", location)
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


async def get_weather_data(location: str) -> dict:
    """
    Fetch current weather for a location, consulting in-memory then S3 cache
    before calling WeatherAPI.

    Args:
        location: City name or coordinates accepted by WeatherAPI.

    Returns:
        Dictionary matching the WeatherAPI current-weather response shape.

    Raises:
        HTTPException: On WeatherAPI HTTP errors or network failures.
    """
    # --- In-memory cache check ---
    if location in _weather_cache:
        data, timestamp = _weather_cache[location]
        if time.time() - timestamp < CACHE_TTL:
            logger.debug("In-memory cache hit for location=%s", location)
            return data
        del _weather_cache[location]
        logger.debug("In-memory cache expired for location=%s", location)

    # --- S3 cache check ---
    s3 = boto3.client("s3")
    bucket_name = os.environ.get("WEATHER_BUCKET_NAME")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    safe_location = "".join(
        c for c in location if c.isalnum() or c in ("-", "_")
    ).lower()
    prefix = f"raw/weather/dt={today}/location={safe_location}/"

    try:
        response = s3.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
        if "Contents" in response:
            latest_file = sorted(
                response["Contents"], key=lambda x: x["LastModified"], reverse=True
            )[0]
            age = datetime.now(timezone.utc) - latest_file["LastModified"]
            if age < timedelta(seconds=CACHE_TTL):
                logger.info(
                    "S3 cache hit for location=%s (age=%ds, key=%s)",
                    location,
                    age.seconds,
                    latest_file["Key"],
                )
                obj = s3.get_object(Bucket=bucket_name, Key=latest_file["Key"])
                data = json.loads(obj["Body"].read())
                _weather_cache[location] = (data, time.time())
                return data
    except Exception:
        logger.warning(
            "S3 cache lookup failed for location=%s — proceeding to API call.",
            location,
            exc_info=True,
        )

    # --- No API key → fall back to mock ---
    try:
        weather_api_key = config.weather_api_key
    except Exception:
        logger.warning(
            "WeatherAPI key not available — falling back to mock data for location=%s.",
            location,
            exc_info=True,
        )
        return await _get_mock_data(location)

    # --- Live API call ---
    logger.info("Calling WeatherAPI for location=%s", location)
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{BASE_URL}/current.json",
                params={
                    "key": weather_api_key,
                    "q": location,
                    "aqi": "no",
                },
            )
            response.raise_for_status()
            logger.info(
                "WeatherAPI responded successfully for location=%s (status=%d)",
                location,
                response.status_code,
            )
            data = response.json()
            validated_data = WeatherResponse(**data)

            await store_raw_weather_data(location, validated_data.model_dump())
            _weather_cache[location] = (validated_data.model_dump(), time.time())
            return validated_data.model_dump()

        except httpx.HTTPStatusError as e:
            logger.error(
                "WeatherAPI HTTP error for location=%s: status=%d body=%s",
                location,
                e.response.status_code,
                e.response.text[:200],
                exc_info=True,
            )
            raise HTTPException(
                status_code=e.response.status_code, detail="Weather service error"
            )
        except Exception:
            logger.error(
                "Unexpected error fetching weather for location=%s",
                location,
                exc_info=True,
            )
            raise HTTPException(status_code=503, detail="Service unavailable")


async def get_weather_with_forecast(location: str, days: int = 1) -> dict:
    """
    Fetch current weather plus forecast data from WeatherAPI, with retry logic
    for transient 5xx errors.

    Args:
        location: Location name or coordinates.
        days: Number of forecast days (1-10, default 1).

    Returns:
        Dictionary with current weather and forecast.

    Raises:
        HTTPException: On non-retriable WeatherAPI errors or network failures.
    """
    if not 1 <= days <= 10:
        logger.warning(
            "Invalid days=%d requested for location=%s — clamping to 1.", days, location
        )
        days = 1

    cache_key = f"{location}:{days}"
    if cache_key in _weather_cache:
        data, timestamp = _weather_cache[cache_key]
        if time.time() - timestamp < CACHE_TTL:
            logger.debug(
                "In-memory cache hit for forecast: location=%s days=%d", location, days
            )
            return data
        del _weather_cache[cache_key]

    try:
        weather_api_key = config.weather_api_key
    except Exception:
        logger.warning(
            "WeatherAPI key not available — falling back to mock forecast for location=%s.",
            location,
            exc_info=True,
        )
        return await _get_mock_forecast_data(location, days)

    logger.info(
        "Calling WeatherAPI forecast endpoint for location=%s days=%d", location, days
    )
    headers = {
        "User-Agent": "FittedWardrobe/1.0 (AWS Lambda; Python 3.13)",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(headers=headers) as client:
        for attempt in range(3):
            try:
                response = await client.get(
                    f"{BASE_URL}/forecast.json",
                    params={
                        "key": weather_api_key,
                        "q": location,
                        "days": days,
                        "aqi": "no",
                    },
                    timeout=15.0,
                )

                if response.status_code in [502, 503, 504] and attempt < 2:
                    logger.warning(
                        "WeatherAPI returned %d for location=%s — retrying (attempt %d/3).",
                        response.status_code,
                        location,
                        attempt + 1,
                    )
                    await asyncio.sleep(1 * (attempt + 1))
                    continue

                response.raise_for_status()
                data = response.json()

                from app.models.weather import WeatherWithForecast

                validated_data = WeatherWithForecast(**data)

                try:
                    await store_raw_weather_data(
                        location,
                        validated_data.model_dump(),
                        is_forecast=True,
                    )
                except Exception:
                    logger.error(
                        "Non-fatal: failed to store forecast data to S3 for location=%s.",
                        location,
                        exc_info=True,
                    )

                _weather_cache[cache_key] = (validated_data.model_dump(), time.time())
                logger.info(
                    "Forecast fetched and cached for location=%s days=%d", location, days
                )
                return validated_data.model_dump()

            except httpx.HTTPStatusError as e:
                logger.error(
                    "WeatherAPI HTTP error for forecast location=%s (attempt %d/3): status=%d",
                    location,
                    attempt + 1,
                    e.response.status_code,
                    exc_info=True,
                )
                if attempt == 2:
                    raise HTTPException(
                        status_code=e.response.status_code,
                        detail=f"Weather forecast service error: {e.response.text}",
                    )
            except Exception:
                logger.error(
                    "Unexpected error fetching forecast for location=%s (attempt %d/3).",
                    location,
                    attempt + 1,
                    exc_info=True,
                )
                if attempt == 2:
                    raise HTTPException(
                        status_code=503, detail="Service unavailable"
                    )


async def _get_mock_forecast_data(location: str, days: int = 1) -> dict:
    """
    Return static mock forecast data for testing without consuming API quota.

    Args:
        location: Location string (passed through to _get_mock_data).
        days: Number of forecast days to generate.

    Returns:
        Dictionary matching the WeatherAPI forecast response shape.
    """
    current_data = await _get_mock_data(location)
    base_temp = current_data["current"]["temp_c"]

    forecast_days = []
    for i in range(days):
        forecast_days.append(
            {
                "date": f"2024-12-{9 + i:02d}",
                "date_epoch": 1733788800 + (i * 86400),
                "day": {
                    "maxtemp_c": base_temp + (i * 2),
                    "mintemp_c": base_temp - 3,
                    "avgtemp_c": base_temp + (i * 0.5),
                    "condition": {
                        "text": "Partly cloudy",
                        "icon": "//cdn.weatherapi.com/weather/64x64/day/116.png",
                    },
                },
                "astro": {
                    "sunrise": "07:00 AM",
                    "sunset": "05:00 PM",
                },
            }
        )

    return {
        **current_data,
        "forecast": {"forecastday": forecast_days},
    }
