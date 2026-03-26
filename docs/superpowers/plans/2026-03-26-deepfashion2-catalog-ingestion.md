# DeepFashion2 Catalog Ingestion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest DeepFashion2 fashion images as dev catalog items, fixing the category filter mismatch (issue #70) and replacing the Poshmark/RapidAPI dependency for dev.

**Architecture:** New service module (`deepfashion2_service.py`) handles annotation parsing and category normalization; new ingestion script walks the dataset directory, uploads images to S3, and upserts into `catalog_items`. Follows the existing `poshmark_service.py` + `ingest_poshmark_dev_catalog.py` pattern exactly. Only "shop" source images are ingested (clean single-item product shots). One catalog entry per image using `item1`'s category.

**Tech Stack:** Python 3.11+, psycopg3, boto3, Pydantic v2, pytest

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `app/services/deepfashion2_service.py` | Category map, annotation parsing, `CatalogItemCreate` construction |
| Create | `scripts/ingest_deepfashion2_catalog.py` | CLI entrypoint, S3 upload, DB upsert loop |
| Create | `tests/test_deepfashion2_service.py` | Unit tests for normalization + parsing |

---

## Dataset context

DeepFashion2 directory layout after extraction:

```
DeepFashion2/
  train/
    image/   # 000001.jpg, 000002.jpg, ...
    annos/   # 000001.json, 000002.json, ...
  validation/
    image/
    annos/
```

Each annotation JSON:

```json
{
  "source": "shop",
  "pair_id": 12345,
  "item1": {
    "category_name": "short sleeve top",
    "category_id": 1,
    "bounding_box": [156, 0, 508, 518],
    "style": 0
  }
}
```

`source` is either `"shop"` (clean product shot — ingest these) or `"user"` (outfit photo — skip these). Only `item1` is used; multi-item images are treated as single-category using the first item.

---

## Task 1: Write failing tests for `deepfashion2_service.py`

**Files:**
- Create: `tests/test_deepfashion2_service.py`

- [ ] **Step 1: Create the test file**

```python
"""Unit tests for deepfashion2_service.py."""

import json
import pathlib

import pytest

import app.services.deepfashion2_service as df2


# ---------------------------------------------------------------------------
# normalize_category
# ---------------------------------------------------------------------------


def test_normalize_category_all_known():
    known = [
        ("short sleeve top", "tops"),
        ("long sleeve top", "tops"),
        ("vest", "tops"),
        ("sling", "tops"),
        ("short sleeve dress", "tops"),
        ("long sleeve dress", "tops"),
        ("vest dress", "tops"),
        ("sling dress", "tops"),
        ("shorts", "bottoms"),
        ("trousers", "bottoms"),
        ("skirt", "bottoms"),
        ("short sleeve outwear", "outerwear"),
        ("long sleeve outwear", "outerwear"),
    ]
    for raw, expected in known:
        assert df2.normalize_category(raw) == expected, f"failed for {raw!r}"


def test_normalize_category_unknown_returns_none():
    assert df2.normalize_category("wizard robe") is None


def test_normalize_category_case_insensitive():
    assert df2.normalize_category("Short Sleeve Top") == "tops"
    assert df2.normalize_category("TROUSERS") == "bottoms"


# ---------------------------------------------------------------------------
# parse_annotation
# ---------------------------------------------------------------------------


def _write_anno(tmp_path: pathlib.Path, source: str, category_name: str) -> tuple:
    """Write a minimal annotation JSON and a dummy image file; return (anno, image) paths."""
    anno_path = tmp_path / "000001.json"
    image_path = tmp_path / "000001.jpg"

    anno_path.write_text(json.dumps({
        "source": source,
        "pair_id": 1,
        "item1": {
            "category_name": category_name,
            "category_id": 1,
            "bounding_box": [0, 0, 100, 100],
            "style": 0,
        },
    }))
    image_path.write_bytes(b"\xff\xd8\xff")  # minimal JPEG magic bytes
    return anno_path, image_path


def test_parse_annotation_shop_source_returns_item(tmp_path):
    anno, image = _write_anno(tmp_path, "shop", "short sleeve top")
    item = df2.parse_annotation(anno, image, split="train")
    assert item is not None
    assert item.item_id == "df2_train_000001"
    assert item.title == "Short Sleeve Top"
    assert item.source == "deepfashion2"
    assert item.domain == "fashion"
    assert item.price == 0.0
    assert item.product_url == ""
    assert item.attributes["category"] == "tops"
    assert item.attributes["source_category"] == "short sleeve top"


def test_parse_annotation_user_source_returns_none(tmp_path):
    anno, image = _write_anno(tmp_path, "user", "short sleeve top")
    assert df2.parse_annotation(anno, image, split="train") is None


def test_parse_annotation_missing_image_returns_none(tmp_path):
    anno, _ = _write_anno(tmp_path, "shop", "short sleeve top")
    missing = tmp_path / "missing.jpg"
    assert df2.parse_annotation(anno, missing, split="train") is None


def test_parse_annotation_unknown_category_returns_none(tmp_path):
    anno, image = _write_anno(tmp_path, "shop", "wizard robe")
    assert df2.parse_annotation(anno, image, split="train") is None


def test_parse_annotation_item_id_uses_split_and_stem(tmp_path):
    anno, image = _write_anno(tmp_path, "shop", "trousers")
    item = df2.parse_annotation(anno, image, split="validation")
    assert item.item_id == "df2_validation_000001"


def test_parse_annotation_content_hash_is_deterministic(tmp_path):
    anno, image = _write_anno(tmp_path, "shop", "trousers")
    item1 = df2.parse_annotation(anno, image, split="train")
    item2 = df2.parse_annotation(anno, image, split="train")
    assert item1.content_hash == item2.content_hash
    assert item1.content_hash is not None
```

