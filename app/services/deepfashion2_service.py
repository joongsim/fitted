"""DeepFashion2 annotation parser and category normalizer."""

import json
import logging
import pathlib
from typing import Optional

from app.models.catalog_item import CatalogItemCreate

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

    return CatalogItemCreate(
        item_id=item_id,
        domain="fashion",
        title=title,
        price=0.0,
        image_url="",  # filled in after S3 upload
        product_url="",
        source="deepfashion2",
        content_hash=None,
        attributes={
            "category": app_category,
            "source_category": raw_category,
        },
    )
