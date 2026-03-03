"""Unit tests for poshmark_service.py."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.services.poshmark_service as poshmark_service
from app.models.catalog_item import CatalogItemCreate, PoshmarkListingRaw
from tests.conftest import (
    MOCK_POSHMARK_LISTING,
    MOCK_POSHMARK_LISTING_LOW_PRICE,
    MOCK_POSHMARK_LISTING_NO_COVER,
    MOCK_POSHMARK_LISTING_NO_TITLE,
    MOCK_POSHMARK_LISTING_POOR,
    MOCK_S3_BUCKET,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_http_response(status_code: int, json_body) -> MagicMock:
    """Create a mock httpx response."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_body
    mock.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx

        mock.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=mock,
        )
    return mock


def _make_streaming_response(status_code: int, content_type: str, body: bytes):
    """Create a mock async streaming httpx response context manager."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.headers = {"content-type": content_type}
    mock_response.raise_for_status = MagicMock()

    async def _aiter_bytes(chunk_size=8192):
        yield body

    mock_response.aiter_bytes = _aiter_bytes

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm


# ---------------------------------------------------------------------------
# TestPoshmarkListingRawModel
# ---------------------------------------------------------------------------


class TestPoshmarkListingRawModel:
    def test_valid_listing_parses_correctly(self):
        listing = PoshmarkListingRaw.model_validate(MOCK_POSHMARK_LISTING)
        assert listing.id == "abc123def456"
        assert listing.title == "Zara Oxford Shirt Men"
        assert listing.condition == "nwt"
        assert listing.brand == "Zara"
        assert listing.price_amount is not None
        assert listing.price_amount.val == "35.00"

    def test_missing_optional_fields_use_defaults(self):
        listing = PoshmarkListingRaw.model_validate({"id": "minimal123"})
        assert listing.id == "minimal123"
        assert listing.title is None
        assert listing.condition is None
        assert listing.colors == []
        assert listing.cover_shot is None

    def test_extra_fields_are_allowed(self):
        data = {**MOCK_POSHMARK_LISTING, "unknown_future_field": "some_value"}
        listing = PoshmarkListingRaw.model_validate(data)
        assert listing.id == MOCK_POSHMARK_LISTING["id"]
        # Extra fields should not raise validation error

    def test_missing_required_id_raises_validation_error(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PoshmarkListingRaw.model_validate({"title": "No ID listing"})

    def test_to_attributes_whitelists_known_fields(self):
        listing = PoshmarkListingRaw.model_validate(MOCK_POSHMARK_LISTING)
        attrs = listing.to_attributes()
        assert attrs["brand"] == "Zara"
        assert attrs["size"] == "M"
        assert attrs["condition"] == "nwt"
        assert "white" in attrs["colors"]
        assert attrs["category"] == "Tops"
        assert attrs["department"] == "Men"
        assert attrs["seller_username"] == "fashion_seller"
        assert attrs["is_available"] is True

    def test_to_attributes_excludes_unknown_extra_fields(self):
        data = {**MOCK_POSHMARK_LISTING, "malicious_key": "DROP TABLE users;"}
        listing = PoshmarkListingRaw.model_validate(data)
        attrs = listing.to_attributes()
        assert "malicious_key" not in attrs

    def test_to_attributes_truncates_long_values(self):
        data = {**MOCK_POSHMARK_LISTING, "brand": "A" * 500}
        listing = PoshmarkListingRaw.model_validate(data)
        attrs = listing.to_attributes()
        assert len(attrs["brand"]) <= 255


# ---------------------------------------------------------------------------
# TestIsQualityListing
# ---------------------------------------------------------------------------


class TestIsQualityListing:
    def test_nwt_condition_passes(self):
        listing = PoshmarkListingRaw.model_validate(MOCK_POSHMARK_LISTING)
        assert poshmark_service.is_quality_listing(listing) is True

    def test_nwot_condition_passes(self):
        data = {**MOCK_POSHMARK_LISTING, "condition": "nwot"}
        listing = PoshmarkListingRaw.model_validate(data)
        assert poshmark_service.is_quality_listing(listing) is True

    def test_good_condition_passes(self):
        data = {**MOCK_POSHMARK_LISTING, "condition": "good"}
        listing = PoshmarkListingRaw.model_validate(data)
        assert poshmark_service.is_quality_listing(listing) is True

    def test_fair_condition_fails(self):
        data = {**MOCK_POSHMARK_LISTING, "condition": "fair"}
        listing = PoshmarkListingRaw.model_validate(data)
        assert poshmark_service.is_quality_listing(listing) is False

    def test_poor_condition_fails(self):
        listing = PoshmarkListingRaw.model_validate(MOCK_POSHMARK_LISTING_POOR)
        assert poshmark_service.is_quality_listing(listing) is False

    def test_missing_condition_fails(self):
        data = {**MOCK_POSHMARK_LISTING, "condition": None}
        listing = PoshmarkListingRaw.model_validate(data)
        assert poshmark_service.is_quality_listing(listing) is False

    def test_missing_title_fails(self):
        listing = PoshmarkListingRaw.model_validate(MOCK_POSHMARK_LISTING_NO_TITLE)
        assert poshmark_service.is_quality_listing(listing) is False

    def test_empty_title_fails(self):
        data = {**MOCK_POSHMARK_LISTING, "title": "  "}
        listing = PoshmarkListingRaw.model_validate(data)
        assert poshmark_service.is_quality_listing(listing) is False

    def test_short_title_fails(self):
        data = {**MOCK_POSHMARK_LISTING, "title": "Hi"}
        listing = PoshmarkListingRaw.model_validate(data)
        assert poshmark_service.is_quality_listing(listing) is False

    def test_missing_price_fails(self):
        data = {**MOCK_POSHMARK_LISTING, "price_amount": None}
        listing = PoshmarkListingRaw.model_validate(data)
        assert poshmark_service.is_quality_listing(listing) is False

    def test_price_below_minimum_fails(self):
        listing = PoshmarkListingRaw.model_validate(MOCK_POSHMARK_LISTING_LOW_PRICE)
        assert poshmark_service.is_quality_listing(listing) is False

    def test_price_above_maximum_fails(self):
        data = {**MOCK_POSHMARK_LISTING, "price_amount": {"val": "9999.00"}}
        listing = PoshmarkListingRaw.model_validate(data)
        assert poshmark_service.is_quality_listing(listing) is False

    def test_missing_cover_shot_fails(self):
        listing = PoshmarkListingRaw.model_validate(MOCK_POSHMARK_LISTING_NO_COVER)
        assert poshmark_service.is_quality_listing(listing) is False

    def test_cover_shot_with_no_url_small_fails(self):
        data = {
            **MOCK_POSHMARK_LISTING,
            "cover_shot": {
                "url_small": None,
                "url_large": "https://example.com/img.jpg",
            },
        }
        listing = PoshmarkListingRaw.model_validate(data)
        assert poshmark_service.is_quality_listing(listing) is False


# ---------------------------------------------------------------------------
# TestParseListing
# ---------------------------------------------------------------------------


class TestParseListing:
    def test_happy_path_returns_catalog_item_create(self):
        listing = PoshmarkListingRaw.model_validate(MOCK_POSHMARK_LISTING)
        result = poshmark_service.parse_listing(
            listing, query_context="oxford shirt men"
        )
        assert isinstance(result, CatalogItemCreate)
        assert result.item_id == "abc123def456"
        assert result.domain == "fashion"
        assert result.source == "poshmark_seed"

    def test_price_parsed_as_float(self):
        listing = PoshmarkListingRaw.model_validate(MOCK_POSHMARK_LISTING)
        result = poshmark_service.parse_listing(listing, query_context="test")
        assert result is not None
        assert result.price == 35.0

    def test_product_url_constructed_correctly(self):
        listing = PoshmarkListingRaw.model_validate(MOCK_POSHMARK_LISTING)
        result = poshmark_service.parse_listing(listing, query_context="test")
        assert result is not None
        assert result.product_url == "https://poshmark.com/listing/abc123def456"

    def test_content_hash_is_sha256(self):
        listing = PoshmarkListingRaw.model_validate(MOCK_POSHMARK_LISTING)
        result = poshmark_service.parse_listing(listing, query_context="test")
        assert result is not None
        assert result.content_hash is not None
        assert len(result.content_hash) == 64  # SHA-256 hex digest

    def test_content_hash_is_deterministic(self):
        listing = PoshmarkListingRaw.model_validate(MOCK_POSHMARK_LISTING)
        r1 = poshmark_service.parse_listing(listing, query_context="q1")
        r2 = poshmark_service.parse_listing(listing, query_context="q2")
        assert r1 is not None and r2 is not None
        assert r1.content_hash == r2.content_hash

    def test_attributes_contain_required_fields(self):
        listing = PoshmarkListingRaw.model_validate(MOCK_POSHMARK_LISTING)
        result = poshmark_service.parse_listing(
            listing, query_context="oxford shirt men"
        )
        assert result is not None
        assert result.attributes["brand"] == "Zara"
        assert result.attributes["size"] == "M"
        assert result.attributes["condition"] == "nwt"

    def test_query_context_stored_in_attributes(self):
        listing = PoshmarkListingRaw.model_validate(MOCK_POSHMARK_LISTING)
        result = poshmark_service.parse_listing(
            listing, query_context="oxford shirt men"
        )
        assert result is not None
        assert result.attributes["query_context"] == "oxford shirt men"

    def test_filtered_listing_returns_none(self):
        listing = PoshmarkListingRaw.model_validate(MOCK_POSHMARK_LISTING_POOR)
        result = poshmark_service.parse_listing(listing, query_context="test")
        assert result is None

    def test_image_url_is_none_before_s3_upload(self):
        """image_url is set separately after S3 upload; should be None from parse_listing."""
        listing = PoshmarkListingRaw.model_validate(MOCK_POSHMARK_LISTING)
        result = poshmark_service.parse_listing(listing, query_context="test")
        assert result is not None
        assert result.image_url is None


# ---------------------------------------------------------------------------
# TestSearchListings
# ---------------------------------------------------------------------------


class TestSearchListings:
    @pytest.mark.asyncio
    async def test_happy_path_returns_parsed_listings(self):
        api_response = {"listings": [MOCK_POSHMARK_LISTING]}
        mock_response = _make_http_response(200, api_response)

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            results = await poshmark_service.search_listings(
                "oxford shirt men", api_key="fake-key"
            )

        assert len(results) == 1
        assert results[0].id == "abc123def456"

    @pytest.mark.asyncio
    async def test_returns_list_response_format(self):
        """Some Poshmark endpoints return a list directly, not {"listings": [...]}."""
        mock_response = _make_http_response(200, [MOCK_POSHMARK_LISTING])

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            results = await poshmark_service.search_listings(
                "oxford shirt men", api_key="fake-key"
            )

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_empty_response_returns_empty_list(self):
        mock_response = _make_http_response(200, {"listings": []})

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            results = await poshmark_service.search_listings(
                "oxford shirt men", api_key="fake-key"
            )

        assert results == []

    @pytest.mark.asyncio
    async def test_correct_rapidapi_headers_sent(self):
        mock_response = _make_http_response(200, {"listings": []})
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await poshmark_service.search_listings("test", api_key="my-api-key-123")

        call_kwargs = mock_client.get.call_args
        headers = call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {}))
        assert headers["x-rapidapi-key"] == "my-api-key-123"
        assert headers["x-rapidapi-host"] == "poshmark.p.rapidapi.com"

    @pytest.mark.asyncio
    async def test_page_param_forwarded(self):
        mock_response = _make_http_response(200, {"listings": []})
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await poshmark_service.search_listings("test", api_key="k", page=3)

        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params", {}))
        assert params["page"] == 3

    @pytest.mark.asyncio
    async def test_rate_limit_429_retries_with_backoff(self):
        import httpx as httpx_mod

        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.raise_for_status = MagicMock()

        success = _make_http_response(200, {"listings": [MOCK_POSHMARK_LISTING]})

        mock_client = AsyncMock()
        mock_client.get.side_effect = [rate_limited, success]

        with (
            patch("httpx.AsyncClient") as mock_cls,
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            results = await poshmark_service.search_listings("test", api_key="k")

        assert len(results) == 1
        mock_sleep.assert_called_once_with(poshmark_service.RETRY_DELAYS[0])

    @pytest.mark.asyncio
    async def test_http_error_raises_after_retries(self):
        import httpx as httpx_mod

        error_response = _make_http_response(500, {})
        mock_client = AsyncMock()
        mock_client.get.return_value = error_response

        with (
            patch("httpx.AsyncClient") as mock_cls,
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(httpx_mod.HTTPStatusError):
                await poshmark_service.search_listings("test", api_key="k")

    @pytest.mark.asyncio
    async def test_malformed_listing_skipped_gracefully(self):
        """A listing missing 'id' should be skipped without raising."""
        bad_listing = {"title": "No ID listing", "condition": "nwt"}
        api_response = {"listings": [bad_listing, MOCK_POSHMARK_LISTING]}
        mock_response = _make_http_response(200, api_response)

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            results = await poshmark_service.search_listings("test", api_key="k")

        # Only the valid listing should be returned
        assert len(results) == 1
        assert results[0].id == "abc123def456"


# ---------------------------------------------------------------------------
# TestDownloadImage
# ---------------------------------------------------------------------------


class TestDownloadImage:
    @pytest.mark.asyncio
    async def test_happy_path_returns_s3_url(self):
        image_bytes = b"\xff\xd8\xff" + b"fake-jpeg-data"
        stream_cm = _make_streaming_response(200, "image/jpeg", image_bytes)

        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = Exception("NoSuchKey")

        mock_client = MagicMock()
        mock_client.stream.return_value = stream_cm
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        sem = asyncio.Semaphore(10)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await poshmark_service.download_image(
                url="https://di2ponv0v5otw.cloudfront.net/img.jpg",
                item_id="abc123",
                s3_client=mock_s3,
                bucket=MOCK_S3_BUCKET,
                sem=sem,
            )

        assert result == f"s3://{MOCK_S3_BUCKET}/images/catalog/poshmark/abc123.jpg"
        mock_s3.put_object.assert_called_once()

    @pytest.mark.asyncio
    async def test_correct_s3_key_format(self):
        image_bytes = b"fake"
        stream_cm = _make_streaming_response(200, "image/png", image_bytes)

        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = Exception("NoSuchKey")

        mock_client = MagicMock()
        mock_client.stream.return_value = stream_cm
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        sem = asyncio.Semaphore(10)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await poshmark_service.download_image(
                url="https://di2ponv0v5otw.cloudfront.net/img.jpg",
                item_id="item-xyz-789",
                s3_client=mock_s3,
                bucket=MOCK_S3_BUCKET,
                sem=sem,
            )

        call_args = mock_s3.put_object.call_args
        assert call_args.kwargs["Key"] == "images/catalog/poshmark/item-xyz-789.jpg"

    @pytest.mark.asyncio
    async def test_ssrf_disallowed_host_returns_none(self):
        mock_s3 = MagicMock()
        sem = asyncio.Semaphore(10)

        result = await poshmark_service.download_image(
            url="http://169.254.169.254/latest/meta-data/",
            item_id="abc123",
            s3_client=mock_s3,
            bucket=MOCK_S3_BUCKET,
            sem=sem,
        )

        assert result is None
        mock_s3.put_object.assert_not_called()

    @pytest.mark.asyncio
    async def test_ssrf_internal_vpc_host_returns_none(self):
        mock_s3 = MagicMock()
        sem = asyncio.Semaphore(10)

        result = await poshmark_service.download_image(
            url="http://10.0.10.5:5432/",
            item_id="abc123",
            s3_client=mock_s3,
            bucket=MOCK_S3_BUCKET,
            sem=sem,
        )

        assert result is None
        mock_s3.put_object.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_image_content_type_returns_none(self):
        stream_cm = _make_streaming_response(200, "text/html", b"<html>error</html>")

        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = Exception("NoSuchKey")

        mock_client = MagicMock()
        mock_client.stream.return_value = stream_cm
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        sem = asyncio.Semaphore(10)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await poshmark_service.download_image(
                url="https://di2ponv0v5otw.cloudfront.net/img.jpg",
                item_id="abc123",
                s3_client=mock_s3,
                bucket=MOCK_S3_BUCKET,
                sem=sem,
            )

        assert result is None
        mock_s3.put_object.assert_not_called()

    @pytest.mark.asyncio
    async def test_oversized_response_returns_none(self):
        """Response exceeding MAX_IMAGE_BYTES should be skipped."""
        large_body = b"X" * (poshmark_service.MAX_IMAGE_BYTES + 1)
        stream_cm = _make_streaming_response(200, "image/jpeg", large_body)

        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = Exception("NoSuchKey")

        mock_client = MagicMock()
        mock_client.stream.return_value = stream_cm
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        sem = asyncio.Semaphore(10)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await poshmark_service.download_image(
                url="https://di2ponv0v5otw.cloudfront.net/large.jpg",
                item_id="bigitem",
                s3_client=mock_s3,
                bucket=MOCK_S3_BUCKET,
                sem=sem,
            )

        assert result is None
        mock_s3.put_object.assert_not_called()

    @pytest.mark.asyncio
    async def test_s3_put_error_returns_none(self):
        image_bytes = b"fake"
        stream_cm = _make_streaming_response(200, "image/jpeg", image_bytes)

        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = Exception("NoSuchKey")
        mock_s3.put_object.side_effect = Exception("S3 error")

        mock_client = MagicMock()
        mock_client.stream.return_value = stream_cm
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        sem = asyncio.Semaphore(10)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await poshmark_service.download_image(
                url="https://di2ponv0v5otw.cloudfront.net/img.jpg",
                item_id="abc123",
                s3_client=mock_s3,
                bucket=MOCK_S3_BUCKET,
                sem=sem,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_existing_s3_key_skips_download(self):
        """If the key already exists in S3, return the URL without downloading."""
        mock_s3 = MagicMock()
        # head_object succeeds (key exists)
        mock_s3.head_object.return_value = {"ContentLength": 1234}

        sem = asyncio.Semaphore(10)

        with patch("httpx.AsyncClient") as mock_http_cls:
            result = await poshmark_service.download_image(
                url="https://di2ponv0v5otw.cloudfront.net/img.jpg",
                item_id="existing123",
                s3_client=mock_s3,
                bucket=MOCK_S3_BUCKET,
                sem=sem,
            )

        expected_url = f"s3://{MOCK_S3_BUCKET}/images/catalog/poshmark/existing123.jpg"
        assert result == expected_url
        # No actual HTTP download or S3 PUT should happen
        mock_http_cls.assert_not_called()
        mock_s3.put_object.assert_not_called()


# ---------------------------------------------------------------------------
# TestStoreBronzeJson
# ---------------------------------------------------------------------------


class TestStoreBronzeJson:
    def test_happy_path_writes_to_s3(self):
        mock_s3 = MagicMock()
        listings = [MOCK_POSHMARK_LISTING]

        poshmark_service.store_bronze_json(
            listings, "oxford shirt men", mock_s3, MOCK_S3_BUCKET
        )

        mock_s3.put_object.assert_called_once()

    def test_key_contains_dt_partition(self):
        mock_s3 = MagicMock()
        poshmark_service.store_bronze_json(
            [MOCK_POSHMARK_LISTING], "test query", mock_s3, MOCK_S3_BUCKET
        )

        key = mock_s3.put_object.call_args.kwargs["Key"]
        assert "dt=" in key

    def test_key_starts_with_bronze_prefix(self):
        mock_s3 = MagicMock()
        poshmark_service.store_bronze_json(
            [MOCK_POSHMARK_LISTING], "test query", mock_s3, MOCK_S3_BUCKET
        )

        key = mock_s3.put_object.call_args.kwargs["Key"]
        assert key.startswith("raw/catalog/poshmark/")

    def test_key_contains_slugified_query(self):
        mock_s3 = MagicMock()
        poshmark_service.store_bronze_json(
            [], "Oxford Shirt Men!", mock_s3, MOCK_S3_BUCKET
        )

        key = mock_s3.put_object.call_args.kwargs["Key"]
        assert "query=oxford-shirt-men" in key

    def test_body_is_valid_json(self):
        mock_s3 = MagicMock()
        listings = [MOCK_POSHMARK_LISTING, MOCK_POSHMARK_LISTING_POOR]
        poshmark_service.store_bronze_json(listings, "test", mock_s3, MOCK_S3_BUCKET)

        body_bytes = mock_s3.put_object.call_args.kwargs["Body"]
        parsed = json.loads(body_bytes)
        assert len(parsed) == 2

    def test_content_type_is_application_json(self):
        mock_s3 = MagicMock()
        poshmark_service.store_bronze_json([], "test", mock_s3, MOCK_S3_BUCKET)

        content_type = mock_s3.put_object.call_args.kwargs["ContentType"]
        assert content_type == "application/json"

    def test_empty_bucket_does_not_write(self):
        mock_s3 = MagicMock()
        poshmark_service.store_bronze_json([], "test", mock_s3, "")
        mock_s3.put_object.assert_not_called()

    def test_none_s3_client_does_not_raise(self):
        # Should log a warning and return gracefully
        poshmark_service.store_bronze_json([], "test", None, MOCK_S3_BUCKET)

    def test_s3_error_does_not_raise(self):
        mock_s3 = MagicMock()
        mock_s3.put_object.side_effect = Exception("S3 connection error")
        # Must not raise
        poshmark_service.store_bronze_json(
            [MOCK_POSHMARK_LISTING], "test", mock_s3, MOCK_S3_BUCKET
        )