- [ ] **Step 2: Run tests to confirm they all fail**

```bash
PYTHONPATH=. pytest tests/test_deepfashion2_service.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.services.deepfashion2_service'`

---

## Task 2: Implement `app/services/deepfashion2_service.py`

**Files:**
- Create: `app/services/deepfashion2_service.py`

- [ ] **Step 1: Write the module**

```python
"""DeepFashion2 annotation parser and category normalizer."""

import json
import logging
import pathlib
from typing import Optional

from app.models.catalog_item import CatalogItemCreate, make_content_hash

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Category normalization
# ---------------------------------------------------------------------------

CATEGORY_MAP: dict[str, str] = {
    "short sleeve top": "tops",
    "long sleeve top": "tops",
    "vest": "tops",
    "sling": "tops",
    "short sleeve dress": "tops",
    "long sleeve dress": "tops",
    "vest dress": "tops",
    "sling dress": "tops",
    "shorts": "bottoms",
    "trousers": "bottoms",
    "skirt": "bottoms",
    "short sleeve outwear": "outerwear",
    "long sleeve outwear": "outerwear",
}


def normalize_category(raw: str) -> Optional[str]:
    """Map a DeepFashion2 category name to the app filter taxonomy.

    Returns None for unknown categories (caller should skip the item).
    """
    return CATEGORY_MAP.get(raw.strip().lower())


# ---------------------------------------------------------------------------
# Annotation parsing
# ---------------------------------------------------------------------------


def parse_annotation(
    anno_path: pathlib.Path,
    image_path: pathlib.Path,
    split: str,
) -> Optional[CatalogItemCreate]:
    """Parse a DeepFashion2 annotation JSON into a CatalogItemCreate.

    Only "shop" source images are ingested — these are clean single-item
    product shots. "user" source images are outfit photos and are skipped.

    Args:
        anno_path: Path to the annotation JSON file.
        image_path: Path to the corresponding image file.
        split: Dataset split name ("train" or "validation") — used in item_id.

    Returns:
        CatalogItemCreate ready for DB upsert, or None if the item should
        be skipped (user source, missing image, unknown category, parse error).
    """
    if not image_path.exists():
        logger.debug("Image not found, skipping: %s", image_path)
        return None

    try:
        with open(anno_path) as f:
            data = json.load(f)
    except Exception:
        logger.warning("Failed to parse annotation: %s", anno_path, exc_info=True)
        return None

    if data.get("source") != "shop":
        return None

    item1 = data.get("item1")
    if not item1:
        logger.debug("No item1 in annotation: %s", anno_path)
        return None

    raw_category = item1.get("category_name", "")
    app_category = normalize_category(raw_category)
    if app_category is None:
        logger.debug("Unknown category %r in %s — skipping", raw_category, anno_path)
        return None

    stem = anno_path.stem  # e.g. "000001"
    item_id = f"df2_{split}_{stem}"
    title = raw_category.title()  # "Short Sleeve Top"
    content_hash = make_content_hash(title, 0.0, "", app_category)

    return CatalogItemCreate(
        item_id=item_id,
        domain="fashion",
        title=title,
        price=0.0,
        image_url="",  # filled in after S3 upload
        product_url="",
        source="deepfashion2",
        content_hash=content_hash,
        attributes={
            "category": app_category,
            "source_category": raw_category,
        },
    )
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
PYTHONPATH=. pytest tests/test_deepfashion2_service.py -v
```

