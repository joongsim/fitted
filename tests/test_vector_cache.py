"""Tests for app/services/vector_cache.py."""

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.models.item import Item
from app.services.vector_cache import _dict_to_item, _item_to_dict

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DIM = 512
_UNIT_VEC = np.ones(_DIM, dtype=np.float32) / np.sqrt(_DIM)


def _make_item(**overrides) -> Item:
    defaults = dict(
        item_id="item-1",
        domain="fashion",
        title="Navy blazer",
        price=45.0,
        image_url="https://example.com/img.jpg",
        product_url="https://poshmark.com/listing/abc",
        source="poshmark_seed",
        embedding=None,
        attributes={"brand": "Zara"},
    )
    defaults.update(overrides)
    return Item(**defaults)


def _make_mock_conn(fetchone_return=None, fetchall_return=None):
    """Build a mock async psycopg3 connection + cursor."""
    mock_cur = AsyncMock()
    mock_cur.fetchone = AsyncMock(return_value=fetchone_return)
    mock_cur.fetchall = AsyncMock(return_value=fetchall_return or [])
    mock_cur.execute = AsyncMock()

    mock_cur_ctx = MagicMock()
    mock_cur_ctx.__aenter__ = AsyncMock(return_value=mock_cur)
    mock_cur_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_conn = MagicMock()
    mock_conn.cursor = MagicMock(return_value=mock_cur_ctx)  # sync call!
    mock_conn.commit = AsyncMock()
    mock_conn.rollback = AsyncMock()

    return mock_conn, mock_cur


@asynccontextmanager
async def _mock_get_connection(mock_conn):
    yield mock_conn


def _patch_cache_conn(mock_conn):
    return patch(
        "app.services.vector_cache.get_connection",
        return_value=_mock_get_connection(mock_conn),
    )


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


class TestItemToDict:
    def test_round_trip_without_embedding(self):
        item = _make_item()
        d = _item_to_dict(item)
        assert d["item_id"] == "item-1"
        assert d["embedding"] is None
        assert d["attributes"] == {"brand": "Zara"}

    def test_round_trip_with_embedding(self):
        emb = _UNIT_VEC.copy()
        item = _make_item(embedding=emb)
        d = _item_to_dict(item)
        assert isinstance(d["embedding"], list)
        assert len(d["embedding"]) == _DIM

    def test_embedding_values_preserved(self):
        emb = np.arange(_DIM, dtype=np.float32)
        emb /= np.linalg.norm(emb)
        item = _make_item(embedding=emb)
        d = _item_to_dict(item)
        np.testing.assert_allclose(d["embedding"], emb.tolist(), rtol=1e-5)


class TestDictToItem:
    def test_reconstructs_item_without_embedding(self):
        d = {
            "item_id": "abc",
            "domain": "fashion",
            "title": "T-shirt",
            "price": 15.0,
            "image_url": "",
            "product_url": "https://poshmark.com/abc",
            "source": "poshmark_seed",
            "embedding": None,
            "attributes": {},
        }
        item = _dict_to_item(d)
        assert item.item_id == "abc"
        assert item.embedding is None

    def test_reconstructs_embedding_as_float32_ndarray(self):
        vec = _UNIT_VEC.tolist()
        d = {
            "item_id": "e1",
            "domain": "fashion",
            "title": "",
            "price": 0.0,
            "embedding": vec,
            "attributes": {},
        }
        item = _dict_to_item(d)
        assert isinstance(item.embedding, np.ndarray)
        assert item.embedding.shape == (_DIM,)
        assert item.embedding.dtype == np.float32

    def test_full_round_trip(self):
        emb = _UNIT_VEC.copy()
        original = _make_item(embedding=emb, attributes={"brand": "Nike", "size": "M"})
        reconstructed = _dict_to_item(_item_to_dict(original))

        assert reconstructed.item_id == original.item_id
        assert reconstructed.title == original.title
        assert reconstructed.price == original.price
        assert reconstructed.attributes == original.attributes
        np.testing.assert_allclose(
            reconstructed.embedding, original.embedding, rtol=1e-5
        )


# ---------------------------------------------------------------------------
# lookup — MISS path
# ---------------------------------------------------------------------------


class TestLookup:
    async def test_returns_none_on_miss(self):
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=None)

        with _patch_cache_conn(mock_conn):
            from app.services.vector_cache import lookup

            result = await lookup(_UNIT_VEC)

        assert result is None

    async def test_executes_query_with_embedding_and_threshold(self):
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=None)

        with _patch_cache_conn(mock_conn):
            from app.services.vector_cache import lookup

            await lookup(_UNIT_VEC, threshold=0.20)

        mock_cur.execute.assert_called_once()
        call_args = mock_cur.execute.call_args
        sql, params = call_args[0]
        assert "<=" in sql or "<" in sql  # cosine distance filter present
        assert params[2] == 0.20  # custom threshold passed through

    async def test_returns_none_when_s3_load_fails(self):
        mock_conn, mock_cur = _make_mock_conn(
            fetchone_return=("cache-id-123", "cache/query/abc.json", 0.05)
        )

        with _patch_cache_conn(mock_conn):
            with patch(
                "app.services.vector_cache._load_items_from_s3",
                new=AsyncMock(return_value=None),
            ):
                from app.services.vector_cache import lookup

                result = await lookup(_UNIT_VEC)

        assert result is None

    async def test_returns_items_and_cache_id_on_hit(self):
        items = [_make_item(item_id="hit-item")]
        mock_conn, mock_cur = _make_mock_conn(
            fetchone_return=("cache-id-abc", "cache/query/abc.json", 0.08)
        )

        with _patch_cache_conn(mock_conn):
            with patch(
                "app.services.vector_cache._load_items_from_s3",
                new=AsyncMock(return_value=items),
            ):
                from app.services.vector_cache import lookup

                result = await lookup(_UNIT_VEC)

        assert result is not None
        returned_items, cache_id = result
        assert returned_items == items
        assert cache_id == "cache-id-abc"


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------


