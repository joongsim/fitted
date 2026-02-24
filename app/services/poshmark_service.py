"""Poshmark RapidAPI client, response parser, and S3 storage helpers."""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote, urlparse

import httpx

from app.models.catalog_item import (
    CatalogItemCreate,
    PoshmarkListingRaw,
    make_content_hash,
)

logger = logging.getLogger(__name__)

RAPIDAPI_HOST = "poshmark.p.rapidapi.com"
BASE_URL = f"https://{RAPIDAPI_HOST}"
REQUEST_TIMEOUT = 15.0
MAX_RETRIES = 3
RETRY_DELAYS = [2, 4, 8]  # seconds — exponential backoff on 429/5xx

ALLOWED_CONDITIONS = {"nwt", "nwot", "good"}
MIN_PRICE = 5.0
MAX_PRICE = 2000.0

# Only download images from known Poshmark CDN hostnames.
# Prevents SSRF by rejecting URLs pointing to internal AWS metadata endpoints
# (169.254.169.254), VPC-internal addresses, or arbitrary external hosts.
ALLOWED_IMAGE_HOSTS = {
    "di2ponv0v5otw.cloudfront.net",
    "poshmark.com",
    "cdn.poshmark.com",
}

MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB hard cap — protects EC2 memory + S3 cost


def _build_headers(api_key: str) -> dict:
    """Build RapidAPI auth headers for Poshmark requests."""
    return {
        "x-rapidapi-key": api_key,
        "x-rapidapi-host": RAPIDAPI_HOST,
        "Accept-Encoding": "gzip, deflate",
        "Content-Type": "application/json",
    }


