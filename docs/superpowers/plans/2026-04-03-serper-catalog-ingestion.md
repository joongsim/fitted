# Serper Catalog Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a one-off script that searches Google Shopping via Serper's API for menswear brands, downloads product images to S3, and upserts items into `catalog_items`.

**Architecture:** Standalone script (`scripts/ingest_serper_catalog.py`) containing all Serper-specific logic as module-level functions. Reads brand list from `config/serper_brands.json`, calls Serper's Shopping API once per brand, downloads images with bounded concurrency, and bulk-upserts into the existing `catalog_items` schema. Follows the Poshmark ingestion script pattern exactly.

**Tech Stack:** Python 3.11+, httpx (async), psycopg3, boto3, asyncio semaphore for image concurrency.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `config/serper_brands.json` | Configurable brand list |
| Modify | `.env.example` | Document SERPER_API_KEY |
| Create | `scripts/ingest_serper_catalog.py` | All ingestion logic + CLI |
| Create | `tests/test_ingest_serper_catalog.py` | Unit tests for all functions |

---

### Task 1: Config files

**Files:**
- Create: `config/serper_brands.json`
- Modify: `.env.example`

- [ ] **Step 1: Create brand config**

Create `config/serper_brands.json`:
```json
{
  "brands": [
    "Acne Studios",
    "AMI Paris",
    "Maison Margiela",
    "A.P.C.",
    "Kenzo",
    "Thom Browne",
    "Loewe",
    "Carhartt WIP",
    "Norse Projects",
    "Universal Works",
    "Jacquemus",
    "Snow Peak",
    "Tod's",
    "Pas Normal Studios",
    "Lemaire"
  ]
}
```

- [ ] **Step 2: Add SERPER_API_KEY to .env.example**

Add to the bottom of `.env.example`:
```
SERPER_API_KEY=<your-key>
```

- [ ] **Step 3: Commit**

```bash
git add config/serper_brands.json .env.example
git commit -m "config: add serper brand list and SERPER_API_KEY env var"
```

---

### Task 2: Price parsing, item ID, and slugify helpers (TDD)

**Files:**
- Create: `scripts/ingest_serper_catalog.py` (initial functions only)
- Create: `tests/test_ingest_serper_catalog.py` (price + ID tests)

- [ ] **Step 1: Write failing tests**

Create `tests/test_ingest_serper_catalog.py`:
```python
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
PYTHONPATH=. pytest tests/test_ingest_serper_catalog.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'scripts.ingest_serper_catalog'`

- [ ] **Step 3: Create the script with just these helper functions**

Create `scripts/ingest_serper_catalog.py`:
```python
"""
Serper Google Shopping catalog ingestion script.

Searches Google Shopping via Serper's API for a configurable list of menswear
brands, downloads product images to S3, and upserts into catalog_items.

Usage:
    # Dry run — no S3 writes or DB upserts
    PYTHONPATH=. python scripts/ingest_serper_catalog.py --dry-run

    # Full run
    PYTHONPATH=. python scripts/ingest_serper_catalog.py

    # Custom brand list
    PYTHONPATH=. python scripts/ingest_serper_catalog.py --brands-file config/my_brands.json

Environment:
    SERPER_API_KEY        Serper.dev API key
    DATABASE_URL          PostgreSQL connection URL
    WEATHER_BUCKET_NAME   S3 bucket for bronze JSON and images
    USE_SSM=false         Use environment variables instead of SSM (local dev)

Security notes:
    - SERPER_API_KEY must NOT be committed to git or logged
    - All DB values are parameterized — no f-string interpolation of API data
    - Images are validated for content-type and size before S3 upload
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import pathlib
import re
import sys
from datetime import datetime, timezone
from typing import Optional

import boto3
import httpx
import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from app.core.config import config
from app.models.catalog_item import CatalogItemCreate, make_content_hash

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("ingest_serper")

SERPER_SHOPPING_URL = "https://google.serper.dev/shopping"
REQUEST_TIMEOUT = 15.0
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_price(price_str: Optional[str]) -> Optional[float]:
    """
    Parse a USD price string to float.

    Returns None if the price is missing, non-USD, or unparseable.
    Only USD prices (starting with '$') are accepted.
    """
    if not price_str:
        return None
    if not price_str.startswith("$"):
        return None
    cleaned = price_str.lstrip("$").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def make_item_id(link: str) -> str:
    """Generate a stable item_id from the product URL."""
    return f"serper_{hashlib.sha256(link.encode()).hexdigest()[:12]}"


def _slugify(text: str) -> str:
    """Convert a brand name to a safe S3 key segment."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:80]
```

