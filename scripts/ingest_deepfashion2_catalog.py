"""
DeepFashion2 dev catalog ingestion script.

Walks the DeepFashion2 annotation directory, parses each annotation,
uploads the corresponding image to S3, and bulk-upserts into catalog_items.

Only "shop" source images are ingested. Run backfill_catalog_embeddings.py
after this script to generate CLIP embeddings.

Usage:
    # Dry run — no S3 writes or DB upserts
    PYTHONPATH=. python scripts/ingest_deepfashion2_catalog.py \\
        --anno-dir /data/DeepFashion2/train/annos \\
        --image-dir /data/DeepFashion2/train/image \\
        --dry-run --max-items 100

    # Full run (train split)
    PYTHONPATH=. python scripts/ingest_deepfashion2_catalog.py \\
        --anno-dir /data/DeepFashion2/train/annos \\
        --image-dir /data/DeepFashion2/train/image \\
        --split train

    # Validation split
    PYTHONPATH=. python scripts/ingest_deepfashion2_catalog.py \\
        --anno-dir /data/DeepFashion2/validation/annos \\
        --image-dir /data/DeepFashion2/validation/image \\
        --split validation

Environment:
    DATABASE_URL          PostgreSQL connection URL
    WEATHER_BUCKET_NAME   S3 bucket for images
    USE_SSM=false         Use env vars instead of SSM (local dev)
"""

import argparse
import json
import logging
import os
import pathlib
import sys

import boto3
import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from app.core.config import config
from app.services.deepfashion2_service import parse_annotation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("ingest_deepfashion2")

MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB

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


def upload_image(
    image_path: pathlib.Path,
    item_id: str,
    s3_client,
    bucket: str,
) -> str | None:
    """Upload a local image file to S3. Returns S3 URL or None on failure."""
    s3_key = f"images/catalog/deepfashion2/{item_id}.jpg"

    try:
        s3_client.head_object(Bucket=bucket, Key=s3_key)
        logger.debug("Image already in S3, skipping: %s", item_id)
        return f"s3://{bucket}/{s3_key}"
    except Exception:
        pass  # key does not exist — proceed

    try:
        image_bytes = image_path.read_bytes()
        if len(image_bytes) > MAX_IMAGE_BYTES:
            logger.warning("Image exceeds 5MB, skipping item_id=%s", item_id)
            return None
        s3_client.put_object(
            Bucket=bucket,
            Key=s3_key,
            Body=image_bytes,
            ContentType="image/jpeg",
        )
        return f"s3://{bucket}/{s3_key}"
    except Exception:
        logger.warning(
            "Failed to upload image for item_id=%s", item_id, exc_info=True
        )
        return None


def bulk_upsert(
    conn: psycopg.Connection,
    items: list,
    dry_run: bool,
) -> tuple[int, int]:
    """Upsert a batch of CatalogItemCreate objects. Returns (inserted, updated)."""
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


def ingest(args: argparse.Namespace) -> None:
    """Main ingestion loop."""
    anno_dir = pathlib.Path(args.anno_dir)
    image_dir = pathlib.Path(args.image_dir)

    if not anno_dir.is_dir():
        logger.error("--anno-dir does not exist: %s", anno_dir)
        sys.exit(1)
    if not image_dir.is_dir():
        logger.error("--image-dir does not exist: %s", image_dir)
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

    s3_client = None
    if not args.dry_run:
        region = os.environ.get("AWS_DEFAULT_REGION", "us-west-1")
        s3_client = boto3.client("s3", region_name=region)

    conn = None
    if not args.dry_run:
        logger.info("Connecting to database...")
        conn = psycopg.connect(database_url)

    anno_paths = sorted(anno_dir.glob("*.json"))
    logger.info("Found %d annotation files in %s", len(anno_paths), anno_dir)

    total_parsed = 0
    total_skipped = 0
    total_inserted = 0
    total_updated = 0
    total_failed_images = 0
    batch: list = []

    try:
        for anno_path in anno_paths:
            if args.max_items and (total_parsed + total_skipped) >= args.max_items:
                break

            stem = anno_path.stem
            image_path = image_dir / f"{stem}.jpg"

            item = parse_annotation(anno_path, image_path, split=args.split)
            if item is None:
                total_skipped += 1
                continue

            total_parsed += 1

            if args.dry_run:
                logger.debug("[DRY RUN] Would ingest: %s", item.item_id)
                continue

            s3_url = upload_image(image_path, item.item_id, s3_client, bucket)
            if s3_url:
                item = item.model_copy(update={"image_url": s3_url})
            else:
                total_failed_images += 1

            batch.append(item)

            if len(batch) >= args.batch_size:
                ins, upd = bulk_upsert(conn, batch, dry_run=False)
                total_inserted += ins
                total_updated += upd
                logger.info(
                    "Upserted batch: inserted=%d updated=%d running_total=%d",
                    ins,
                    upd,
                    total_inserted + total_updated,
                )
                batch = []

        # Flush remaining batch
        if batch:
            ins, upd = bulk_upsert(conn, batch, dry_run=False)
            total_inserted += ins
            total_updated += upd

    finally:
        if conn:
            conn.close()

    logger.info(
        "\n=== Ingestion complete ===\n"
        "  Parsed (ingested): %d\n"
        "  Skipped (user/unknown): %d\n"
        "  Inserted (new):    %d\n"
        "  Updated (dupe):    %d\n"
        "  Failed images:     %d",
        total_parsed,
        total_skipped,
        total_inserted,
        total_updated,
        total_failed_images,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest DeepFashion2 shop images into the dev catalog."
    )
    parser.add_argument(
        "--anno-dir",
        required=True,
        metavar="PATH",
        help="Path to DeepFashion2 annos/ directory.",
    )
    parser.add_argument(
        "--image-dir",
        required=True,
        metavar="PATH",
        help="Path to DeepFashion2 image/ directory.",
    )
    parser.add_argument(
        "--split",
        default="train",
        choices=["train", "validation"],
        help="Dataset split name, used in item_id (default: train).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and count items without writing to S3 or the database.",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=0,
        metavar="N",
        help="Stop after processing N annotation files (0 = unlimited).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        metavar="N",
        help="DB commit batch size (default: 100).",
    )
    args = parser.parse_args()

    if args.dry_run:
        logger.info("=== DRY RUN MODE — no S3 writes or DB upserts ===")

    ingest(args)


if __name__ == "__main__":
    main()