class TestStore:
    def _make_s3(self) -> MagicMock:
        mock_s3 = MagicMock()
        mock_s3.put_object.return_value = {}
        return mock_s3

    async def test_calls_put_object_with_correct_bucket_and_key_prefix(self):
        mock_conn, _ = _make_mock_conn()
        mock_s3 = self._make_s3()

        with _patch_cache_conn(mock_conn):
            from app.services.vector_cache import store

            await store(
                query_text="navy blazer",
                query_embedding=_UNIT_VEC,
                items=[_make_item()],
                s3_client=mock_s3,
                bucket="test-bucket",
            )

        mock_s3.put_object.assert_called_once()
        kwargs = mock_s3.put_object.call_args.kwargs
        assert kwargs["Bucket"] == "test-bucket"
        assert kwargs["Key"].startswith("cache/query/")
        assert kwargs["Key"].endswith(".json")

    async def test_s3_payload_is_valid_json_list(self):
        mock_conn, _ = _make_mock_conn()
        mock_s3 = self._make_s3()

        with _patch_cache_conn(mock_conn):
            from app.services.vector_cache import store

            await store(
                query_text="linen shirt",
                query_embedding=_UNIT_VEC,
                items=[_make_item(item_id="a"), _make_item(item_id="b")],
                s3_client=mock_s3,
                bucket="test-bucket",
            )

        body_bytes = mock_s3.put_object.call_args.kwargs["Body"]
        payload = json.loads(body_bytes.decode("utf-8"))
        assert isinstance(payload, list)
        assert len(payload) == 2
        assert payload[0]["item_id"] == "a"

    async def test_returns_cache_id_string_on_success(self):
        mock_conn, _ = _make_mock_conn()
        mock_s3 = self._make_s3()

        with _patch_cache_conn(mock_conn):
            from app.services.vector_cache import store

            result = await store(
                query_text="blue chinos",
                query_embedding=_UNIT_VEC,
                items=[_make_item()],
                s3_client=mock_s3,
                bucket="test-bucket",
            )

        assert isinstance(result, str)
        assert len(result) == 36  # UUID string length

    async def test_returns_none_when_s3_put_fails(self):
        mock_conn, _ = _make_mock_conn()
        mock_s3 = MagicMock()
        mock_s3.put_object.side_effect = Exception("S3 error")

        with _patch_cache_conn(mock_conn):
            from app.services.vector_cache import store

            result = await store(
                query_text="any query",
                query_embedding=_UNIT_VEC,
                items=[_make_item()],
                s3_client=mock_s3,
                bucket="test-bucket",
            )

        assert result is None

    async def test_returns_none_when_db_upsert_fails(self):
        mock_conn, mock_cur = _make_mock_conn()
        mock_cur.execute.side_effect = Exception("DB error")
        mock_s3 = self._make_s3()

        with _patch_cache_conn(mock_conn):
            from app.services.vector_cache import store

            result = await store(
                query_text="any query",
                query_embedding=_UNIT_VEC,
                items=[_make_item()],
                s3_client=mock_s3,
                bucket="test-bucket",
            )

        assert result is None

    async def test_db_upsert_includes_query_text_and_embedding(self):
        mock_conn, mock_cur = _make_mock_conn()
        mock_s3 = self._make_s3()

        with _patch_cache_conn(mock_conn):
            from app.services.vector_cache import store

            await store(
                query_text="streetwear jacket",
                query_embedding=_UNIT_VEC,
                items=[_make_item()],
                s3_client=mock_s3,
                bucket="test-bucket",
            )

        mock_cur.execute.assert_called_once()
        sql, params = mock_cur.execute.call_args[0]
        assert "INSERT INTO query_cache" in sql
        assert "ON CONFLICT" in sql
        assert params[2] == "streetwear jacket"  # query_text
        assert isinstance(params[3], list)  # embedding as list
        assert len(params[3]) == _DIM

    async def test_store_then_lookup_uses_same_hash_for_dedup(self):
        """Same query text produces the same SHA-256 hash — second store refreshes TTL."""
        import hashlib

        query = "grey cashmere sweater"
        expected_hash = hashlib.sha256(query.encode()).hexdigest()

        mock_conn, mock_cur = _make_mock_conn()
        mock_s3 = self._make_s3()

        with _patch_cache_conn(mock_conn):
            from app.services.vector_cache import store

            await store(
                query_text=query,
                query_embedding=_UNIT_VEC,
                items=[_make_item()],
                s3_client=mock_s3,
                bucket="test-bucket",
            )

        _, params = mock_cur.execute.call_args[0]
        assert params[1] == expected_hash  # query_hash is second positional param

    async def test_empty_item_list_stores_empty_json_array(self):
        mock_conn, _ = _make_mock_conn()
        mock_s3 = self._make_s3()

        with _patch_cache_conn(mock_conn):
            from app.services.vector_cache import store

            await store(
                query_text="empty",
                query_embedding=_UNIT_VEC,
                items=[],
                s3_client=mock_s3,
                bucket="test-bucket",
            )

        body_bytes = mock_s3.put_object.call_args.kwargs["Body"]
        assert json.loads(body_bytes.decode("utf-8")) == []
