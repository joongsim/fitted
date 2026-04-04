"""
Serper Google Shopping catalog ingestion script.

Searches Google Shopping via Serper's API for a configurable list of menswear
brands, downloads product images to S3, and upserts into catalog_items.

Usage:
    # Dry run — no S3 writes or DB upserts
    PYTHONPATH=. python scripts/ingest_serper_catalog.py --dry-run

    # Full run
    PYTHONPATH=. python scripts/ingest_serper_catalog.py

    # Custom brand list
    PYTHONPATH=. python scripts/ingest_serper_catalog.py --brands-file config/my_brands.json

Environment:
    SERPER_API_KEY        Serper.dev API key
    DATABASE_URL          PostgreSQL connection URL
    WEATHER_BUCKET_NAME   S3 bucket for bronze JSON and images
    USE_SSM=false         Use environment variables instead of SSM (local dev)

Security notes:
    - SERPER_API_KEY must NOT be committed to git or logged
    - All DB values are parameterized — no f-string interpolation of API data
    - Images are validated for content-type and size before S3 upload
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import pathlib
import re
import sys
from datetime import datetime, timezone
from typing import Optional

import boto3
import httpx
import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from app.core.config import config
from app.models.catalog_item import CatalogItemCreate, make_content_hash

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("ingest_serper")

SERPER_SHOPPING_URL = "https://google.serper.dev/shopping"
REQUEST_TIMEOUT = 15.0
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_price(price_str: Optional[str]) -> Optional[float]:
    """
    Parse a USD price string to float.

    Returns None if the price is missing, non-USD, or unparseable.
    Only USD prices (starting with '$') are accepted.
    """
    if not price_str:
        return None
    if not price_str.startswith("$"):
        return None
    cleaned = price_str.lstrip("$").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def make_item_id(link: str) -> str:
    """Generate a stable item_id from the product URL."""
    return f"serper_{hashlib.sha256(link.encode()).hexdigest()[:12]}"


def _slugify(text: str) -> str:
    """Convert a brand name to a safe S3 key segment."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:80]


# ---------------------------------------------------------------------------
# Stubs — implemented in later tasks
# ---------------------------------------------------------------------------

def parse_result(result: dict, brand: str) -> Optional[CatalogItemCreate]:
    """
    Parse a single Serper Google Shopping result into a CatalogItemCreate.

    Returns None if the result is missing required fields or has a non-USD price.
    image_url is intentionally left None — set after S3 upload.
    """
    link = result.get("link") or ""
    if not link:
        return None

    title = result.get("title") or ""
    if not title:
        return None

    price = parse_price(result.get("price"))
    if price is None:
        return None

    item_id = make_item_id(link)
    content_hash = make_content_hash(title, price, brand, "menswear")

    return CatalogItemCreate(
        item_id=item_id,
        domain="fashion",
        title=title[:500],
        price=price,
        image_url=None,
        product_url=link,
        source="serper",
        content_hash=content_hash,
        attributes={"brand": brand[:255], "category": "menswear"},
    )


async def search_shopping(query: str, api_key: str) -> list[dict]:
    """
    Call Serper Google Shopping API and return the raw results list.

    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.post(
            SERPER_SHOPPING_URL,
            headers=headers,
            json={"q": query},
        )
        response.raise_for_status()
        data = response.json()
    return data.get("shopping", [])


async def download_image(
    url: str,
    item_id: str,
    s3_client,
    bucket: str,
    sem: asyncio.Semaphore,
) -> Optional[str]:
    """
    Download a product image and upload it to S3.

    Returns the S3 URL (s3://bucket/key) on success, or None on any failure.
    Validates content-type (must be image/*) and size (max 5 MB).
    """
    async with sem:
        try:
            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT, follow_redirects=True
            ) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "")
                    if not content_type.startswith("image/"):
                        logger.warning(
                            "Skipping %s — non-image content-type: %r",
                            item_id,
                            content_type,
                        )
                        return None

                    chunks = []
                    total = 0
                    async for chunk in response.aiter_bytes(8192):
                        total += len(chunk)
                        if total > MAX_IMAGE_BYTES:
                            logger.warning(
                                "Skipping %s — image exceeds 5 MB", item_id
                            )
                            return None
                        chunks.append(chunk)

                    image_data = b"".join(chunks)

            s3_key = f"images/catalog/serper/{item_id}.jpg"
            s3_client.put_object(
                Bucket=bucket,
                Key=s3_key,
                Body=image_data,
                ContentType="image/jpeg",
            )
            return f"s3://{bucket}/{s3_key}"

        except Exception:
            logger.warning(
                "Failed to download/upload image for %s", item_id, exc_info=True
            )
            return None


UPSERT_SQL = """
INSERT INTO catalog_items
    (item_id, domain, title, price, image_url, product_url, source, content_hash, attributes)
VALUES
    (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (item_id) DO UPDATE SET
    last_seen     = NOW(),
    hit_count     = catalog_items.hit_count + 1,
    price         = EXCLUDED.price,
    image_url     = COALESCE(EXCLUDED.image_url, catalog_items.image_url),
    content_hash  = EXCLUDED.content_hash
RETURNING item_id, (xmax = 0) AS inserted
"""


def store_bronze_json(
    results: list[dict],
    brand: str,
    s3_client,
    bucket: str,
) -> None:
    """Store raw Serper results to the S3 bronze layer for audit."""
    now = datetime.now(tz=timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H-%M-%S")
    slug = _slugify(brand)
    key = f"raw/catalog/serper/dt={date_str}/brand={slug}/{time_str}.json"
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(results),
        ContentType="application/json",
    )
    logger.info("Stored %d raw results to s3://%s/%s", len(results), bucket, key)


def bulk_upsert(
    conn,
    items: list[CatalogItemCreate],
    dry_run: bool,
) -> tuple[int, int]:
    """
    Upsert a batch of CatalogItemCreate objects into catalog_items.

    All values are passed via %s parameterized placeholders — no API data is
    ever interpolated directly into the SQL string.

    Returns:
        (inserted_count, updated_count)
    """
    if dry_run or not items:
        return 0, 0

    inserted = updated = 0
    with conn.cursor() as cur:
        for item in items:
            cur.execute(
                UPSERT_SQL,
                (
                    item.item_id,
                    item.domain,
                    item.title,
                    item.price,
                    item.image_url,
                    item.product_url,
                    item.source,
                    item.content_hash,
                    json.dumps(item.attributes),
                ),
            )
            row = cur.fetchone()
            if row and row[1]:
                inserted += 1
            else:
                updated += 1
    conn.commit()
    return inserted, updated
