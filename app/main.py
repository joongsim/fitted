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
async def suggest_outfit(location: str):
    weather_data = await weather_service.get_weather_data(location)

    # Extract temperature and condition from weather data
    temp_c = weather_data["current"]["temp_c"]
    condition = weather_data["current"]["condition"]["text"]

    # Call the LLM service to get an outfit suggestion
    outfit_suggestion = await llm_service.get_outfit_suggestion(
        location=location,
        temp_c=temp_c,
        condition=condition
    )

    # Return both weather data and the outfit suggestion
    return {
        "location": location,
        "weather": weather_data,
        "outfit_suggestion": outfit_suggestion
    }

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