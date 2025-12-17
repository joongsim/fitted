import pytest
import os
import httpx
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi import HTTPException
from app.services import weather_service, llm_service
from app.models.weather import (
    WeatherResponse,
    WeatherWithForecast,
    CurrentWeather,
    Location,
    WeatherCondition,
)
from pydantic import ValidationError

# Mock data for tests
MOCK_WEATHER_RESPONSE = {
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


@pytest.mark.asyncio
async def test_get_weather_data_no_api_key():
    """Test fallback to mock data when API key is missing"""
    # Clear cache to ensure we don't get cached data
    weather_service._weather_cache.clear()
    
    # Mock config.get_parameter to raise an exception (simulating no API key)
    with patch("app.core.config.config.get_parameter", side_effect=Exception("No API key")):
        # Mock S3 operations to avoid S3 errors
        with patch("boto3.client"):
            data = await weather_service.get_weather_data("Tokyo")
            assert data["location"]["name"] == "Tokyo"
            assert data["current"]["temp_c"] == 5.2  # Mock data value


@pytest.mark.asyncio
async def test_get_weather_data_success():
    """Test successful API call and S3 storage"""
    # Clear cache to ensure we don't get cached data
    weather_service._weather_cache.clear()
    
    # Mock config.get_parameter to return fake API key
    with patch("app.core.config.config.get_parameter", return_value="fake-key"):
        # Mock S3 operations to avoid S3 errors
        with patch("boto3.client"):
            with patch("httpx.AsyncClient") as mock_client:
                # Setup mock response
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = MOCK_WEATHER_RESPONSE

                # Setup async context manager for client
                mock_client_instance = AsyncMock()
                mock_client_instance.get.return_value = mock_response
                mock_client.return_value.__aenter__.return_value = mock_client_instance

                # Mock storage service
                with patch(
                    "app.services.weather_service.store_raw_weather_data",
                    new_callable=AsyncMock,
                ) as mock_store:
                    data = await weather_service.get_weather_data("London")

                    # Verify response
                    assert data == MOCK_WEATHER_RESPONSE

                    # Verify API was called correctly
                    mock_client_instance.get.assert_called_once()
                    call_args = mock_client_instance.get.call_args
                    assert "London" in call_args[1]["params"]["q"]

                    # Verify storage was called
                    mock_store.assert_called_once_with("London", MOCK_WEATHER_RESPONSE)


@pytest.mark.asyncio
async def test_get_weather_data_api_error():
    """Test handling of API errors"""
    # Clear cache to ensure we don't get cached data
    weather_service._weather_cache.clear()
    
    # Mock config.get_parameter to return fake API key
    with patch("app.core.config.config.get_parameter", return_value="fake-key"):
        # Mock S3 operations to avoid S3 errors
        with patch("boto3.client"):
            with patch("httpx.AsyncClient") as mock_client:
                # Setup mock error response
                mock_response = MagicMock()
                mock_response.status_code = 404
                mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "Not Found", request=MagicMock(), response=mock_response
                )

                mock_client_instance = AsyncMock()
                mock_client_instance.get.return_value = mock_response
                mock_client.return_value.__aenter__.return_value = mock_client_instance

                with pytest.raises(HTTPException) as exc:
                    await weather_service.get_weather_data("InvalidCity")

                assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_weather_data_network_error():
    """Test handling of network errors"""
    # Clear cache to ensure we hit the network
    weather_service._weather_cache.clear()

    # Mock config.get_parameter to return fake API key
    with patch("app.core.config.config.get_parameter", return_value="fake-key"):
        # Mock S3 operations to avoid S3 errors
        with patch("boto3.client"):
            with patch("httpx.AsyncClient") as mock_client:
                mock_client_instance = AsyncMock()
                mock_client_instance.get.side_effect = Exception("Network Error")
                mock_client.return_value.__aenter__.return_value = mock_client_instance

                with pytest.raises(HTTPException) as exc:
                    await weather_service.get_weather_data("London")

                assert exc.value.status_code == 503


def test_valid_weather_response():
    """Test that the valid data is correctly parsed."""
    weather = WeatherResponse(**MOCK_WEATHER_RESPONSE)
    assert weather.location.name == "Tokyo"
    assert weather.current.temp_c == 5.2
    assert weather.current.condition.text == "Clear"


def test_invalid_temperature():
    """Test that invalid temperature raises a validation error."""
    # Create a deep copy of the mock data and modify it to have an invalid temperature
    # Needs to be a deep copy to avoid mutating the original mock data
    invalid_data = MOCK_WEATHER_RESPONSE.copy()
    invalid_data["current"] = invalid_data["current"].copy()
    invalid_data["current"]["temp_c"] = 1000.0  # Unrealistic temperature

    with pytest.raises(ValidationError) as exc_info:
        WeatherResponse(**invalid_data)
        
    # Verify the error message points to the temperature field
    assert "temp_c" in str(exc_info.value)
    assert "less than or equal to 60" in str(exc_info.value)

def test_invalid_humidity():
    """Test that invalid humidity raises a validation error."""
    # Create a deep copy of the mock data and modify it to have an invalid humidity
    # Needs to be a deep copy to avoid mutating the original mock data
    invalid_data = MOCK_WEATHER_RESPONSE.copy()
    invalid_data["current"] = invalid_data["current"].copy()
    invalid_data["current"]["humidity"] = 1000.0  # Unrealistic humidity

    with pytest.raises(ValidationError) as exc_info:
        WeatherResponse(**invalid_data)
        
    # Verify the error message points to the humidity field
    assert "humidity" in str(exc_info.value)
    assert "less than or equal to 100" in str(exc_info.value)

def test_missing_required_field():
    """Test that missing required fields raise a validation error."""
    # Create a deep copy of the mock data and remove a required field
    invalid_data = MOCK_WEATHER_RESPONSE.copy()
    invalid_data["location"] = invalid_data["location"].copy()
    del invalid_data["location"]["name"]  # Remove required field

    with pytest.raises(ValidationError) as exc_info:
        WeatherResponse(**invalid_data)
        
    # Verify the error message points to the missing field
    assert "location" in str(exc_info.value)
    assert "Field required" in str(exc_info.value)
    

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
    # Free tier API typically returns max 3 days
    data = await weather_service.get_weather_with_forecast("Paris", days=3)
    
    # Should not raise validation error
    validated = WeatherWithForecast(**data)
    assert validated.forecast is not None
    # API may return less days than requested on free tier
    assert len(validated.forecast.forecastday) >= 1
    assert len(validated.forecast.forecastday) <= 3


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