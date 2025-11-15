import httpx
from fastapi import HTTPException

async def get_weather_data(location: str):
    """
    Fetches weather data for a given location.

    This is a placeholder function. In a real application, you would
    integrate with a weather API provider like OpenWeatherMap or WeatherAPI.com.
    You would need to handle API keys, error responses, and the structure
    of the weather data.
    """
    # For demonstration purposes, we'll return mock data.
    # In a real implementation, you would make an API call like this:
    #
    # weather_api_url = f"https://api.weatherapi.com/v1/current.json?key=YOUR_API_KEY&q={location}"
    # async with httpx.AsyncClient() as client:
    #     response = await client.get(weather_api_url)
    #     if response.status_code != 200:
    #         raise HTTPException(status_code=404, detail="Weather data not found for this location.")
    #     return response.json()

    print(f"Fetching weather for {location}...")
    return {
        "location": {
            "name": location,
            "region": "California",
            "country": "USA",
        },
        "current": {
            "temp_c": 14.0,
            "temp_f": 57.2,
            "condition": {
                "text": "Partly cloudy",
            },
            "wind_mph": 5.0,
            "wind_dir": "W",
            "precip_mm": 0.0,
            "humidity": 82,
            "cloud": 50,
        }
    }