- [ ] **Step 4: Run tests again**

```bash
PYTHONPATH=. pytest tests/test_ingest_serper_catalog.py::TestParsePrice tests/test_ingest_serper_catalog.py::TestMakeItemId tests/test_ingest_serper_catalog.py::TestSlugify -v
```

Expected: All 13 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/ingest_serper_catalog.py tests/test_ingest_serper_catalog.py
git commit -m "feat: add serper ingestion script helpers (parse_price, make_item_id)"
```

---

### Task 3: Response parser (TDD)

**Files:**
- Modify: `scripts/ingest_serper_catalog.py` (add `parse_result`)
- Modify: `tests/test_ingest_serper_catalog.py` (add `TestParseResult`)

- [ ] **Step 1: Write failing tests**

Add to `tests/test_ingest_serper_catalog.py`:
```python
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
```

- [ ] **Step 2: Run to confirm failure**

```bash
PYTHONPATH=. pytest tests/test_ingest_serper_catalog.py::TestParseResult -v 2>&1 | head -20
```

Expected: `ImportError` — `parse_result` not yet defined.

- [ ] **Step 3: Implement `parse_result`**

Add to `scripts/ingest_serper_catalog.py` after the helpers section:
```python
# ---------------------------------------------------------------------------
# Serper response parser
# ---------------------------------------------------------------------------


def parse_result(result: dict, brand: str) -> Optional[CatalogItemCreate]:
    """
    Parse a single Serper Google Shopping result into a CatalogItemCreate.

    Returns None if the result is missing required fields or has a non-USD price.
    image_url is intentionally left None — set after S3 upload.
    """
    link = result.get("link") or ""
    if not link:
        return None

    title = result.get("title") or ""
    if not title:
        return None

    price = parse_price(result.get("price"))
    if price is None:
        return None

    item_id = make_item_id(link)
    content_hash = make_content_hash(title, price, brand, "menswear")

    return CatalogItemCreate(
        item_id=item_id,
        domain="fashion",
        title=title[:500],
        price=price,
        image_url=None,
        product_url=link,
        source="serper",
        content_hash=content_hash,
        attributes={"brand": brand[:255], "category": "menswear"},
    )
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=. pytest tests/test_ingest_serper_catalog.py::TestParseResult -v
```

Expected: All 13 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/ingest_serper_catalog.py tests/test_ingest_serper_catalog.py
git commit -m "feat: add serper result parser"
```

---

### Task 4: Serper API client (TDD)

**Files:**
- Modify: `scripts/ingest_serper_catalog.py` (add `search_shopping`)
- Modify: `tests/test_ingest_serper_catalog.py` (add `TestSearchShopping`)

- [ ] **Step 1: Write failing tests**

Add to `tests/test_ingest_serper_catalog.py`:
```python
# ---------------------------------------------------------------------------
# search_shopping
# ---------------------------------------------------------------------------

from scripts.ingest_serper_catalog import search_shopping


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
```

- [ ] **Step 2: Run to confirm failure**

```bash
PYTHONPATH=. pytest tests/test_ingest_serper_catalog.py::TestSearchShopping -v 2>&1 | head -20
```

Expected: `ImportError` — `search_shopping` not yet defined.

- [ ] **Step 3: Implement `search_shopping`**

