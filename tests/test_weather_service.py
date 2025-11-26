import pytest
import os
import httpx
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi import HTTPException
from app.services import weather_service

# Mock data for tests
MOCK_WEATHER_RESPONSE = {
    "location": {"name": "London"},
    "current": {"temp_c": 15.0, "condition": {"text": "Sunny"}}
}

@pytest.mark.asyncio
async def test_get_weather_data_no_api_key():
    """Test fallback to mock data when API key is missing"""
    with patch.dict(os.environ, {}, clear=True):
        # Reload module to pick up empty env
        # Note: In a real scenario, we might need to reload the module or patch the variable directly
        with patch('app.services.weather_service.WEATHER_API_KEY', None):
            data = await weather_service.get_weather_data("London")
            assert data["location"]["name"] == "London"
            assert data["current"]["temp_c"] == 14.0  # Mock data value

@pytest.mark.asyncio
async def test_get_weather_data_success():
    """Test successful API call and S3 storage"""
    with patch('app.services.weather_service.WEATHER_API_KEY', 'fake-key'):
        with patch('httpx.AsyncClient') as mock_client:
            # Setup mock response
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = MOCK_WEATHER_RESPONSE
            
            # Setup async context manager for client
            mock_client_instance = AsyncMock()
            mock_client_instance.get.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_client_instance
            
            # Mock storage service
            with patch('app.services.weather_service.store_raw_weather_data', new_callable=AsyncMock) as mock_store:
                data = await weather_service.get_weather_data("London")
                
                # Verify response
                assert data == MOCK_WEATHER_RESPONSE
                
                # Verify API was called correctly
                mock_client_instance.get.assert_called_once()
                call_args = mock_client_instance.get.call_args
                assert "London" in call_args[1]['params']['q']
                
                # Verify storage was called
                mock_store.assert_called_once_with("London", MOCK_WEATHER_RESPONSE)

@pytest.mark.asyncio
async def test_get_weather_data_api_error():
    """Test handling of API errors"""
    with patch('app.services.weather_service.WEATHER_API_KEY', 'fake-key'):
        with patch('httpx.AsyncClient') as mock_client:
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
    with patch('app.services.weather_service.WEATHER_API_KEY', 'fake-key'):
        with patch('httpx.AsyncClient') as mock_client:
            mock_client_instance = AsyncMock()
            mock_client_instance.get.side_effect = Exception("Network Error")
            mock_client.return_value.__aenter__.return_value = mock_client_instance
            
            with pytest.raises(HTTPException) as exc:
                await weather_service.get_weather_data("London")
            
            assert exc.value.status_code == 503