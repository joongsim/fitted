import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

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
    if IS_LOCAL:
        logger.debug("Running locally — skipping S3 upload for location=%s.", location)
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


def upload_wardrobe_image(
    file_content: bytes,
    content_type: str,
    user_id: str,
    item_id: str,
) -> Optional[str]:
    """
    Upload a wardrobe item image to S3.

    Stored at ``wardrobe-images/{user_id}/{item_id}.jpg`` regardless of the
    original filename or content-type, to keep paths predictable.

    Args:
        file_content: Raw image bytes.
        content_type: MIME type (e.g. ``"image/jpeg"``).
        user_id: UUID of the owning user (used as a path prefix).
        item_id: UUID of the wardrobe item (used as the filename stem).

    Returns:
        The S3 key on success, or ``None`` if the upload fails or S3 is
        unavailable (graceful degrade — callers should treat None as "no image").
    """
    if not s3_client:
        logger.warning(
            "S3 client not initialized — skipping wardrobe image upload for item_id=%s.",
            item_id,
        )
        return None

    if not WEATHER_BUCKET:
        logger.warning(
            "WEATHER_BUCKET_NAME not set — skipping wardrobe image upload for item_id=%s.",
            item_id,
        )
        return None

    key = f"wardrobe-images/{user_id}/{item_id}.jpg"
    try:
        s3_client.put_object(
            Bucket=WEATHER_BUCKET,
            Key=key,
            Body=file_content,
            ContentType=content_type,
        )
        logger.info(
            "Wardrobe image uploaded: s3://%s/%s (%d bytes)",
            WEATHER_BUCKET,
            key,
            len(file_content),
        )
        return key
    except ClientError:
        logger.error(
            "S3 ClientError uploading wardrobe image for item_id=%s.",
            item_id,
            exc_info=True,
        )
    except Exception:
        logger.error(
            "Unexpected error uploading wardrobe image for item_id=%s.",
            item_id,
            exc_info=True,
        )
    return None


def get_image_presigned_url(s3_key: str, expiry_seconds: int = 3600) -> Optional[str]:
    """
    Generate a presigned GET URL for an S3 object.

    The URL is valid for ``expiry_seconds`` (default 1 hour).  Callers should
    re-generate the URL on each API response rather than caching it.

    Args:
        s3_key: The S3 object key (e.g. ``"wardrobe-images/{user_id}/{item_id}.jpg"``).
        expiry_seconds: TTL of the presigned URL in seconds.

    Returns:
        A presigned HTTPS URL, or ``None`` if generation fails or S3 is unavailable.
    """
    if not s3_client:
        logger.warning(
            "S3 client not initialized — cannot generate presigned URL for key=%s.",
            s3_key,
        )
        return None

    if not WEATHER_BUCKET:
        logger.warning(
            "WEATHER_BUCKET_NAME not set — cannot generate presigned URL for key=%s.",
            s3_key,
        )
        return None

    try:
        url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": WEATHER_BUCKET, "Key": s3_key},
            ExpiresIn=expiry_seconds,
        )
        logger.debug(
            "Generated presigned URL for key=%s (expires=%ds)", s3_key, expiry_seconds
        )
        return url
    except ClientError:
        logger.error(
            "S3 ClientError generating presigned URL for key=%s.", s3_key, exc_info=True
        )
    except Exception:
        logger.error(
            "Unexpected error generating presigned URL for key=%s.",
            s3_key,
            exc_info=True,
        )
    return None