Add to `scripts/ingest_serper_catalog.py` after the parser section:
```python
# ---------------------------------------------------------------------------
# Serper API client
# ---------------------------------------------------------------------------


async def search_shopping(query: str, api_key: str) -> list[dict]:
    """
    Call Serper Google Shopping API and return the raw results list.

    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.post(
            SERPER_SHOPPING_URL,
            headers=headers,
            json={"q": query},
        )
        response.raise_for_status()
        data = response.json()
    return data.get("shopping", [])
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=. pytest tests/test_ingest_serper_catalog.py::TestSearchShopping -v
```

Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/ingest_serper_catalog.py tests/test_ingest_serper_catalog.py
git commit -m "feat: add serper shopping API client"
```

---

### Task 5: Image download + S3 upload (TDD)

**Files:**
- Modify: `scripts/ingest_serper_catalog.py` (add `download_image`)
- Modify: `tests/test_ingest_serper_catalog.py` (add `TestDownloadImage`)

- [ ] **Step 1: Write failing tests**

Add to `tests/test_ingest_serper_catalog.py`:
```python
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
```

- [ ] **Step 2: Run to confirm failure**

```bash
PYTHONPATH=. pytest tests/test_ingest_serper_catalog.py::TestDownloadImage -v 2>&1 | head -20
```

Expected: `ImportError` — `download_image` not yet defined.

- [ ] **Step 3: Implement `download_image`**

Add to `scripts/ingest_serper_catalog.py`:
```python
# ---------------------------------------------------------------------------
# Image download + S3 upload
# ---------------------------------------------------------------------------


async def download_image(
    url: str,
    item_id: str,
    s3_client,
    bucket: str,
    sem: asyncio.Semaphore,
) -> Optional[str]:
    """
    Download a product image and upload it to S3.

    Returns the S3 URL (s3://bucket/key) on success, or None on any failure.
    Validates content-type (must be image/*) and size (max 5 MB).
    """
    async with sem:
        try:
            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT, follow_redirects=True
            ) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "")
                    if not content_type.startswith("image/"):
                        logger.warning(
                            "Skipping %s — non-image content-type: %r",
                            item_id,
                            content_type,
                        )
                        return None

                    chunks = []
                    total = 0
                    async for chunk in response.aiter_bytes(8192):
                        total += len(chunk)
                        if total > MAX_IMAGE_BYTES:
                            logger.warning(
                                "Skipping %s — image exceeds 5 MB", item_id
                            )
                            return None
                        chunks.append(chunk)

                    image_data = b"".join(chunks)

            s3_key = f"images/catalog/serper/{item_id}.jpg"
            s3_client.put_object(
                Bucket=bucket,
                Key=s3_key,
                Body=image_data,
                ContentType="image/jpeg",
            )
            return f"s3://{bucket}/{s3_key}"

        except Exception:
            logger.warning(
                "Failed to download/upload image for %s", item_id, exc_info=True
            )
            return None
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=. pytest tests/test_ingest_serper_catalog.py::TestDownloadImage -v
```

Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/ingest_serper_catalog.py tests/test_ingest_serper_catalog.py
git commit -m "feat: add serper image download and S3 upload"
```

---

### Task 6: Bronze storage + DB upsert (TDD)

**Files:**
- Modify: `scripts/ingest_serper_catalog.py` (add `store_bronze_json`, `bulk_upsert`)
- Modify: `tests/test_ingest_serper_catalog.py` (add `TestStoreBronzeJson`, `TestBulkUpsert`)

- [ ] **Step 1: Write failing tests**

Add to `tests/test_ingest_serper_catalog.py`:
```python
# ---------------------------------------------------------------------------
# store_bronze_json
# ---------------------------------------------------------------------------

from scripts.ingest_serper_catalog import store_bronze_json, bulk_upsert


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
        # SQL must use %s placeholders, never f-strings with user data
        assert "%s" in sql
        assert "serper_abc123" not in sql  # item_id must be a param, not interpolated
        assert len(params) == 9
```

