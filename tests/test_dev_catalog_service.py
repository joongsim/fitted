"""Tests for app/services/dev_catalog_service.py and app/services/candidate_source.py."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.models.item import Item

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DIM = 512
_UNIT_VEC = np.ones(_DIM, dtype=np.float32) / np.sqrt(_DIM)


def _make_mock_conn(fetchall_return=None):
    mock_cur = AsyncMock()
    mock_cur.fetchall = AsyncMock(return_value=fetchall_return or [])
    mock_cur.execute = AsyncMock()

    mock_cur_ctx = MagicMock()
    mock_cur_ctx.__aenter__ = AsyncMock(return_value=mock_cur)
    mock_cur_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_conn = MagicMock()
    mock_conn.cursor = MagicMock(return_value=mock_cur_ctx)

    return mock_conn, mock_cur


@asynccontextmanager
async def _mock_get_connection(mock_conn):
    yield mock_conn


def _patch_conn(
    mock_conn, target: str = "app.services.dev_catalog_service.get_connection"
):
    return patch(target, return_value=_mock_get_connection(mock_conn))


def _catalog_row(
    item_id="item-1",
    domain="fashion",
    title="Navy blazer",
    price=45.0,
    image_url="https://example.com/img.jpg",
    product_url="https://poshmark.com/listing/abc",
    source="poshmark_seed",
    embedding=None,
    attributes=None,
    cosine_distance=0.1,
):
    return (
        item_id,
        domain,
        title,
        price,
        image_url,
        product_url,
        source,
        embedding,
        attributes or {},
        cosine_distance,
    )


# ---------------------------------------------------------------------------
# dev_catalog_service.search — primary (vector) path
# ---------------------------------------------------------------------------


class TestDevCatalogSearch:
    async def test_returns_items_from_primary_path(self):
        rows = [
            _catalog_row(item_id="a", title="Blazer", cosine_distance=0.05),
            _catalog_row(item_id="b", title="Chinos", cosine_distance=0.12),
        ]
        mock_conn, mock_cur = _make_mock_conn(fetchall_return=rows)

        with _patch_conn(mock_conn):
            from app.services.dev_catalog_service import search

            result = await search(_UNIT_VEC, limit=10)

        assert len(result) == 2
        assert result[0].item_id == "a"
        assert result[1].item_id == "b"

    async def test_items_are_item_dataclass_instances(self):
        rows = [_catalog_row()]
        mock_conn, _ = _make_mock_conn(fetchall_return=rows)

        with _patch_conn(mock_conn):
            from app.services.dev_catalog_service import search

            result = await search(_UNIT_VEC)

        assert isinstance(result[0], Item)

    async def test_passes_embedding_as_list_to_query(self):
        mock_conn, mock_cur = _make_mock_conn(fetchall_return=[_catalog_row()])

        with _patch_conn(mock_conn):
            from app.services.dev_catalog_service import search

            await search(_UNIT_VEC, domain="fashion")

        first_call_args = mock_cur.execute.call_args_list[0]
        _, params = first_call_args[0]
        assert isinstance(params[0], list)
        assert len(params[0]) == _DIM

    async def test_passes_domain_to_query(self):
        mock_conn, mock_cur = _make_mock_conn(fetchall_return=[_catalog_row()])

        with _patch_conn(mock_conn):
            from app.services.dev_catalog_service import search

            await search(_UNIT_VEC, domain="furniture")

        _, params = mock_cur.execute.call_args_list[0][0]
        assert params[1] == "furniture"

    async def test_passes_limit_to_query(self):
        mock_conn, mock_cur = _make_mock_conn(fetchall_return=[_catalog_row()])

        with _patch_conn(mock_conn):
            from app.services.dev_catalog_service import search

            await search(_UNIT_VEC, limit=25)

        _, params = mock_cur.execute.call_args_list[0][0]
        assert params[2] == 25

    async def test_returns_empty_list_when_no_rows(self):
        mock_conn, _ = _make_mock_conn(fetchall_return=[])
        # Second fetchall (fallback) also empty
        mock_conn.cursor.return_value.__aenter__.return_value.fetchall = AsyncMock(
            return_value=[]
        )

        with _patch_conn(mock_conn):
            from app.services.dev_catalog_service import search

            result = await search(_UNIT_VEC)

        assert result == []

    async def test_parses_numpy_embedding_from_row(self):
        emb = _UNIT_VEC.tolist()
        rows = [_catalog_row(embedding=emb)]
        mock_conn, _ = _make_mock_conn(fetchall_return=rows)

        with _patch_conn(mock_conn):
            from app.services.dev_catalog_service import search

            result = await search(_UNIT_VEC)

        assert isinstance(result[0].embedding, np.ndarray)
        assert result[0].embedding.shape == (_DIM,)
        assert result[0].embedding.dtype == np.float32

    async def test_null_embedding_in_row_stays_none(self):
        rows = [_catalog_row(embedding=None)]
        mock_conn, _ = _make_mock_conn(fetchall_return=rows)

        with _patch_conn(mock_conn):
            from app.services.dev_catalog_service import search

            result = await search(_UNIT_VEC)

        assert result[0].embedding is None

    async def test_defaults_to_zero_price_when_none(self):
        rows = [_catalog_row(price=None)]
        mock_conn, _ = _make_mock_conn(fetchall_return=rows)

        with _patch_conn(mock_conn):
            from app.services.dev_catalog_service import search

            result = await search(_UNIT_VEC)

        assert result[0].price == 0.0


# ---------------------------------------------------------------------------
# dev_catalog_service.search — fallback (recency) path
# ---------------------------------------------------------------------------


class TestDevCatalogFallback:
    async def test_falls_back_to_recency_when_no_embedded_rows(self):
        """When primary ANN returns empty, a second query should be executed."""
        fallback_row = _catalog_row(item_id="fallback", cosine_distance=None)
        mock_conn, mock_cur = _make_mock_conn()
        # First call (ANN): empty; second call (fallback): one row
        mock_cur.fetchall = AsyncMock(side_effect=[[], [fallback_row]])

        with _patch_conn(mock_conn):
            from app.services.dev_catalog_service import search

            result = await search(_UNIT_VEC)

        assert mock_cur.execute.call_count == 2
        assert result[0].item_id == "fallback"

    async def test_fallback_query_uses_last_seen_order(self):
        mock_conn, mock_cur = _make_mock_conn()
        mock_cur.fetchall = AsyncMock(side_effect=[[], []])

        with _patch_conn(mock_conn):
            from app.services.dev_catalog_service import search

            await search(_UNIT_VEC)

        second_sql = mock_cur.execute.call_args_list[1][0][0]
        assert "last_seen" in second_sql.lower()


# ---------------------------------------------------------------------------
# candidate_source.get_candidates
# ---------------------------------------------------------------------------


class TestCandidateSource:
    async def test_dev_mode_routes_to_dev_catalog(self, monkeypatch):
        monkeypatch.setenv("DEV_MODE", "true")
        items = [
            Item(
                item_id="x",
                domain="fashion",
                title="T",
                price=10.0,
                image_url="",
                product_url="",
                source="poshmark_seed",
                embedding=None,
            )
        ]

        with patch(
            "app.services.candidate_source.dev_catalog_service.search",
            new=AsyncMock(return_value=items),
        ):
            from app.services.candidate_source import get_candidates

            result = await get_candidates(_UNIT_VEC)

        assert result == items

    async def test_dev_mode_passes_limit_and_domain(self, monkeypatch):
        monkeypatch.setenv("DEV_MODE", "true")
        mock_search = AsyncMock(return_value=[])

        with patch(
            "app.services.candidate_source.dev_catalog_service.search",
            new=mock_search,
        ):
            from app.services.candidate_source import get_candidates

            await get_candidates(_UNIT_VEC, limit=30, domain="furniture")

        mock_search.assert_called_once_with(_UNIT_VEC, limit=30, domain="furniture")

    async def test_prod_mode_returns_empty_list(self, monkeypatch):
        monkeypatch.setenv("DEV_MODE", "false")

        from app.services.candidate_source import get_candidates

        result = await get_candidates(_UNIT_VEC)

        assert result == []

    async def test_dev_mode_false_skips_dev_catalog(self, monkeypatch):
        monkeypatch.setenv("DEV_MODE", "false")
        mock_search = AsyncMock(return_value=[])

        with patch(
            "app.services.candidate_source.dev_catalog_service.search",
            new=mock_search,
        ):
            from app.services.candidate_source import get_candidates

            await get_candidates(_UNIT_VEC)

        mock_search.assert_not_called()
