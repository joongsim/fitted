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


# ---------------------------------------------------------------------------
# parse_result
# ---------------------------------------------------------------------------

MOCK_SERPER_RESULT = {
    "title": "Acne Studios Face Logo T-Shirt",
    "price": "$195.00",
    "imageUrl": "https://cdn.example.com/image.jpg",
    "link": "https://www.mrporter.com/en-us/mens/product/acne/12345",
    "source": "Mr Porter",
}


class TestParseResult:
    def test_valid_result_returns_catalog_item(self):
        item = parse_result(MOCK_SERPER_RESULT, brand="Acne Studios")
        assert isinstance(item, CatalogItemCreate)
        assert item.source == "serper"
        assert item.domain == "fashion"

    def test_item_id_matches_link_hash(self):
        item = parse_result(MOCK_SERPER_RESULT, brand="Acne Studios")
        assert item.item_id == make_item_id(MOCK_SERPER_RESULT["link"])

    def test_price_parsed_correctly(self):
        item = parse_result(MOCK_SERPER_RESULT, brand="Acne Studios")
        assert item.price == 195.00

    def test_title_set(self):
        item = parse_result(MOCK_SERPER_RESULT, brand="Acne Studios")
        assert item.title == "Acne Studios Face Logo T-Shirt"

    def test_brand_from_config_in_attributes(self):
        item = parse_result(MOCK_SERPER_RESULT, brand="Acne Studios")
        assert item.attributes["brand"] == "Acne Studios"

    def test_category_menswear_in_attributes(self):
        item = parse_result(MOCK_SERPER_RESULT, brand="Acne Studios")
        assert item.attributes["category"] == "menswear"

    def test_image_url_is_none_initially(self):
        item = parse_result(MOCK_SERPER_RESULT, brand="Acne Studios")
        assert item.image_url is None

    def test_product_url_set(self):
        item = parse_result(MOCK_SERPER_RESULT, brand="Acne Studios")
        assert item.product_url == MOCK_SERPER_RESULT["link"]

    def test_content_hash_set(self):
        item = parse_result(MOCK_SERPER_RESULT, brand="Acne Studios")
        assert item.content_hash is not None
        assert len(item.content_hash) == 64

    def test_missing_link_returns_none(self):
        result = {**MOCK_SERPER_RESULT, "link": ""}
        assert parse_result(result, brand="Acne Studios") is None

    def test_missing_title_returns_none(self):
        result = {**MOCK_SERPER_RESULT, "title": ""}
        assert parse_result(result, brand="Acne Studios") is None

    def test_non_usd_price_returns_none(self):
        result = {**MOCK_SERPER_RESULT, "price": "£195.00"}
        assert parse_result(result, brand="Acne Studios") is None

    def test_missing_price_returns_none(self):
        result = {**MOCK_SERPER_RESULT, "price": None}
        assert parse_result(result, brand="Acne Studios") is None
