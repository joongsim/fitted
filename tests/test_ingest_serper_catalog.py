"""Unit tests for scripts/ingest_serper_catalog.py."""

import hashlib
import json
import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import asyncio

from scripts.ingest_serper_catalog import (
    _slugify,
    bulk_upsert,
    download_image,
    make_item_id,
    parse_price,
    parse_result,
    search_shopping,
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


# ---------------------------------------------------------------------------
# search_shopping
# ---------------------------------------------------------------------------


class TestSearchShopping:
    async def test_returns_shopping_list(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "shopping": [MOCK_SERPER_RESULT, MOCK_SERPER_RESULT]
        }

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("scripts.ingest_serper_catalog.httpx.AsyncClient", return_value=mock_client):
            results = await search_shopping("Acne Studios menswear", api_key="test-key")

        assert len(results) == 2
        assert results[0]["title"] == MOCK_SERPER_RESULT["title"]

    async def test_sends_correct_query(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"shopping": []}

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("scripts.ingest_serper_catalog.httpx.AsyncClient", return_value=mock_client):
            await search_shopping("Acne Studios menswear", api_key="test-key")

        call_kwargs = mock_client.post.call_args
        assert call_kwargs.kwargs["json"] == {"q": "Acne Studios menswear"}
        assert call_kwargs.kwargs["headers"]["X-API-KEY"] == "test-key"

    async def test_empty_shopping_key_returns_empty_list(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {}

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("scripts.ingest_serper_catalog.httpx.AsyncClient", return_value=mock_client):
            results = await search_shopping("Acne Studios menswear", api_key="test-key")

        assert results == []


# ---------------------------------------------------------------------------
# download_image
# ---------------------------------------------------------------------------

MOCK_S3_BUCKET = "fitted-dev-bucket"


def _make_streaming_response(status_code: int, content_type: str, body: bytes):
    """Create a mock async streaming httpx response context manager."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.headers = {"content-type": content_type}
    mock_response.raise_for_status = MagicMock()

    async def _aiter_bytes(chunk_size=8192):
        yield body

    mock_response.aiter_bytes = _aiter_bytes

    mock_stream_cm = MagicMock()
    mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
    mock_stream_cm.__aexit__ = AsyncMock(return_value=False)

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.stream = MagicMock(return_value=mock_stream_cm)

    return mock_client


class TestDownloadImage:
    async def test_success_returns_s3_url(self):
        image_bytes = b"\xff\xd8\xff" + b"x" * 100  # fake JPEG
        mock_s3 = MagicMock()
        sem = asyncio.Semaphore(10)

        with patch(
            "scripts.ingest_serper_catalog.httpx.AsyncClient",
            return_value=_make_streaming_response(200, "image/jpeg", image_bytes),
        ):
            result = await download_image(
                url="https://cdn.example.com/image.jpg",
                item_id="serper_abc123",
                s3_client=mock_s3,
                bucket=MOCK_S3_BUCKET,
                sem=sem,
            )

        assert result == f"s3://{MOCK_S3_BUCKET}/images/catalog/serper/serper_abc123.jpg"
        mock_s3.put_object.assert_called_once()
        call_kwargs = mock_s3.put_object.call_args.kwargs
        assert call_kwargs["Key"] == "images/catalog/serper/serper_abc123.jpg"

    async def test_non_image_content_type_returns_none(self):
        mock_s3 = MagicMock()
        sem = asyncio.Semaphore(10)

        with patch(
            "scripts.ingest_serper_catalog.httpx.AsyncClient",
            return_value=_make_streaming_response(200, "text/html", b"<html>"),
        ):
            result = await download_image(
                url="https://cdn.example.com/image.jpg",
                item_id="serper_abc123",
                s3_client=mock_s3,
                bucket=MOCK_S3_BUCKET,
                sem=sem,
            )

        assert result is None
        mock_s3.put_object.assert_not_called()

    async def test_image_too_large_returns_none(self):
        large_body = b"x" * (5 * 1024 * 1024 + 1)
        mock_s3 = MagicMock()
        sem = asyncio.Semaphore(10)

        with patch(
            "scripts.ingest_serper_catalog.httpx.AsyncClient",
            return_value=_make_streaming_response(200, "image/jpeg", large_body),
        ):
            result = await download_image(
                url="https://cdn.example.com/image.jpg",
                item_id="serper_abc123",
                s3_client=mock_s3,
                bucket=MOCK_S3_BUCKET,
                sem=sem,
            )

        assert result is None

    async def test_http_error_returns_none(self):
        mock_s3 = MagicMock()
        sem = asyncio.Semaphore(10)

        with patch(
            "scripts.ingest_serper_catalog.httpx.AsyncClient",
            side_effect=Exception("connection refused"),
        ):
            result = await download_image(
                url="https://cdn.example.com/image.jpg",
                item_id="serper_abc123",
                s3_client=mock_s3,
                bucket=MOCK_S3_BUCKET,
                sem=sem,
            )

        assert result is None


# ---------------------------------------------------------------------------
# store_bronze_json
# ---------------------------------------------------------------------------


class TestStoreBronzeJson:
    def test_s3_key_format(self):
        mock_s3 = MagicMock()
        store_bronze_json([MOCK_SERPER_RESULT], brand="Acne Studios", s3_client=mock_s3, bucket=MOCK_S3_BUCKET)
        call_kwargs = mock_s3.put_object.call_args.kwargs
        key = call_kwargs["Key"]
        assert key.startswith("raw/catalog/serper/dt=")
        assert "brand=acne-studios" in key
        assert key.endswith(".json")

    def test_content_is_json(self):
        mock_s3 = MagicMock()
        store_bronze_json([MOCK_SERPER_RESULT], brand="Acne Studios", s3_client=mock_s3, bucket=MOCK_S3_BUCKET)
        call_kwargs = mock_s3.put_object.call_args.kwargs
        assert call_kwargs["ContentType"] == "application/json"
        parsed = json.loads(call_kwargs["Body"])
        assert len(parsed) == 1

    def test_correct_bucket(self):
        mock_s3 = MagicMock()
        store_bronze_json([MOCK_SERPER_RESULT], brand="Acne Studios", s3_client=mock_s3, bucket=MOCK_S3_BUCKET)
        call_kwargs = mock_s3.put_object.call_args.kwargs
        assert call_kwargs["Bucket"] == MOCK_S3_BUCKET


# ---------------------------------------------------------------------------
# bulk_upsert
# ---------------------------------------------------------------------------


def _make_item(item_id: str = "serper_abc123") -> CatalogItemCreate:
    return CatalogItemCreate(
        item_id=item_id,
        domain="fashion",
        title="Test Shirt",
        price=195.00,
        image_url=f"s3://{MOCK_S3_BUCKET}/images/catalog/serper/{item_id}.jpg",
        product_url="https://example.com/product",
        source="serper",
        content_hash="a" * 64,
        attributes={"brand": "Acne Studios", "category": "menswear"},
    )


class TestBulkUpsert:
    def test_dry_run_returns_zeros(self):
        mock_conn = MagicMock()
        inserted, updated = bulk_upsert(mock_conn, [_make_item()], dry_run=True)
        assert inserted == 0
        assert updated == 0
        mock_conn.cursor.assert_not_called()

    def test_empty_items_returns_zeros(self):
        mock_conn = MagicMock()
        inserted, updated = bulk_upsert(mock_conn, [], dry_run=False)
        assert inserted == 0
        assert updated == 0

    def test_inserted_item_counted(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchone.return_value = ("serper_abc123", True)  # xmax=0 → inserted
        mock_conn.cursor.return_value = mock_cur

        inserted, updated = bulk_upsert(mock_conn, [_make_item()], dry_run=False)

        assert inserted == 1
        assert updated == 0
        mock_conn.commit.assert_called_once()

    def test_updated_item_counted(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchone.return_value = ("serper_abc123", False)  # xmax≠0 → updated
        mock_conn.cursor.return_value = mock_cur

        inserted, updated = bulk_upsert(mock_conn, [_make_item()], dry_run=False)

        assert inserted == 0
        assert updated == 1

    def test_sql_uses_parameterized_values(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchone.return_value = ("serper_abc123", True)
        mock_conn.cursor.return_value = mock_cur

        bulk_upsert(mock_conn, [_make_item()], dry_run=False)

        call_args = mock_cur.execute.call_args
        sql, params = call_args[0]
        assert "%s" in sql
        assert "serper_abc123" not in sql  # item_id must be a param, not interpolated
        assert len(params) == 9
