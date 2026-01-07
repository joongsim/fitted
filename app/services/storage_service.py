import boto3
import json
import os
import asyncio
from functools import partial
from datetime import datetime, timezone
from botocore.exceptions import ClientError

# Initialize S3 client
# We use a try-except block to handle cases where AWS credentials might not be available locally
try:
    s3_client = boto3.client('s3')
except Exception as e:
    print(f"Warning: Could not initialize S3 client: {e}")
    s3_client = None

WEATHER_BUCKET = os.environ.get('WEATHER_BUCKET_NAME')
IS_LOCAL = os.environ.get('IS_LOCAL', 'false').lower() == 'true'

async def store_raw_weather_data(location: str, data: dict, is_forecast: bool = False):
    """
    Store raw weather API response in S3 (Bronze Layer).
    
    Args:
        location: The location name (e.g., "London")
        data: The raw JSON response from the weather API
        is_forecast: Whether the data includes forecast information
    """
    # Check if we are running in Lambda or locally
    is_lambda = os.environ.get('AWS_EXECUTION_ENV') is not None
    
    if IS_LOCAL and not is_lambda:
        print(f"ℹ️  Running locally. Skipping S3 upload for {location}.")
        return

    if not s3_client:
        print("Error: S3 client not initialized. Cannot store weather data.")
        return
        
    if not WEATHER_BUCKET:
        print("Warning: WEATHER_BUCKET_NAME not set. Skipping S3 storage.")
        return

    try:
        timestamp = datetime.now(timezone.utc)
        # Partition by date and location
        # Structure: raw/weather/dt=YYYY-MM-DD/location=city/HH-MM-SS.json
        date_partition = timestamp.strftime('%Y-%m-%d')
        time_partition = timestamp.strftime('%H-%M-%S')
        
        # Sanitize location for S3 key
        safe_location = "".join(c for c in location if c.isalnum() or c in ('-', '_')).lower()
        
        data_type = "forecast" if is_forecast else "current"
        key = f"raw/weather/{data_type}/dt={date_partition}/location={safe_location}/{time_partition}.json"
        
        print(f"Attempting to store {data_type} weather data to s3://{WEATHER_BUCKET}/{key}")
        
        # Call S3 directly (blocking call is fine here as we are in an executor-like context in Lambda)
        # Or better, just use the client directly to ensure it finishes.
        s3_client.put_object(
            Bucket=WEATHER_BUCKET,
            Key=key,
            Body=json.dumps(data),
            ContentType='application/json',
            Metadata={
                'data-type': data_type,
                'location': safe_location
            }
        )
        
        print(f"Successfully stored weather data to s3://{WEATHER_BUCKET}/{key}")
        
    except ClientError as e:
        print(f"Error storing data in S3: {e}")
    except Exception as e:
        print(f"Unexpected error in store_raw_weather_data: {e}")