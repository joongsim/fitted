"""Tests for app/services/affiliate_service.py.

Covers:
- _rewrite_amazon: ASIN pattern matching, tag injection, non-Amazon passthrough
- _rewrite_shopstyle: domain matching, pid/uid injection
- _rewrite_rakuten: link build, None when mid missing
- rewrite_to_affiliate_url: network priority, original URL fallback
- detect_network: domain-to-network mapping
- record_affiliate_click: DB insert, returns click_id UUID
- resolve_and_record_click: marks clicked_at, returns URL; None when not found
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.affiliate_service import (
    _rewrite_amazon,
    _rewrite_rakuten,
    _rewrite_shopstyle,
    detect_network,
    record_affiliate_click,
    resolve_and_record_click,
    rewrite_to_affiliate_url,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AMAZON_URL = "https://www.amazon.com/dp/B09XYZ12345"
_SHOPSTYLE_URL = "https://www.shopstyle.com/p/product-name/123456"
_POSHMARK_URL = "https://poshmark.com/listing/item-123"

_PATCH_CONN = "app.services.affiliate_service.get_connection"


def _make_mock_conn(fetchone_return=None):
    mock_cur = AsyncMock()
    mock_cur.fetchone = AsyncMock(return_value=fetchone_return)
    mock_cur.execute = AsyncMock()

    mock_cur_ctx = MagicMock()
    mock_cur_ctx.__aenter__ = AsyncMock(return_value=mock_cur)
    mock_cur_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_conn = MagicMock()
    mock_conn.cursor = MagicMock(return_value=mock_cur_ctx)
    mock_conn.commit = AsyncMock()

    return mock_conn, mock_cur


@asynccontextmanager
async def _mock_get_connection(mock_conn):
    yield mock_conn


# ---------------------------------------------------------------------------
# _rewrite_amazon
# ---------------------------------------------------------------------------


class TestRewriteAmazon:
    def test_injects_affiliate_tag(self):
        result = _rewrite_amazon(_AMAZON_URL, "fitted-20")
        assert result is not None
        assert "tag=fitted-20" in result

    def test_preserves_path(self):
        result = _rewrite_amazon(_AMAZON_URL, "fitted-20")
        assert "/dp/B09XYZ12345" in result

    def test_replaces_existing_tag(self):
        url = "https://www.amazon.com/dp/B09XYZ12345?tag=old-20"
        result = _rewrite_amazon(url, "fitted-20")
        assert "tag=fitted-20" in result
        assert "tag=old-20" not in result

    def test_returns_none_for_non_amazon_url(self):
        result = _rewrite_amazon(_POSHMARK_URL, "fitted-20")
        assert result is None

    def test_returns_none_for_amazon_non_product_page(self):
        result = _rewrite_amazon("https://www.amazon.com/s?k=blazer", "fitted-20")
        assert result is None

    def test_handles_longer_amazon_path(self):
        url = "https://www.amazon.com/Product-Title-Here/dp/B01N5IB20Q/ref=sr_1_1"
        result = _rewrite_amazon(url, "fitted-20")
        assert result is not None
        assert "tag=fitted-20" in result


# ---------------------------------------------------------------------------
# _rewrite_shopstyle
# ---------------------------------------------------------------------------


class TestRewriteShopStyle:
    def test_injects_pid(self):
        result = _rewrite_shopstyle(_SHOPSTYLE_URL, "pub123")
        assert result is not None
        assert "pid=pub123" in result

    def test_adds_uid(self):
        result = _rewrite_shopstyle(_SHOPSTYLE_URL, "pub123")
        assert "uid=" in result

    def test_returns_none_for_non_shopstyle(self):
        result = _rewrite_shopstyle(_POSHMARK_URL, "pub123")
        assert result is None


# ---------------------------------------------------------------------------
# _rewrite_rakuten
# ---------------------------------------------------------------------------


class TestRewriteRakuten:
    def test_builds_linksynergy_url(self):
        result = _rewrite_rakuten(_POSHMARK_URL, site_id="12345", mid="98765")
        assert result is not None
        assert "click.linksynergy.com" in result
        assert "id=12345" in result
        assert "mid=98765" in result

    def test_encodes_original_url(self):
        result = _rewrite_rakuten(_POSHMARK_URL, site_id="12345", mid="98765")
        assert "poshmark.com" in result  # encoded in murl param

    def test_returns_none_when_mid_is_empty(self):
        result = _rewrite_rakuten(_POSHMARK_URL, site_id="12345", mid="")
        assert result is None


# ---------------------------------------------------------------------------
# rewrite_to_affiliate_url
# ---------------------------------------------------------------------------


class TestRewriteToAffiliateUrl:
    def test_rewrites_amazon_when_tag_set(self):
        result = rewrite_to_affiliate_url(_AMAZON_URL, amazon_tag="fitted-20")
        assert "tag=fitted-20" in result

    def test_rewrites_shopstyle_when_pid_set(self):
        result = rewrite_to_affiliate_url(_SHOPSTYLE_URL, shopstyle_pid="pub123")
        assert "pid=pub123" in result

    def test_returns_original_when_no_network_configured(self):
        result = rewrite_to_affiliate_url(_POSHMARK_URL)
        assert result == _POSHMARK_URL

    def test_amazon_takes_priority_over_shopstyle(self):
        # Amazon URL should be rewritten via amazon, not shopstyle
        result = rewrite_to_affiliate_url(
            _AMAZON_URL, amazon_tag="fitted-20", shopstyle_pid="pub123"
        )
        assert "tag=fitted-20" in result
        assert "pid=" not in result

    def test_falls_through_to_original_when_no_match(self):
        result = rewrite_to_affiliate_url(
            _POSHMARK_URL, amazon_tag="fitted-20", shopstyle_pid="pub123"
        )
        assert result == _POSHMARK_URL


# ---------------------------------------------------------------------------
# detect_network
# ---------------------------------------------------------------------------


class TestDetectNetwork:
    def test_amazon_detection(self):
        assert detect_network("https://www.amazon.com/dp/B09XYZ12345") == "amazon"

    def test_shopstyle_detection(self):
        assert detect_network("https://www.shopstyle.com/p/item/123") == "shopstyle"

    def test_rakuten_detection(self):
        assert detect_network("https://click.linksynergy.com/deeplink?...") == "rakuten"

    def test_unknown_returns_none(self):
        assert detect_network("https://poshmark.com/listing/item") == "none"


# ---------------------------------------------------------------------------
# record_affiliate_click (async)
# ---------------------------------------------------------------------------


class TestRecordAffiliateClick:
    @pytest.mark.asyncio
    async def test_returns_uuid_string(self):
        mock_conn, mock_cur = _make_mock_conn()
        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            click_id = await record_affiliate_click(
                user_id="user-1",
                item_id="item-1",
                original_url=_POSHMARK_URL,
                affiliate_url=_POSHMARK_URL,
                network="none",
            )

        # Should be a valid UUID string
        import uuid

        uuid.UUID(click_id)  # raises if not valid

    @pytest.mark.asyncio
    async def test_inserts_correct_values(self):
        mock_conn, mock_cur = _make_mock_conn()
        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            await record_affiliate_click(
                user_id="user-1",
                item_id="item-1",
                original_url=_POSHMARK_URL,
                affiliate_url=_AMAZON_URL,
                network="amazon",
            )

        mock_cur.execute.assert_awaited_once()
        params = mock_cur.execute.call_args[0][1]
        assert "user-1" in params
        assert "item-1" in params
        assert _POSHMARK_URL in params
        assert _AMAZON_URL in params
        assert "amazon" in params


# ---------------------------------------------------------------------------
# resolve_and_record_click (async)
# ---------------------------------------------------------------------------


class TestResolveAndRecordClick:
    @pytest.mark.asyncio
    async def test_returns_affiliate_url_when_found(self):
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=(_AMAZON_URL,))
        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            result = await resolve_and_record_click("some-click-id")

        assert result == _AMAZON_URL

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=None)
        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            result = await resolve_and_record_click("nonexistent-id")

        assert result is None

    @pytest.mark.asyncio
    async def test_commits_when_row_found(self):
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=(_AMAZON_URL,))
        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            await resolve_and_record_click("some-click-id")

        mock_conn.commit.assert_awaited_once()