Expected: all 8 tests PASS

- [ ] **Step 3: Commit**

```bash
git add app/services/deepfashion2_service.py tests/test_deepfashion2_service.py
git commit -m "feat: add DeepFashion2 annotation parser and category normalizer"
```

---

## Task 3: Implement `scripts/ingest_deepfashion2_catalog.py`

**Files:**
- Create: `scripts/ingest_deepfashion2_catalog.py`

- [ ] **Step 1: Write the ingestion script**

```python
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
```

- [ ] **Step 2: Run the full test suite to verify nothing is broken**

```bash
PYTHONPATH=. pytest tests/ -v --tb=short
```

Expected: all existing tests still pass, plus the 8 new tests from Task 2.

- [ ] **Step 3: Smoke-test the script in dry-run mode**

Download or locate a small sample of DeepFashion2 annotations (at least 5 files), then:

```bash
PYTHONPATH=. python scripts/ingest_deepfashion2_catalog.py \
    --anno-dir /path/to/DeepFashion2/train/annos \
    --image-dir /path/to/DeepFashion2/train/image \
    --dry-run --max-items 20
```

Expected output:

```
=== DRY RUN MODE — no S3 writes or DB upserts ===
Found N annotation files in /path/to/annos
=== Ingestion complete ===
  Parsed (ingested): X
  Skipped (user/unknown): Y
  ...
```

- [ ] **Step 4: Commit**

```bash
git add scripts/ingest_deepfashion2_catalog.py
git commit -m "feat: add DeepFashion2 catalog ingestion script"
```

---

## Task 4: Run full ingestion and verify category filters

This task runs against real infrastructure (S3 + RDS). Open an SSH tunnel to RDS first.

- [ ] **Step 1: Run ingestion for train split**

```bash
PYTHONPATH=. DATABASE_URL=postgresql://...@localhost:5432/fitted \
    WEATHER_BUCKET_NAME=your-bucket \
    python scripts/ingest_deepfashion2_catalog.py \
    --anno-dir /path/to/DeepFashion2/train/annos \
    --image-dir /path/to/DeepFashion2/train/image \
    --split train
```

- [ ] **Step 2: Run embedding backfill**

```bash
PYTHONPATH=. DATABASE_URL=postgresql://...@localhost:5432/fitted \
    python scripts/backfill_catalog_embeddings.py --batch-size 100
```

- [ ] **Step 3: Verify category distribution in DB**

```sql
SELECT attributes->>'category' AS category, COUNT(*) AS count
FROM catalog_items
WHERE source = 'deepfashion2'
GROUP BY 1
ORDER BY 2 DESC;
```

Expected: rows for `tops`, `bottoms`, `outerwear` with non-zero counts. `shoes` and `accessories` absent (expected).

- [ ] **Step 4: Verify category filters in the app**

Start the backend and frontend locally, navigate to the recommendations page, click each filter. `Tops`, `Bottoms`, `Outerwear` should return results. `Shoes` and `Accessories` should return empty (expected until Phase 2).

---

## Follow-up (out of scope for this plan)

Run `scripts/pretrain_item_tower.py` pointing at the DeepFashion2 image directory for better item tower embeddings. This is a separate training workflow tracked in issue #71.

---

## Post-ingestion checklist

- [ ] `source = 'deepfashion2'` rows visible in `catalog_items`
- [ ] `attributes->>'category'` values are lowercase and match `_CATEGORIES` in `frontend/app.py`
- [ ] Issue #70 (category filters broken) resolved
- [ ] Issue #71 updated with Phase 1 complete
