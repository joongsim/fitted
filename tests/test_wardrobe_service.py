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

    @pytest.mark.asyncio
    async def test_commits_transaction(self):
        row = (_ITEM_ID, "T-Shirt", None, None, [], _NOW)
        mock_conn, _ = _make_mock_conn(fetchone_return=row)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            await wardrobe_service.create_wardrobe_item(
                user_id=_USER_ID, name="T-Shirt", category=None, image_s3_key=None
            )

        mock_conn.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_null_tags_returns_empty_list(self):
        row = (_ITEM_ID, "Item", None, None, None, _NOW)
        mock_conn, _ = _make_mock_conn(fetchone_return=row)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            result = await wardrobe_service.create_wardrobe_item(
                user_id=_USER_ID, name="Item", category=None, image_s3_key=None
            )

        assert result["tags"] == []

    @pytest.mark.asyncio
    async def test_executes_insert_with_correct_params(self):
        row = (_ITEM_ID, "Jeans", "bottoms", None, [], _NOW)
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
            (_ITEM_ID, "Blazer", "outerwear", "s3/key.jpg", ["navy"], _NOW),
            (uuid.uuid4(), "Jeans", "bottoms", None, [], _NOW),
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
        rows = [(_ITEM_ID, "Shirt", None, None, None, _NOW)]
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
        row = (_ITEM_ID, "Blazer", "outerwear", "s3/key.jpg", ["navy"], _NOW)
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
        row = (_ITEM_ID, "Updated Blazer", "tops", "s3/key.jpg", ["blue"], _NOW)
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
        assert result["name"] == "Updated Blazer"
        assert result["category"] == "tops"
        assert result["tags"] == ["blue"]

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
        row = (_ITEM_ID, "Blazer", "outerwear", None, [], _NOW)
        mock_conn, _ = _make_mock_conn(fetchone_return=row)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            await wardrobe_service.update_wardrobe_item(
                user_id=_USER_ID, item_id=str(_ITEM_ID), name="Blazer"
            )

        mock_conn.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_where_clause_includes_user_id(self):
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=None)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            await wardrobe_service.update_wardrobe_item(
                user_id=_USER_ID, item_id=str(_ITEM_ID), name="X"
            )

        mock_cur.execute.assert_awaited_once()
        params = mock_cur.execute.call_args[0][1]
        assert str(_ITEM_ID) in params
        assert _USER_ID in params
