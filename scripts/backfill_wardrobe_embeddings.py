"""
Backfill CLIP image embeddings for wardrobe_items where embedding IS NULL.

Runs on EC2 (CLIP image encoder is ~290MB — too large for Lambda).

    # Open SSH tunnel first, then:
    PYTHONPATH=. DATABASE_URL=postgresql://...@localhost:5432/fitted \\
        python scripts/backfill_wardrobe_embeddings.py

Options:
    --batch-size N   Rows per DB commit (default: 100)
    --dry-run        Log without writing
    --limit N        Stop after N items (0 = unlimited)
"""

import argparse
import logging
import pathlib
import sys

import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from app.core.config import config
from app.services.embedding_service import encode_image

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_wardrobe_embeddings")

logger.info("Backfill CLIP image embeddings for wardrobe_items where embedding IS NULL")


def fetch_unembedded_batch(conn: psycopg.Connection, batch_size: int) -> list[dict]:
    """Return up to batch_size rows where embedding IS NULL and image_s3_key is set."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT item_id, image_s3_key
            FROM wardrobe_items
            WHERE embedding IS NULL AND image_s3_key IS NOT NULL
            LIMIT %s
            """,
            (batch_size,),
        )
        rows = cur.fetchall()

    return [{"item_id": row[0], "image_s3_key": row[1]} for row in rows]


def embed_batch(items: list[dict]) -> list[tuple[str, list[float]]]:
    """
    Encode each item's wardrobe image via CLIP image encoder.

    Returns (item_id, embedding_as_list) pairs.
    """
    results = []
    for item in items:
        vec = encode_image(item["image_s3_key"])
        results.append((item["item_id"], vec.tolist()))
    return results


def write_embeddings(
    conn: psycopg.Connection,
    embeddings: list[tuple[str, list[float]]],
) -> int:
    """
    UPDATE wardrobe_items SET embedding = %s::vector WHERE item_id = %s.

    Returns the number of rows updated.
    """
    with conn.cursor() as cur:
        for item_id, vec in embeddings:
            cur.execute(
                """
                UPDATE wardrobe_items
                SET embedding = %s::vector
                WHERE item_id = %s
                """,
                (vec, item_id),
            )
    conn.commit()
    return len(embeddings)


def run(args: argparse.Namespace) -> None:
    """Main loop: fetch batch → embed → write → repeat."""
    conn = psycopg.connect(config.database_url)
    total_done = 0

    try:
        while True:
            batch = fetch_unembedded_batch(conn, args.batch_size)
            if not batch:
                logger.info("No more unembedded items. Done.")
                break

            if args.dry_run:
                logger.info("[DRY RUN] Would embed %d items", len(batch))
                break

            embeddings = embed_batch(batch)
            written = write_embeddings(conn, embeddings)
            total_done += written
            logger.info("Embedded %d items (total so far: %d)", written, total_done)

            if args.limit and total_done >= args.limit:
                logger.info("Reached --limit=%d — stopping", args.limit)
                break

    finally:
        conn.close()

    logger.info("Backfill complete. Total embedded: %d", total_done)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill CLIP image embeddings for wardrobe_items"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Rows per DB commit (default: 100)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log without writing embeddings",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Stop after N items; 0 = no limit (default: 0)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    run(_parse_args())
