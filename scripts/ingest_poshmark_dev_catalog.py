"""
Poshmark dev catalog ingestion script.

Fetches clothing listings from the Poshmark RapidAPI across a curated set of
men's fashion queries, stores raw JSON to S3 (bronze layer), downloads cover
images to S3, and bulk-upserts into the catalog_items table.

This is a one-time dev operation, not a Lambda function. Run on EC2 or a
developer machine with AWS credentials configured.

Usage:
    # Dry run — no S3 writes or DB upserts
    PYTHONPATH=. python scripts/ingest_poshmark_dev_catalog.py --dry-run --max-listings 50

    # Full run
    PYTHONPATH=. python scripts/ingest_poshmark_dev_catalog.py

    # Resume after interruption
    PYTHONPATH=. python scripts/ingest_poshmark_dev_catalog.py --resume

    # On EC2 (background):
    nohup PYTHONPATH=. python scripts/ingest_poshmark_dev_catalog.py > ingest.log 2>&1 &

Environment (set in .env or SSM):
    RAPIDAPI_KEY          RapidAPI key for Poshmark API access
    DATABASE_URL          PostgreSQL connection URL
    WEATHER_BUCKET_NAME   S3 bucket for bronze JSON and images
    USE_SSM=false         Use environment variables instead of SSM (local dev)

Security notes:
    - RAPIDAPI_KEY must NOT be committed to git or logged
    - Images are only downloaded from approved Poshmark CDN hostnames (SSRF guard)
    - All DB values are parameterized — no f-string interpolation of API data
"""

