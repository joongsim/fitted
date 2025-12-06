import pytest
import os
import httpx
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi import HTTPException
from app.services import weather_service

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
    with patch.dict(os.environ, {}, clear=True):
        # Reload module to pick up empty env
        # Note: In a real scenario, we might need to reload the module or patch the variable directly
        with patch("app.services.weather_service.WEATHER_API_KEY", None):
            data = await weather_service.get_weather_data("Tokyo")
            assert data["location"]["name"] == "Tokyo"
            assert data["current"]["temp_c"] == 5.2  # Mock data value


@pytest.mark.asyncio
async def test_get_weather_data_success():
    """Test successful API call and S3 storage"""
    with patch("app.services.weather_service.WEATHER_API_KEY", "fake-key"):
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
    with patch("app.services.weather_service.WEATHER_API_KEY", "fake-key"):
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
    
    with patch("app.services.weather_service.WEATHER_API_KEY", "fake-key"):
        with patch("httpx.AsyncClient") as mock_client:
            mock_client_instance = AsyncMock()
            mock_client_instance.get.side_effect = Exception("Network Error")
            mock_client.return_value.__aenter__.return_value = mock_client_instance

            with pytest.raises(HTTPException) as exc:
                await weather_service.get_weather_data("London")

            assert exc.value.status_code == 503
