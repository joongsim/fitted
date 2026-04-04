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

def parse_result(result: dict, brand: str): ...


async def search_shopping(query: str, api_key: str) -> list: ...


async def download_image(url, item_id, s3_client, bucket, sem): ...


def store_bronze_json(results, brand, s3_client, bucket): ...


def bulk_upsert(conn, items, dry_run=False): ...


UPSERT_SQL = ""
