"""Unit tests for scripts/ingest_serper_catalog.py."""

import hashlib
import json
import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from scripts.ingest_serper_catalog import (
    _slugify,
    bulk_upsert,
    download_image,
    make_item_id,
    parse_price,
    parse_result,
    store_bronze_json,
)
from app.models.catalog_item import CatalogItemCreate


# ---------------------------------------------------------------------------
# parse_price
# ---------------------------------------------------------------------------


class TestParsePrice:
    def test_usd_simple(self):
        assert parse_price("$295.00") == 295.00

    def test_usd_with_comma(self):
        assert parse_price("$1,299.00") == 1299.00

    def test_usd_whole_number(self):
        assert parse_price("$85") == 85.0

    def test_non_usd_returns_none(self):
        assert parse_price("£200") is None

    def test_non_usd_eur_returns_none(self):
        assert parse_price("€150.00") is None

    def test_empty_string_returns_none(self):
        assert parse_price("") is None

    def test_non_numeric_returns_none(self):
        assert parse_price("$abc") is None

    def test_none_returns_none(self):
        assert parse_price(None) is None


# ---------------------------------------------------------------------------
# make_item_id
# ---------------------------------------------------------------------------


class TestMakeItemId:
    def test_returns_serper_prefix(self):
        item_id = make_item_id("https://example.com/product/123")
        assert item_id.startswith("serper_")

    def test_hash_length(self):
        item_id = make_item_id("https://example.com/product/123")
        assert len(item_id) == len("serper_") + 12

    def test_deterministic(self):
        url = "https://example.com/product/123"
        assert make_item_id(url) == make_item_id(url)

    def test_different_urls_different_ids(self):
        assert make_item_id("https://example.com/a") != make_item_id("https://example.com/b")

    def test_hash_matches_sha256(self):
        url = "https://example.com/product/123"
        expected = "serper_" + hashlib.sha256(url.encode()).hexdigest()[:12]
        assert make_item_id(url) == expected


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_lowercase_and_hyphens(self):
        assert _slugify("Acne Studios") == "acne-studios"

    def test_special_chars_removed(self):
        assert _slugify("A.P.C.") == "a-p-c"

    def test_truncated_to_80(self):
        long_name = "a" * 100
        assert len(_slugify(long_name)) <= 80
