# app/main.py
from datetime import datetime
from typing import Optional
import boto3
from fastapi import FastAPI, HTTPException, Query
from mangum import Mangum
from app.services import weather_service
from app.services import llm_service
from app.core.config import config
from app.services import analysis_service

app = FastAPI()

# Lambda handler - this is what AWS Lambda will call
# Configure Mangum with lifespan="off" to avoid async context issues in Lambda
handler = Mangum(app, lifespan="off", api_gateway_base_path="/")

@app.get("/")
def read_root():
    return {"message": "Welcome to the Fitted Wardrobe Assistant!"}

@app.get("/debug/config")
def debug_config():
    """Debug endpoint to verify configuration (sensitive values masked)"""
    try:
        openrouter_key = config.openrouter_api_key
        has_openrouter = bool(openrouter_key)
        openrouter_preview = f"{openrouter_key[:10]}..." if openrouter_key else None
    except Exception as e:
        has_openrouter = False
        openrouter_preview = f"Error: {str(e)}"
    
    try:
        weather_key = config.weather_api_key
        has_weather = bool(weather_key)
        weather_preview = f"{weather_key[:10]}..." if weather_key else None
    except Exception as e:
        has_weather = False
        weather_preview = f"Error: {str(e)}"
    
    return {
        "weather_bucket_name": config.weather_bucket_name,
        "has_openrouter_api_key": has_openrouter,
        "openrouter_key_preview": openrouter_preview,
        "has_weather_api_key": has_weather,
        "weather_key_preview": weather_preview,
    }

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

@app.post("/analyze-weather")
async def analyze_weather(bucket: Optional[str] = None, key: Optional[str] = None):
    """Legacy endpoint - queries individual S3 file. Use /analytics/* endpoints for better performance."""
    # Use configured bucket if not provided
    if bucket is None:
        bucket = config.weather_bucket_name
    
    # If key is not provided, try to find the latest file for today
    if key is None:
        today = datetime.now().strftime("%Y-%m-%d")
        prefix = f"raw/weather/dt={today}/"
        
        try:
            s3 = boto3.client("s3")
            response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
            
            if "Contents" not in response:
                raise HTTPException(status_code=404, detail=f"No weather data found for today ({today})")
                
            # Get the most recent file
            latest_file = sorted(response['Contents'], key=lambda x: x['LastModified'], reverse=True)[0]
            key = latest_file['Key']
            print(f"Found latest weather file: {key}")
            
        except Exception as e:
            if isinstance(e, HTTPException):
                raise e
            raise HTTPException(status_code=500, detail=f"Error finding weather data: {str(e)}")
    
    try:
        analysis_service.query_weather_file(bucket, key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error analyzing weather file: {str(e)}")

    return {
        "message": "Weather analysis completed.",
        "bucket": bucket,
        "key": key
    }

@app.get("/analytics/temperature")
async def analytics_by_temperature(
    min_temp: float = Query(15.0, description="Minimum temperature in Celsius"),
    date: Optional[str] = Query(None, description="Date filter (YYYY-MM-DD)")
):
    """
    Query weather data where temperature is above a threshold.
    Uses Athena for efficient SQL-based queries on S3 data.
    """
    try:
        results = analysis_service.query_weather_by_temperature(min_temp, date)
        return {
            "query": f"temperature > {min_temp}°C",
            "date": date or "all dates",
            "count": len(results),
            "results": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analytics query failed: {str(e)}")

@app.get("/analytics/location/{location}")
async def analytics_location_trend(
    location: str,
    days: int = Query(7, ge=1, le=30, description="Number of days to analyze")
):
    """
    Get weather trend for a specific location over past N days.
    Returns daily averages, min/max temperatures, and other metrics.
    """
    try:
        results = analysis_service.get_location_weather_trend(location, days)
        return {
            "location": location,
            "days": days,
            "count": len(results),
            "trend": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Location trend query failed: {str(e)}")

@app.get("/analytics/summary")
async def analytics_summary(
    date: Optional[str] = Query(None, description="Date (YYYY-MM-DD), defaults to today")
):
    """
    Get summary analytics for weather data.
    Includes unique locations, avg/min/max temperatures, total readings.
    """
    try:
        summary = analysis_service.get_weather_analytics_summary(date)
        return {
            "date": date or datetime.now().strftime("%Y-%m-%d"),
            "summary": summary
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Summary analytics failed: {str(e)}")

@app.get("/analytics/condition/{condition}")
async def analytics_by_condition(
    condition: str,
    date: Optional[str] = Query(None, description="Date filter (YYYY-MM-DD)")
):
    """
    Query weather data by condition (e.g., 'Rain', 'Clear', 'Cloudy').
    Returns all locations matching the weather condition.
    """
    try:
        results = analysis_service.get_weather_by_condition(condition, date)
        return {
            "condition": condition,
            "date": date or "all dates",
            "count": len(results),
            "results": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Condition query failed: {str(e)}")
    
    
@app.get("/weather/{location}")
async def get_current_weather(location:str) -> dict:
    """
    Get current weather for a specified location

    Args:
        location (str): string representing the location (city name, coordinates, etc.) 
        
    Returns:
        dict: Current weather data
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
