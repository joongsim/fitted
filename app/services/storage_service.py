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

async def store_raw_weather_data(location: str, data: dict):
    """
    Store raw weather API response in S3 (Bronze Layer).
    
    Args:
        location: The location name (e.g., "London")
        data: The raw JSON response from the weather API
    """
    if IS_LOCAL:
        print(f"ℹ️  Running locally. Skipping S3 upload for {location}.")
        return

    if not s3_client:
        print("Warning: S3 client not initialized. Skipping S3 storage.")
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
        
        key = f"raw/weather/dt={date_partition}/location={safe_location}/{time_partition}.json"
        
        # Run the blocking S3 call in a separate thread
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, # Use default thread pool
            partial(
                s3_client.put_object,
                Bucket=WEATHER_BUCKET,
                Key=key,
                Body=json.dumps(data),
                ContentType='application/json'
            )
        )
        print(f"Successfully stored weather data to s3://{WEATHER_BUCKET}/{key}")
        
    except ClientError as e:
        print(f"Error storing data in S3: {e}")
    except Exception as e:
        print(f"Unexpected error in store_raw_weather_data: {e}")