- [ ] **Step 2: Run to confirm failure**

```bash
PYTHONPATH=. pytest tests/test_ingest_serper_catalog.py::TestStoreBronzeJson tests/test_ingest_serper_catalog.py::TestBulkUpsert -v 2>&1 | head -20
```

Expected: `ImportError` — `store_bronze_json` and `bulk_upsert` not yet defined.

- [ ] **Step 3: Implement `store_bronze_json` and `bulk_upsert`**

Add to `scripts/ingest_serper_catalog.py`:
```python
# ---------------------------------------------------------------------------
# Bronze layer + DB upsert
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


def store_bronze_json(
    results: list[dict],
    brand: str,
    s3_client,
    bucket: str,
) -> None:
    """Store raw Serper results to the S3 bronze layer for audit."""
    now = datetime.now(tz=timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H-%M-%S")
    slug = _slugify(brand)
    key = f"raw/catalog/serper/dt={date_str}/brand={slug}/{time_str}.json"
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(results),
        ContentType="application/json",
    )
    logger.info("Stored %d raw results to s3://%s/%s", len(results), bucket, key)


def bulk_upsert(
    conn,
    items: list[CatalogItemCreate],
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
            if row and row[1]:
                inserted += 1
            else:
                updated += 1
    conn.commit()
    return inserted, updated
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=. pytest tests/test_ingest_serper_catalog.py::TestStoreBronzeJson tests/test_ingest_serper_catalog.py::TestBulkUpsert -v
```

Expected: All 8 tests PASS.

- [ ] **Step 5: Run the full test file to confirm nothing regressed**

```bash
PYTHONPATH=. pytest tests/test_ingest_serper_catalog.py -v
```

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/ingest_serper_catalog.py tests/test_ingest_serper_catalog.py
git commit -m "feat: add serper bronze storage and DB upsert"
```

---

### Task 7: Main ingestion loop + CLI

**Files:**
- Modify: `scripts/ingest_serper_catalog.py` (add `ingest()` and `main()`)

No new tests — the functions these call are already unit-tested. Verified via dry-run in Task 8.

- [ ] **Step 1: Implement `ingest()` and `main()`**

Append to `scripts/ingest_serper_catalog.py`:
```python
# ---------------------------------------------------------------------------
# Main ingestion coroutine
# ---------------------------------------------------------------------------


