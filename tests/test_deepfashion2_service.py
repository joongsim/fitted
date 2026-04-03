"""Unit tests for deepfashion2_service.py."""

import json
import pathlib

import pytest

import app.services.deepfashion2_service as df2


# ---------------------------------------------------------------------------
# normalize_category
# ---------------------------------------------------------------------------


def test_normalize_category_all_known():
    for raw, expected in df2.CATEGORY_MAP.items():
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


def test_parse_annotation_content_hash_is_none(tmp_path):
    anno, image = _write_anno(tmp_path, "shop", "trousers")
    item = df2.parse_annotation(anno, image, split="train")
    assert item.content_hash is None
