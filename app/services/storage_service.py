import json
import logging
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Initialize S3 client once at module load; degrade gracefully if credentials
# are absent (e.g., local dev without AWS profile configured).
try:
    s3_client = boto3.client("s3")
except Exception:
    logger.warning(
        "Could not initialize S3 client — S3 storage operations will be skipped.",
        exc_info=True,
    )
    s3_client = None

WEATHER_BUCKET = os.environ.get("WEATHER_BUCKET_NAME")
IS_LOCAL = os.environ.get("IS_LOCAL", "false").lower() == "true"


async def store_raw_weather_data(
    location: str, data: dict, is_forecast: bool = False
) -> None:
    """
    Store raw weather API response in S3 (Bronze Layer).

    Args:
        location: The location name (e.g., "London").
        data: The raw JSON response from the weather API.
        is_forecast: Whether the data includes forecast information.
    """
    is_lambda = os.environ.get("AWS_EXECUTION_ENV") is not None

    if IS_LOCAL and not is_lambda:
        logger.debug(
            "Running locally — skipping S3 upload for location=%s.", location
        )
        return

    if not s3_client:
        logger.error(
            "S3 client not initialized — cannot store weather data for location=%s.",
            location,
        )
        return

    if not WEATHER_BUCKET:
        logger.warning(
            "WEATHER_BUCKET_NAME not set — skipping S3 storage for location=%s.",
            location,
        )
        return

    try:
        timestamp = datetime.now(timezone.utc)
        date_partition = timestamp.strftime("%Y-%m-%d")
        time_partition = timestamp.strftime("%H-%M-%S")

        safe_location = "".join(
            c for c in location if c.isalnum() or c in ("-", "_")
        ).lower()

        data_type = "forecast" if is_forecast else "current"
        key = f"raw/weather/{data_type}/dt={date_partition}/location={safe_location}/{time_partition}.json"

        logger.debug(
            "Writing %s weather data to s3://%s/%s", data_type, WEATHER_BUCKET, key
        )

        s3_client.put_object(
            Bucket=WEATHER_BUCKET,
            Key=key,
            Body=json.dumps(data),
            ContentType="application/json",
            Metadata={"data-type": data_type, "location": safe_location},
        )

        logger.debug(
            "Successfully stored %s weather data to s3://%s/%s",
            data_type,
            WEATHER_BUCKET,
            key,
        )

    except ClientError:
        logger.error(
            "S3 ClientError storing %s weather data for location=%s.",
            data_type,
            location,
            exc_info=True,
        )
    except Exception:
        logger.error(
            "Unexpected error in store_raw_weather_data for location=%s.",
            location,
            exc_info=True,
        )
