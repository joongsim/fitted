# app/main.py
from datetime import datetime
from typing import Optional
import boto3
from fastapi import FastAPI, HTTPException
from mangum import Mangum
from app.services import weather_service
from app.services import llm_service # Import the new LLM service
from app.core.config import config
from scripts.analyze_weather import query_weather_file

app = FastAPI()

# Lambda handler - this is what AWS Lambda will call
handler = Mangum(app)

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
        query_weather_file(bucket, key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error analyzing weather file: {str(e)}")

    return {
        "message": "Weather analysis completed.",
        "bucket": bucket,
        "key": key
    }