async def ingest(args: argparse.Namespace) -> None:
    """Main ingestion loop: iterate over brands, search, download, upsert."""
    # Load brand list
    brands_path = pathlib.Path(args.brands_file).resolve()
    if not brands_path.exists():
        logger.error("Brand list not found: %s", brands_path)
        sys.exit(1)
    with open(brands_path) as f:
        brands: list[str] = json.load(f).get("brands", [])
    if not brands:
        logger.error("Brand list is empty")
        sys.exit(1)
    logger.info("Loaded %d brands from %s", len(brands), brands_path)

    # Load config
    api_key = os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        logger.error("SERPER_API_KEY is not set")
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
    conn = None
    if not args.dry_run:
        logger.info("Connecting to database...")
        conn = psycopg.connect(database_url)

    image_sem = asyncio.Semaphore(10)

    total_fetched = 0
    total_skipped = 0
    total_inserted = 0
    total_updated = 0
    total_failed_images = 0
    total_dry_run_count = 0

    try:
        for brand in brands:
            query = f"{brand} menswear"
            logger.info("=== Brand: %r | Query: %r ===", brand, query)

            await asyncio.sleep(0.5)  # rate limit: 0.5s between API calls

            try:
                raw_results = await search_shopping(query, api_key)
            except Exception:
                logger.error("API error for brand=%r — skipping", brand, exc_info=True)
                continue

            logger.info("Fetched %d results for %r", len(raw_results), brand)
            total_fetched += len(raw_results)

            # Store raw JSON to S3 bronze layer
            if not args.dry_run and s3_client and bucket:
                store_bronze_json(raw_results, brand, s3_client, bucket)

            # Parse results
            parsed_items = []
            for raw in raw_results:
                item = parse_result(raw, brand=brand)
                if item is None:
                    total_skipped += 1
                    continue
                parsed_items.append((raw, item))

            logger.info(
                "Parsed %d valid items from %d results (skipped %d)",
                len(parsed_items),
                len(raw_results),
                len(raw_results) - len(parsed_items),
            )

            if args.dry_run:
                total_dry_run_count += len(parsed_items)
                logger.info(
                    "[DRY RUN] Would upsert %d items (running total: %d)",
                    len(parsed_items),
                    total_dry_run_count,
                )
                if args.max_items and total_dry_run_count >= args.max_items:
                    logger.info("Reached --max-items=%d — stopping", args.max_items)
                    return
                continue

            # Download images in parallel
            image_tasks = [
                download_image(
                    url=raw.get("imageUrl", ""),
                    item_id=item.item_id,
                    s3_client=s3_client,
                    bucket=bucket,
                    sem=image_sem,
                )
                for raw, item in parsed_items
            ]
            image_urls = await asyncio.gather(*image_tasks)

            # Attach S3 URLs and upsert
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
                "Upserted %d items (inserted=%d updated=%d) — total: %d",
                len(upsert_batch),
                inserted,
                updated,
                total_inserted + total_updated,
            )

            if args.max_items and (total_inserted + total_updated) >= args.max_items:
                logger.info("Reached --max-items=%d — stopping", args.max_items)
                return

    finally:
        if conn:
            conn.close()

    logger.info(
        "\n=== Ingestion complete ===\n"
        "  Fetched:         %d raw results\n"
        "  Skipped:         %d (parse failures / non-USD)\n"
        "  Inserted (new):  %d\n"
        "  Updated (dupe):  %d\n"
        "  Failed images:   %d",
        total_fetched,
        total_skipped,
        total_inserted,
        total_updated,
        total_failed_images,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest menswear brands from Google Shopping via Serper API."
    )
    parser.add_argument(
        "--brands-file",
        default="config/serper_brands.json",
        metavar="PATH",
        help="Path to brand list JSON (default: config/serper_brands.json).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and log without writing to S3 or the database.",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=0,
        metavar="N",
        help="Stop after N total items upserted (0 = unlimited).",
    )
    args = parser.parse_args()

    if args.dry_run:
        logger.info("=== DRY RUN MODE — no S3 writes or DB upserts ===")

    asyncio.run(ingest(args))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run all tests to confirm nothing broke**

```bash
PYTHONPATH=. pytest tests/test_ingest_serper_catalog.py -v
```

Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add scripts/ingest_serper_catalog.py
git commit -m "feat: add serper ingestion main loop and CLI"
```

---

### Task 8: Dry-run smoke test

**Files:** None (verification only)

- [ ] **Step 1: Run dry-run with one brand to confirm parsing works end-to-end**

```bash
PYTHONPATH=. SERPER_API_KEY=<your-key> python scripts/ingest_serper_catalog.py \
  --dry-run \
  --max-items 5 \
  --brands-file config/serper_brands.json
```

Expected output contains:
```
=== DRY RUN MODE — no S3 writes or DB upserts ===
Loaded 15 brands from ...
=== Brand: 'Acne Studios' | Query: 'Acne Studios menswear' ===
Fetched N results for 'Acne Studios'
Parsed N valid items from N results ...
[DRY RUN] Would upsert N items ...
```

- [ ] **Step 2: Run the full unit test suite to confirm no regressions**

```bash
PYTHONPATH=. pytest tests/ -v --ignore=tests/test_integration 2>&1 | tail -20
```

Expected: All pre-existing tests PASS.

- [ ] **Step 3: Commit if any fixes were needed, otherwise done**

```bash
git add -p  # stage only relevant changes
git commit -m "fix: serper ingestion smoke test corrections"
```