def _slugify(text: str) -> str:
    """Convert a query string to a safe S3 key segment."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:80]


def is_quality_listing(listing: PoshmarkListingRaw) -> bool:
    """
    Return True if the listing meets minimum quality criteria.

    Filters:
    - Condition must be nwt, nwot, or good (excludes fair/poor)
    - Price must be present and within the valid range (5–2000 USD)
    - Title must be a non-empty string of at least 3 characters
    - Cover shot must exist and have a url_small value
    """
    if listing.condition is not None and listing.condition not in ALLOWED_CONDITIONS:
        return False
    if listing.price_amount is None:
        return False
    try:
        price = float(listing.price_amount.val)
    except (ValueError, TypeError):
        return False
    if not (MIN_PRICE <= price <= MAX_PRICE):
        return False
    if not listing.title or len(listing.title.strip()) < 3:
        return False
    if listing.cover_shot is None or not listing.cover_shot.url_small:
        return False
    return True


def parse_listing(
    listing: PoshmarkListingRaw, query_context: str
) -> Optional[CatalogItemCreate]:
    """
    Normalize a validated PoshmarkListingRaw to a CatalogItemCreate.

    Returns None (and logs at DEBUG) for listings that fail the quality filter.
    The query_context is stored in attributes for training signal purposes
    (records which search query surfaced this item).
    """
    if not is_quality_listing(listing):
        logger.debug(
            "Filtered listing id=%s condition=%s price_amount=%s",
            listing.id,
            listing.condition,
            listing.price_amount,
        )
        return None

    price = float(listing.price_amount.val)  # type: ignore[union-attr]
    title = listing.title or ""
    brand = listing.brand or ""
    category = listing.category or ""

    content_hash = make_content_hash(title, price, brand, category)
    product_url = f"https://poshmark.com/listing/{listing.id}"

    attributes = listing.to_attributes()
    attributes["query_context"] = query_context[:200]

    return CatalogItemCreate(
        item_id=listing.id,
        domain="fashion",
        title=title,
        price=price,
        product_url=product_url,
        source="poshmark_seed",
        content_hash=content_hash,
        attributes=attributes,
        # image_url is populated separately after S3 upload
    )


async def search_listings(
    query: str,
    api_key: str,
    *,
    category: Optional[str] = None,
    department: Optional[str] = None,
    size: Optional[str] = None,
    sort_by: Optional[str] = None,
    page: int = 1,
) -> list[PoshmarkListingRaw]:
    """
    Search Poshmark listings via RapidAPI.

    Returns a list of validated PoshmarkListingRaw objects for the given page.
    Invalid records are skipped with a warning. Retries on 429 and 5xx with
    exponential backoff.

    Args:
        query: Search keyword string.
        api_key: RapidAPI key.
        category: Optional Poshmark category filter (e.g. 'Tops').
        department: Optional department filter ('Men' | 'Women' | 'Kids').
        size: Optional size filter.
        sort_by: Optional sort order sent as 'sort' param (e.g. 'popularity', 'price_asc', 'price_desc').
        page: Page number (1-indexed).

    Returns:
        List of parsed listing objects (may be empty if no results or all filtered).
    """
    # Encode query with %20 (not +) to match the API's expected format
    encoded_query = quote(query, safe="")
    base_url = f"{BASE_URL}/search?query={encoded_query}"

    params: dict = {"domain": "com", "page": page}
    if category:
        params["category"] = category
    if department:
        params["department"] = department
    if size:
        params["size"] = size
    if sort_by:
        params["sort"] = sort_by

    headers = _build_headers(api_key)

    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.get(
                    base_url,
                    params=params,
                    headers=headers,
                )

            if response.status_code == 429 and attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                logger.warning(
                    "Rate limited by RapidAPI — sleeping %ds (attempt %d/%d)",
                    delay,
                    attempt + 1,
                    MAX_RETRIES,
                )
                await asyncio.sleep(delay)
                continue

            response.raise_for_status()
            data = response.json()

        except httpx.HTTPStatusError:
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                logger.warning(
                    "HTTP error on attempt %d/%d, retrying in %ds",
                    attempt + 1,
                    MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            raise
        except Exception:
            logger.error("Unexpected error calling Poshmark API", exc_info=True)
            raise

        # Log response structure to help diagnose unexpected shapes
        if isinstance(data, dict):
            logger.debug("API response keys: %s", list(data.keys()))
        elif isinstance(data, list):
            logger.debug("API response: list of %d items", len(data))
        else:
            logger.debug("API response type: %s", type(data).__name__)

        # Parse and validate each listing; skip malformed entries
        raw_listings = data if isinstance(data, list) else data.get("data", [])
        results: list[PoshmarkListingRaw] = []
        for raw in raw_listings:
            try:
                results.append(PoshmarkListingRaw.model_validate(raw))
            except Exception:
                logger.warning(
                    "Failed to parse listing: %s",
                    str(raw)[:200],
                    exc_info=True,
                )
        logger.debug(
            "search_listings query=%r page=%d returned %d raw, %d parsed",
            query,
            page,
            len(raw_listings),
            len(results),
        )
        return results

    # Should not reach here — last attempt raises
    return []  # pragma: no cover


async def download_image(
    url: str,
    item_id: str,
    s3_client,
    bucket: str,
    sem: asyncio.Semaphore,
) -> Optional[str]:
    """
    Download a Poshmark cover image and upload it to S3.

    Non-fatal: returns None on any error rather than raising, so a single
    failed image does not abort the full ingestion batch.

    Security guards:
    - Hostname must be in ALLOWED_IMAGE_HOSTS (prevents SSRF)
    - Response Content-Type must start with 'image/' (rejects HTML error pages)
    - Response body capped at MAX_IMAGE_BYTES = 5 MB (protects memory + S3 cost)

    Args:
        url: Poshmark CDN image URL.
        item_id: Poshmark listing ID (used as the S3 key suffix).
        s3_client: Initialized boto3 S3 client.
        bucket: S3 bucket name.
        sem: Semaphore to limit concurrent downloads.

    Returns:
        S3 URL string on success, None on failure.
    """
    # SSRF guard: only download from known Poshmark CDN hostnames
    try:
        parsed = urlparse(url)
        if parsed.hostname not in ALLOWED_IMAGE_HOSTS:
            logger.warning(
                "Rejected image URL with disallowed host '%s' for item_id=%s",
                parsed.hostname,
                item_id,
            )
            return None
    except Exception:
        logger.warning("Could not parse image URL for item_id=%s: %s", item_id, url)
        return None

    s3_key = f"images/catalog/poshmark/{item_id}.jpg"

    # Skip if image already exists in S3 (avoids versioning duplication on re-runs)
    try:
        s3_client.head_object(Bucket=bucket, Key=s3_key)
        logger.debug("Image already in S3, skipping download for item_id=%s", item_id)
        return f"s3://{bucket}/{s3_key}"
    except Exception:
        pass  # key does not exist — proceed with download

    async with sem:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                async with client.stream("GET", url, follow_redirects=True) as response:
                    response.raise_for_status()

                    # Content-Type guard: reject non-image responses (HTML error pages, etc.)
                    content_type = response.headers.get("content-type", "")
                    if not content_type.startswith("image/"):
                        logger.warning(
                            "Rejected non-image content-type '%s' for item_id=%s",
                            content_type,
                            item_id,
                        )
                        return None

                    # Size guard: cap at MAX_IMAGE_BYTES to protect EC2 memory
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes(8192):
                        total += len(chunk)
                        if total > MAX_IMAGE_BYTES:
                            logger.warning(
                                "Image exceeds %d bytes, skipping item_id=%s",
                                MAX_IMAGE_BYTES,
                                item_id,
                            )
                            return None
                        chunks.append(chunk)

                    image_bytes = b"".join(chunks)

            s3_client.put_object(
                Bucket=bucket,
                Key=s3_key,
                Body=image_bytes,
                ContentType="image/jpeg",
            )
            s3_url = f"s3://{bucket}/{s3_key}"
            logger.debug("Uploaded image for item_id=%s → %s", item_id, s3_url)
            return s3_url

        except Exception:
            logger.warning(
                "Failed to download/upload image for item_id=%s url=%s",
                item_id,
                url,
                exc_info=True,
            )
            return None


def store_bronze_json(
    listings: list[dict],
    query: str,
    s3_client,
    bucket: str,
) -> None:
    """
    Store raw Poshmark API response JSON to the S3 bronze layer.

    Key format: raw/catalog/poshmark/dt={YYYY-MM-DD}/query={slug}/{HH-MM-SS}.json

    Consistent with the existing weather bronze layer convention
    (raw/weather/{type}/dt={date}/...). Non-fatal: logs errors but does not raise.

    Args:
        listings: List of raw listing dicts from the API response.
        query: Search query string (used in the S3 key path).
        s3_client: Initialized boto3 S3 client.
        bucket: S3 bucket name.
    """
    if not bucket:
        logger.warning("WEATHER_BUCKET_NAME not set — skipping bronze S3 write")
        return
    if s3_client is None:
        logger.warning("S3 client not initialized — skipping bronze S3 write")
        return

    now = datetime.now(tz=timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H-%M-%S")
    slug = _slugify(query)
    key = f"raw/catalog/poshmark/dt={date_str}/query={slug}/{time_str}.json"

    try:
        s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(listings, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
            Metadata={"query": query[:256], "source": "poshmark"},
        )
        logger.debug(
            "Bronze JSON stored: s3://%s/%s (%d listings)", bucket, key, len(listings)
        )
    except Exception:
        logger.error("Failed to store bronze JSON to S3 (key=%s)", key, exc_info=True)
