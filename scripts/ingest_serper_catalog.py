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


# ---------------------------------------------------------------------------
# Main ingestion coroutine
# ---------------------------------------------------------------------------


async def ingest(args: argparse.Namespace) -> None:
    """Main ingestion loop: iterate over brands, search, download, upsert."""
    # Load brand list
    brands_path = pathlib.Path(args.brands_file).resolve()
    if not brands_path.exists():
        logger.error("Brand list not found: %s", brands_path)
        sys.exit(1)
    with open(brands_path) as f:
        brands: list[str] = json.load(f).get("brands", [])
    if not brands:
        logger.error("Brand list is empty")
        sys.exit(1)
    logger.info("Loaded %d brands from %s", len(brands), brands_path)

    # Load config
    api_key = os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        logger.error("SERPER_API_KEY is not set")
        sys.exit(1)

    database_url = config.database_url
    bucket = os.environ.get("WEATHER_BUCKET_NAME", "")
    if not bucket:
        try:
            bucket = config.weather_bucket_name
        except Exception:
            bucket = ""
    if not bucket and not args.dry_run:
        logger.error("WEATHER_BUCKET_NAME is not set — cannot write to S3")
        sys.exit(1)

    # Initialize S3 client
    s3_client = None
    if not args.dry_run:
        try:
            region = os.environ.get("AWS_DEFAULT_REGION", "us-west-1")
            s3_client = boto3.client("s3", region_name=region)
        except Exception:
            logger.error("Failed to initialize S3 client", exc_info=True)
            sys.exit(1)

    # Open DB connection
    conn = None
    if not args.dry_run:
        logger.info("Connecting to database...")
        import psycopg
        conn = psycopg.connect(database_url)

    image_sem = asyncio.Semaphore(10)

    total_fetched = 0
    total_skipped = 0
    total_inserted = 0
    total_updated = 0
    total_failed_images = 0
    total_dry_run_count = 0

    try:
        for brand in brands:
            query = f"{brand} menswear"
            logger.info("=== Brand: %r | Query: %r ===", brand, query)

            await asyncio.sleep(0.5)  # rate limit: 0.5s between API calls

            try:
                raw_results = await search_shopping(query, api_key)
            except Exception:
                logger.error("API error for brand=%r — skipping", brand, exc_info=True)
                continue

            logger.info("Fetched %d results for %r", len(raw_results), brand)
            total_fetched += len(raw_results)

            # Store raw JSON to S3 bronze layer
            if not args.dry_run and s3_client and bucket:
                store_bronze_json(raw_results, brand, s3_client, bucket)

            # Parse results
            parsed_items = []
            for raw in raw_results:
                item = parse_result(raw, brand=brand)
                if item is None:
                    total_skipped += 1
                    continue
                parsed_items.append((raw, item))

            logger.info(
                "Parsed %d valid items from %d results (skipped %d)",
                len(parsed_items),
                len(raw_results),
                len(raw_results) - len(parsed_items),
            )

            if args.dry_run:
                total_dry_run_count += len(parsed_items)
                logger.info(
                    "[DRY RUN] Would upsert %d items (running total: %d)",
                    len(parsed_items),
                    total_dry_run_count,
                )
                if args.max_items and total_dry_run_count >= args.max_items:
                    logger.info("Reached --max-items=%d — stopping", args.max_items)
                    return
                continue

            # Download images in parallel
            image_tasks = [
                download_image(
                    url=raw.get("imageUrl", ""),
                    item_id=item.item_id,
                    s3_client=s3_client,
                    bucket=bucket,
                    sem=image_sem,
                )
                for raw, item in parsed_items
            ]
            image_urls = await asyncio.gather(*image_tasks)

            # Attach S3 URLs and upsert
            upsert_batch = []
            for (raw, item), s3_url in zip(parsed_items, image_urls):
                if s3_url:
                    item = item.model_copy(update={"image_url": s3_url})
                else:
                    total_failed_images += 1
                upsert_batch.append(item)

            inserted, updated = bulk_upsert(conn, upsert_batch, dry_run=False)
            total_inserted += inserted
            total_updated += updated

            logger.info(
                "Upserted %d items (inserted=%d updated=%d) — total: %d",
                len(upsert_batch),
                inserted,
                updated,
                total_inserted + total_updated,
            )

            if args.max_items and (total_inserted + total_updated) >= args.max_items:
                logger.info("Reached --max-items=%d — stopping", args.max_items)
                return

    finally:
        if conn:
            conn.close()

    logger.info(
        "\n=== Ingestion complete ===\n"
        "  Fetched:         %d raw results\n"
        "  Skipped:         %d (parse failures / non-USD)\n"
        "  Inserted (new):  %d\n"
        "  Updated (dupe):  %d\n"
        "  Failed images:   %d",
        total_fetched,
        total_skipped,
        total_inserted,
        total_updated,
        total_failed_images,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest menswear brands from Google Shopping via Serper API."
    )
    parser.add_argument(
        "--brands-file",
        default="config/serper_brands.json",
        metavar="PATH",
        help="Path to brand list JSON (default: config/serper_brands.json).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and log without writing to S3 or the database.",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=0,
        metavar="N",
        help="Stop after N total items upserted (0 = unlimited).",
    )
    args = parser.parse_args()

    if args.dry_run:
        logger.info("=== DRY RUN MODE — no S3 writes or DB upserts ===")

    asyncio.run(ingest(args))


if __name__ == "__main__":
    main()
