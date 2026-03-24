"""Tests for app/services/wardrobe_service.py.

All database I/O is mocked using the same async psycopg3 pattern established
in test_recommendation_service.py — a MagicMock connection with an async cursor
context manager.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.services import wardrobe_service

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_USER_ID = "00000000-0000-0000-0000-000000000001"
_ITEM_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _make_mock_conn(fetchone_return=None, fetchall_return=None, rowcount=1):
    """Build a mock async psycopg3 connection + cursor."""
    mock_cur = AsyncMock()
    mock_cur.fetchone = AsyncMock(return_value=fetchone_return)
    mock_cur.fetchall = AsyncMock(return_value=fetchall_return or [])
    mock_cur.execute = AsyncMock()
    mock_cur.rowcount = rowcount

    mock_cur_ctx = MagicMock()
    mock_cur_ctx.__aenter__ = AsyncMock(return_value=mock_cur)
    mock_cur_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_conn = MagicMock()
    mock_conn.cursor = MagicMock(return_value=mock_cur_ctx)  # sync call!
    mock_conn.commit = AsyncMock()

    return mock_conn, mock_cur


@asynccontextmanager
async def _mock_get_connection(mock_conn):
    yield mock_conn


_PATCH_CONN = "app.services.wardrobe_service.get_connection"


# ---------------------------------------------------------------------------
# create_wardrobe_item
# ---------------------------------------------------------------------------


class TestCreateWardrobeItem:
    @pytest.mark.asyncio
    async def test_returns_dict_with_expected_fields(self):
        row = (
            _ITEM_ID,
            "Navy Blazer",
            "outerwear",
            "wardrobe-images/u/i.jpg",
            ["navy"],
            _NOW,
            "pending",
        )
        mock_conn, _ = _make_mock_conn(fetchone_return=row)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            result = await wardrobe_service.create_wardrobe_item(
                user_id=_USER_ID,
                name="Navy Blazer",
                category="outerwear",
                image_s3_key="wardrobe-images/u/i.jpg",
            )

        assert result["item_id"] == str(_ITEM_ID)
        assert result["name"] == "Navy Blazer"
        assert result["category"] == "outerwear"
        assert result["image_s3_key"] == "wardrobe-images/u/i.jpg"
        assert result["tags"] == ["navy"]
        assert result["created_at"] == _NOW
        assert result["embedding_status"] == "pending"

    @pytest.mark.asyncio
    async def test_commits_transaction(self):
        row = (_ITEM_ID, "T-Shirt", None, None, [], _NOW, "pending")
        mock_conn, _ = _make_mock_conn(fetchone_return=row)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            await wardrobe_service.create_wardrobe_item(
                user_id=_USER_ID, name="T-Shirt", category=None, image_s3_key=None
            )

        mock_conn.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_null_tags_returns_empty_list(self):
        row = (_ITEM_ID, "Item", None, None, None, _NOW, "pending")
        mock_conn, _ = _make_mock_conn(fetchone_return=row)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            result = await wardrobe_service.create_wardrobe_item(
                user_id=_USER_ID, name="Item", category=None, image_s3_key=None
            )

        assert result["tags"] == []

    @pytest.mark.asyncio
    async def test_executes_insert_with_correct_params(self):
        row = (_ITEM_ID, "Jeans", "bottoms", None, [], _NOW, "pending")
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=row)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            await wardrobe_service.create_wardrobe_item(
                user_id=_USER_ID, name="Jeans", category="bottoms", image_s3_key=None
            )

        mock_cur.execute.assert_awaited_once()
        call_args = mock_cur.execute.call_args[0]
        params = call_args[1]
        assert params[0] == _USER_ID
        assert params[1] == "Jeans"
        assert params[2] == "bottoms"
        assert params[3] is None


# ---------------------------------------------------------------------------
# get_wardrobe_items
# ---------------------------------------------------------------------------


class TestGetWardrobeItems:
    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self):
        rows = [
            (_ITEM_ID, "Blazer", "outerwear", "s3/key.jpg", ["navy"], _NOW, "pending"),
            (uuid.uuid4(), "Jeans", "bottoms", None, [], _NOW, "pending"),
        ]
        mock_conn, _ = _make_mock_conn(fetchall_return=rows)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            result = await wardrobe_service.get_wardrobe_items(_USER_ID)

        assert len(result) == 2
        assert result[0]["name"] == "Blazer"
        assert result[0]["item_id"] == str(_ITEM_ID)
        assert result[1]["image_s3_key"] is None

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_items(self):
        mock_conn, _ = _make_mock_conn(fetchall_return=[])

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            result = await wardrobe_service.get_wardrobe_items(_USER_ID)

        assert result == []

    @pytest.mark.asyncio
    async def test_null_tags_normalised_to_empty_list(self):
        rows = [(_ITEM_ID, "Shirt", None, None, None, _NOW, "pending")]
        mock_conn, _ = _make_mock_conn(fetchall_return=rows)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            result = await wardrobe_service.get_wardrobe_items(_USER_ID)

        assert result[0]["tags"] == []


# ---------------------------------------------------------------------------
# get_wardrobe_item
# ---------------------------------------------------------------------------


class TestGetWardrobeItem:
    @pytest.mark.asyncio
    async def test_found_returns_dict(self):
        row = (_ITEM_ID, "Blazer", "outerwear", "s3/key.jpg", ["navy"], _NOW, "pending")
        mock_conn, _ = _make_mock_conn(fetchone_return=row)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            result = await wardrobe_service.get_wardrobe_item(_USER_ID, str(_ITEM_ID))

        assert result is not None
        assert result["item_id"] == str(_ITEM_ID)
        assert result["name"] == "Blazer"

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        mock_conn, _ = _make_mock_conn(fetchone_return=None)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            result = await wardrobe_service.get_wardrobe_item(
                _USER_ID, "nonexistent-id"
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_query_filters_by_user_id(self):
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=None)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            await wardrobe_service.get_wardrobe_item(_USER_ID, str(_ITEM_ID))

        mock_cur.execute.assert_awaited_once()
        params = mock_cur.execute.call_args[0][1]
        assert str(_ITEM_ID) in params
        assert _USER_ID in params


# ---------------------------------------------------------------------------
# delete_wardrobe_item
# ---------------------------------------------------------------------------


class TestDeleteWardrobeItem:
    @pytest.mark.asyncio
    async def test_returns_true_when_row_deleted(self):
        mock_conn, _ = _make_mock_conn(rowcount=1)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            result = await wardrobe_service.delete_wardrobe_item(
                _USER_ID, str(_ITEM_ID)
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_not_found(self):
        mock_conn, _ = _make_mock_conn(rowcount=0)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            result = await wardrobe_service.delete_wardrobe_item(_USER_ID, "bad-id")

        assert result is False

    @pytest.mark.asyncio
    async def test_commits_after_delete(self):
        mock_conn, _ = _make_mock_conn(rowcount=1)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            await wardrobe_service.delete_wardrobe_item(_USER_ID, str(_ITEM_ID))

        mock_conn.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_where_clause_includes_user_id(self):
        mock_conn, mock_cur = _make_mock_conn(rowcount=0)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            await wardrobe_service.delete_wardrobe_item(_USER_ID, str(_ITEM_ID))

        params = mock_cur.execute.call_args[0][1]
        # Both item_id and user_id must appear — ownership check
        assert str(_ITEM_ID) in params
        assert _USER_ID in params


# ---------------------------------------------------------------------------
# update_wardrobe_item
# ---------------------------------------------------------------------------


class TestUpdateWardrobeItem:
    @pytest.mark.asyncio
    async def test_returns_updated_dict(self):
        row = (_ITEM_ID, "Updated Blazer", "tops", "s3/key.jpg", ["blue"], _NOW, "pending")
        mock_conn, _ = _make_mock_conn(fetchone_return=row)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            result = await wardrobe_service.update_wardrobe_item(
                user_id=_USER_ID,
                item_id=str(_ITEM_ID),
                name="Updated Blazer",
                category="tops",
                tags=["blue"],
            )

        assert result is not None
        assert result["item_id"] == str(_ITEM_ID)
        assert result["name"] == "Updated Blazer"
        assert result["category"] == "tops"
        assert result["tags"] == ["blue"]
        assert result["embedding_status"] == "pending"

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        mock_conn, _ = _make_mock_conn(fetchone_return=None)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            result = await wardrobe_service.update_wardrobe_item(
                user_id=_USER_ID,
                item_id="nonexistent",
                name="X",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_commits_transaction(self):
        row = (_ITEM_ID, "Blazer", "outerwear", None, [], _NOW, "pending")
        mock_conn, _ = _make_mock_conn(fetchone_return=row)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            await wardrobe_service.update_wardrobe_item(
                user_id=_USER_ID, item_id=str(_ITEM_ID), name="Blazer"
            )

        mock_conn.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_where_clause_includes_user_id_and_executes_update(self):
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=None)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            await wardrobe_service.update_wardrobe_item(
                user_id=_USER_ID, item_id=str(_ITEM_ID), name="X"
            )

        mock_cur.execute.assert_awaited_once()
        call_sql, params = mock_cur.execute.call_args[0]
        # Ownership check: both IDs must appear in the WHERE clause parameters
        assert str(_ITEM_ID) in params
        assert _USER_ID in params
        # Must be an UPDATE, not a SELECT (distinguishes from the empty-body fallback path)
        assert "UPDATE" in call_sql

    @pytest.mark.asyncio
    async def test_empty_body_raises_value_error(self):
        """All-None fields must raise ValueError — callers surface this as HTTP 422."""
        mock_conn, mock_cur = _make_mock_conn()

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            with pytest.raises(ValueError, match="At least one field"):
                await wardrobe_service.update_wardrobe_item(
                    user_id=_USER_ID,
                    item_id=str(_ITEM_ID),
                    # name, category, tags all default to None
                )

        # No DB query should have been executed
        mock_cur.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# embed_wardrobe_item
# ---------------------------------------------------------------------------

class TestEmbedWardrobeItem:
    _ITEM_ID_STR = str(_ITEM_ID)
    _S3_KEY = "wardrobe-images/user/item.jpg"

    def _make_executor_patch(self, vec, raise_exc=None):
        """
        Patch asyncio.get_running_loop so run_in_executor calls encode_image
        synchronously and returns vec (or raises raise_exc).
        """
        mock_loop = MagicMock()
        if raise_exc:
            async def fake_executor(executor, fn, *args):
                raise raise_exc
        else:
            async def fake_executor(executor, fn, *args):
                return vec
        mock_loop.run_in_executor = fake_executor
        return patch("asyncio.get_running_loop", return_value=mock_loop)

    @pytest.mark.asyncio
    async def test_success_sets_embedding_and_done_status(self):
        import numpy as np
        vec = np.ones(512, dtype=np.float32)
        vec /= np.linalg.norm(vec)

        conn1, cur1 = _make_mock_conn()  # SET embedding
        conn2, cur2 = _make_mock_conn()  # SET done
        call_iter = [_mock_get_connection(conn1), _mock_get_connection(conn2)]
        idx = [-1]
        def next_conn():
            idx[0] += 1
            return call_iter[idx[0]]

        with patch(_PATCH_CONN, side_effect=next_conn), \
             self._make_executor_patch(vec):
            await wardrobe_service.embed_wardrobe_item(self._ITEM_ID_STR, self._S3_KEY)

        # First DB call: SET embedding_status = 'embedding'
        sql1, params1 = cur1.execute.call_args[0]
        assert "embedding_status" in sql1
        assert "'embedding'" in sql1
        conn1.commit.assert_awaited_once()

        # Second DB call: SET embedding + done
        sql2, params2 = cur2.execute.call_args[0]
        assert "embedding_status" in sql2
        assert "done" in sql2
        assert "::vector" in sql2
        conn2.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failure_sets_failed_status_and_reraises(self):
        conn1, cur1 = _make_mock_conn()  # SET embedding
        conn2, cur2 = _make_mock_conn()  # SET failed
        call_iter = [_mock_get_connection(conn1), _mock_get_connection(conn2)]
        idx = [-1]
        def next_conn():
            idx[0] += 1
            return call_iter[idx[0]]

        exc = RuntimeError("CLIP exploded")
        with patch(_PATCH_CONN, side_effect=next_conn), \
             self._make_executor_patch(None, raise_exc=exc):
            with pytest.raises(RuntimeError, match="CLIP exploded"):
                await wardrobe_service.embed_wardrobe_item(self._ITEM_ID_STR, self._S3_KEY)

        # Cleanup DB call: SET embedding_status = 'failed'
        sql2, _ = cur2.execute.call_args[0]
        assert "failed" in sql2
        conn2.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup_error_is_swallowed_original_reraises(self):
        """If the failed-status UPDATE itself throws, original exception still propagates."""
        conn1, _ = _make_mock_conn()

        bad_conn = MagicMock()
        bad_conn.commit = AsyncMock(side_effect=Exception("DB gone"))
        bad_cur = AsyncMock()
        bad_cur.execute = AsyncMock()
        bad_cur_ctx = MagicMock()
        bad_cur_ctx.__aenter__ = AsyncMock(return_value=bad_cur)
        bad_cur_ctx.__aexit__ = AsyncMock(return_value=False)
        bad_conn.cursor = MagicMock(return_value=bad_cur_ctx)

        @asynccontextmanager
        async def _bad_conn_ctx():
            yield bad_conn

        call_iter = [_mock_get_connection(conn1), _bad_conn_ctx()]
        idx = [-1]
        def next_conn():
            idx[0] += 1
            return call_iter[idx[0]]

        original_exc = RuntimeError("encode failed")
        with patch(_PATCH_CONN, side_effect=next_conn), \
             self._make_executor_patch(None, raise_exc=original_exc):
            with pytest.raises(RuntimeError, match="encode failed"):
                await wardrobe_service.embed_wardrobe_item(self._ITEM_ID_STR, self._S3_KEY)


# ---------------------------------------------------------------------------
# get_wardrobe_item_status
# ---------------------------------------------------------------------------

class TestGetWardrobeItemStatus:
    @pytest.mark.asyncio
    async def test_found_returns_status_string(self):
        mock_conn, _ = _make_mock_conn(fetchone_return=("done",))

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            result = await wardrobe_service.get_wardrobe_item_status(
                _USER_ID, str(_ITEM_ID)
            )

        assert result == "done"

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        mock_conn, _ = _make_mock_conn(fetchone_return=None)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            result = await wardrobe_service.get_wardrobe_item_status(
                _USER_ID, "nonexistent-id"
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_query_filters_by_both_item_id_and_user_id(self):
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=None)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            await wardrobe_service.get_wardrobe_item_status(_USER_ID, str(_ITEM_ID))

        params = mock_cur.execute.call_args[0][1]
        assert str(_ITEM_ID) in params
        assert _USER_ID in params