import argparse
import asyncio
import json
import logging
import os
import pathlib
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import boto3
import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from app.core.config import config
from app.services.poshmark_service import (
    download_image,
    parse_listing,
    search_listings,
    store_bronze_json,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("ingest_poshmark")

# ---------------------------------------------------------------------------
# Query list — men's fashion, 5 categories, 25 queries
# ---------------------------------------------------------------------------

QUERY_LIST = [
    # Men's — Tops
    {"query": "oxford shirt men dress", "department": "Men", "category": "Tops"},
    {"query": "henley shirt men casual", "department": "Men", "category": "Tops"},
    {"query": "polo shirt men classic", "department": "Men", "category": "Tops"},
    {"query": "graphic tee men streetwear", "department": "Men", "category": "Tops"},
    {"query": "linen shirt men summer", "department": "Men", "category": "Tops"},
    {"query": "flannel shirt men", "department": "Men", "category": "Tops"},
    {"query": "cashmere sweater men luxury", "department": "Men", "category": "Tops"},
    {"query": "crewneck sweatshirt men", "department": "Men", "category": "Tops"},
    # Men's — Bottoms
    {"query": "slim fit chinos men navy", "department": "Men", "category": "Bottoms"},
    {"query": "raw denim jeans men", "department": "Men", "category": "Bottoms"},
    {"query": "straight leg trousers men", "department": "Men", "category": "Bottoms"},
    {"query": "cargo pants men", "department": "Men", "category": "Bottoms"},
    {"query": "jogger pants men", "department": "Men", "category": "Bottoms"},
    # Men's — Outerwear
    {
        "query": "bomber jacket men varsity",
        "department": "Men",
        "category": "Jackets & Coats",
    },
    {"query": "blazer men suit", "department": "Men", "category": "Jackets & Coats"},
    {"query": "trench coat men", "department": "Men", "category": "Jackets & Coats"},
    {"query": "puffer jacket men", "department": "Men", "category": "Jackets & Coats"},
    {"query": "denim jacket men", "department": "Men", "category": "Jackets & Coats"},
    # Men's — Shoes
    {"query": "chelsea boots men leather", "department": "Men", "category": "Shoes"},
    {"query": "white sneakers men clean", "department": "Men", "category": "Shoes"},
    {"query": "loafers men dress", "department": "Men", "category": "Shoes"},
    {"query": "running shoes men", "department": "Men", "category": "Shoes"},
    {"query": "boots men work", "department": "Men", "category": "Shoes"},
    # Men's — Bags & Accessories
    {"query": "backpack men leather", "department": "Men", "category": "Bags"},
    {"query": "tote bag men canvas", "department": "Men", "category": "Bags"},
]

# ---------------------------------------------------------------------------
# Upsert SQL
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------


def load_checkpoint(path: pathlib.Path) -> dict:
    """Load the ingestion checkpoint file, or return a fresh state."""
    if path.exists():
        try:
            with open(path) as f:
                data = json.load(f)
            logger.info(
                "Loaded checkpoint from %s (%d completed pages)",
                path,
                len(data.get("completed_pages", [])),
            )
            return data
        except Exception:
            logger.warning("Could not read checkpoint file %s — starting fresh", path)
    return {"completed_pages": [], "total_inserted": 0, "total_updated": 0}


def save_checkpoint(path: pathlib.Path, state: dict) -> None:
    """Persist the checkpoint state to disk atomically."""
    state["last_updated"] = datetime.now(tz=timezone.utc).isoformat()
    try:
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(state, f)
        tmp.rename(path)
    except Exception:
        logger.warning("Failed to save checkpoint to %s", path, exc_info=True)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def bulk_upsert(
    conn: psycopg.Connection,
    items: list,
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
            if row and row[1]:  # inserted flag (xmax = 0)
                inserted += 1
            else:
                updated += 1
    conn.commit()
    return inserted, updated


# ---------------------------------------------------------------------------
# Main ingestion coroutine
# ---------------------------------------------------------------------------


async def ingest(args: argparse.Namespace) -> None:
    """Main ingestion loop."""
    # Validate and resolve checkpoint path (prevent path traversal)
    checkpoint_path = pathlib.Path(args.checkpoint).resolve()
    cwd = pathlib.Path.cwd().resolve()
    if not str(checkpoint_path).startswith(str(cwd)):
        raise ValueError(
            f"Checkpoint path '{checkpoint_path}' is outside the working directory. "
            "Use a relative or absolute path within the project."
        )

    state = (
        load_checkpoint(checkpoint_path)
        if args.resume
        else {
            "completed_pages": [],
            "total_inserted": 0,
            "total_updated": 0,
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
        }
    )
    completed_set = {tuple(p) for p in state.get("completed_pages", [])}

    # Load configuration
    api_key = config.rapidapi_key
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
    conn: Optional[psycopg.Connection] = None
    if not args.dry_run:
        logger.info("Connecting to database...")
        conn = psycopg.connect(database_url)

    # Semaphore limits concurrent image downloads to 10
    image_sem = asyncio.Semaphore(10)

    total_fetched = 0
    total_filtered = 0
    total_inserted = state.get("total_inserted", 0)
    total_updated = state.get("total_updated", 0)
    total_failed_images = 0

    try:
        for query_spec in QUERY_LIST:
            query = query_spec["query"]
            department = query_spec.get("department")
            category = query_spec.get("category")

            logger.info(
                "=== Query: %r (dept=%s cat=%s) ===", query, department, category
            )

            for page in range(1, args.max_pages_per_query + 1):
                page_key = (query, page)
                if page_key in completed_set:
                    logger.debug("Skipping already-completed page: %s p%d", query, page)
                    continue

                # Rate limit: 0.5s between API calls
                await asyncio.sleep(0.5)

                try:
                    raw_listings = await search_listings(
                        query,
                        api_key,
                        category=category,
                        department=department,
                        page=page,
                    )
                except Exception:
                    logger.error(
                        "API error for query=%r page=%d — skipping page",
                        query,
                        page,
                        exc_info=True,
                    )
                    break

                if not raw_listings:
                    logger.info(
                        "No results for %r page %d — end of results", query, page
                    )
                    break

                total_fetched += len(raw_listings)
                logger.info(
                    "Fetched %d listings for %r page %d",
                    len(raw_listings),
                    query,
                    page,
                )

                # Store raw JSON to S3 bronze layer
                if not args.dry_run and s3_client and bucket:
                    store_bronze_json(
                        [r.model_dump() for r in raw_listings],
                        query,
                        s3_client,
                        bucket,
                    )

                # Parse and filter listings
                parsed_items = []
                for raw in raw_listings:
                    item = parse_listing(raw, query_context=query)
                    if item is None:
                        total_filtered += 1
                        continue
                    parsed_items.append((raw, item))

                logger.info(
                    "Parsed %d quality listings from %d raw (filtered %d)",
                    len(parsed_items),
                    len(raw_listings),
                    len(raw_listings) - len(parsed_items),
                )

                if args.dry_run:
                    logger.info("[DRY RUN] Would upsert %d items", len(parsed_items))
                    completed_set.add(page_key)
                    state["completed_pages"].append(list(page_key))
                    continue

                # Download images in parallel (bounded by semaphore)
                image_tasks = []
                for raw, item in parsed_items:
                    cover_url = (
                        raw.cover_shot.url_small
                        if raw.cover_shot and raw.cover_shot.url_small
                        else None
                    )
                    if cover_url:
                        image_tasks.append(
                            download_image(
                                url=cover_url,
                                item_id=item.item_id,
                                s3_client=s3_client,
                                bucket=bucket,
                                sem=image_sem,
                            )
                        )
                    else:
                        image_tasks.append(asyncio.coroutine(lambda: None)())

                image_urls = await asyncio.gather(*image_tasks, return_exceptions=False)

                # Attach S3 image URLs and upsert
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
                    "Upserted %d items (inserted=%d updated=%d) — running total: %d",
                    len(upsert_batch),
                    inserted,
                    updated,
                    total_inserted + total_updated,
                )

                # Checkpoint this page
                completed_set.add(page_key)
                state["completed_pages"].append(list(page_key))
                state["total_inserted"] = total_inserted
                state["total_updated"] = total_updated
                save_checkpoint(checkpoint_path, state)

                # Stop if we've hit the max-listings cap
                if (
                    args.max_listings
                    and (total_inserted + total_updated) >= args.max_listings
                ):
                    logger.info(
                        "Reached --max-listings=%d — stopping", args.max_listings
                    )
                    return

    finally:
        if conn:
            conn.close()

    logger.info(
        "\n=== Ingestion complete ===\n"
        "  Fetched:         %d raw listings\n"
        "  Filtered:        %d (quality filter)\n"
        "  Inserted (new):  %d\n"
        "  Updated (dupe):  %d\n"
        "  Failed images:   %d",
        total_fetched,
        total_filtered,
        total_inserted,
        total_updated,
        total_failed_images,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest Poshmark men's listings into the dev catalog."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and log listings without writing to S3 or the database.",
    )
    parser.add_argument(
        "--max-listings",
        type=int,
        default=0,
        metavar="N",
        help="Stop after N total listings upserted (0 = unlimited).",
    )
    parser.add_argument(
        "--max-pages-per-query",
        type=int,
        default=15,
        metavar="N",
        help="Maximum pages to fetch per query (default: 15). Hard cap to prevent RapidAPI quota exhaustion.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from the checkpoint file, skipping already-completed pages.",
    )
    parser.add_argument(
        "--checkpoint",
        default="./poshmark_ingest_checkpoint.json",
        metavar="PATH",
        help="Path to the checkpoint file (default: ./poshmark_ingest_checkpoint.json).",
    )
    args = parser.parse_args()

    if args.dry_run:
        logger.info("=== DRY RUN MODE — no S3 writes or DB upserts ===")

    asyncio.run(ingest(args))


if __name__ == "__main__":
    main()
