"""Tests for app/services/user_service.py — user CRUD operations."""
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.models.user import User, UserCreate
from tests.conftest import MOCK_USER_ID, MOCK_USER_EMAIL, MOCK_USER_FULL_NAME


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_conn(fetchone_return=None, fetchall_return=None):
    """
    Build a mock async DB connection + cursor.
    Returns (mock_connection, mock_cursor).

    psycopg3 uses `conn.cursor()` as a synchronous call that returns an async
    context manager — it is NOT a coroutine itself.  We model that by making
    `cursor` a plain (non-async) callable on the MagicMock connection.
    """
    mock_cur = AsyncMock()
    mock_cur.fetchone = AsyncMock(return_value=fetchone_return)
    mock_cur.fetchall = AsyncMock(return_value=fetchall_return or [])
    mock_cur.execute = AsyncMock()

    # cursor() returns an async context manager (not a coroutine)
    mock_cur_ctx = MagicMock()
    mock_cur_ctx.__aenter__ = AsyncMock(return_value=mock_cur)
    mock_cur_ctx.__aexit__ = AsyncMock(return_value=False)

    # conn itself is a MagicMock (not AsyncMock) so that conn.cursor() is a
    # regular function call, not a coroutine.
    mock_conn = MagicMock()
    mock_conn.cursor = MagicMock(return_value=mock_cur_ctx)
    mock_conn.commit = AsyncMock()
    mock_conn.rollback = AsyncMock()

    return mock_conn, mock_cur


@asynccontextmanager
async def _mock_get_connection(mock_conn):
    """Async context manager that yields a pre-built mock connection."""
    yield mock_conn


def _patch_get_connection(mock_conn):
    """Return a patch context that replaces get_connection with our mock."""
    return patch(
        "app.services.user_service.get_connection",
        return_value=_mock_get_connection(mock_conn),
    )


# Example DB row tuple returned by SELECT on users table
def _user_row(
    user_id=None,
    email=MOCK_USER_EMAIL,
    full_name=MOCK_USER_FULL_NAME,
    is_active=True,
    created_at=None,
    last_login=None,
):
    uid = UUID(user_id) if user_id else UUID(MOCK_USER_ID)
    ts = created_at or datetime(2024, 1, 1, tzinfo=timezone.utc)
    return (uid, email, full_name, is_active, ts, last_login)


# ---------------------------------------------------------------------------
# create_user
# ---------------------------------------------------------------------------


class TestCreateUser:
    async def test_happy_path_returns_user_model(self):
        from app.services import user_service

        user_in = UserCreate(
            email=MOCK_USER_EMAIL, password="Password123", full_name=MOCK_USER_FULL_NAME
        )
        row = _user_row()
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=row)

        with _patch_get_connection(mock_conn):
            with patch("app.services.user_service.get_password_hash", return_value="hashed"):
                result = await user_service.create_user(user_in)

        assert isinstance(result, User)
        assert result.email == MOCK_USER_EMAIL
        assert result.full_name == MOCK_USER_FULL_NAME
        assert result.is_active is True

    async def test_happy_path_inserts_user_and_preferences(self):
        from app.services import user_service

        user_in = UserCreate(email=MOCK_USER_EMAIL, password="Password123")
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=_user_row())

        with _patch_get_connection(mock_conn):
            with patch("app.services.user_service.get_password_hash", return_value="hashed"):
                await user_service.create_user(user_in)

        # execute should be called twice: INSERT users + INSERT user_preferences
        assert mock_cur.execute.await_count == 2

    async def test_happy_path_commits_transaction(self):
        from app.services import user_service

        user_in = UserCreate(email=MOCK_USER_EMAIL, password="Password123")
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=_user_row())

        with _patch_get_connection(mock_conn):
            with patch("app.services.user_service.get_password_hash", return_value="hashed"):
                await user_service.create_user(user_in)

        mock_conn.commit.assert_awaited_once()

    async def test_returns_none_when_fetchone_returns_nothing(self):
        from app.services import user_service

        user_in = UserCreate(email=MOCK_USER_EMAIL, password="Password123")
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=None)

        with _patch_get_connection(mock_conn):
            with patch("app.services.user_service.get_password_hash", return_value="hashed"):
                result = await user_service.create_user(user_in)

        assert result is None

    async def test_returns_none_and_rolls_back_on_db_exception(self):
        from app.services import user_service

        user_in = UserCreate(email=MOCK_USER_EMAIL, password="Password123")
        mock_conn, mock_cur = _make_mock_conn()
        mock_cur.execute.side_effect = Exception("duplicate key value")

        with _patch_get_connection(mock_conn):
            with patch("app.services.user_service.get_password_hash", return_value="hashed"):
                result = await user_service.create_user(user_in)

        assert result is None
        mock_conn.rollback.assert_awaited_once()

    async def test_uses_hashed_password_not_plaintext(self):
        from app.services import user_service

        user_in = UserCreate(email=MOCK_USER_EMAIL, password="plaintext")
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=_user_row())

        with _patch_get_connection(mock_conn):
            with patch("app.services.user_service.get_password_hash", return_value="$2b$hashed") as mock_hash:
                await user_service.create_user(user_in)

        mock_hash.assert_called_once_with("plaintext")
        # Verify the hashed value was passed to the DB, not the plain one
        first_execute_args = mock_cur.execute.await_args_list[0]
        assert "plaintext" not in str(first_execute_args)


# ---------------------------------------------------------------------------
# get_user_by_email
# ---------------------------------------------------------------------------


class TestGetUserByEmail:
    async def test_found_returns_dict_with_expected_keys(self):
        from app.services import user_service

        db_row = (UUID(MOCK_USER_ID), MOCK_USER_EMAIL, "$2b$hashed", MOCK_USER_FULL_NAME, True)
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=db_row)

        with _patch_get_connection(mock_conn):
            result = await user_service.get_user_by_email(MOCK_USER_EMAIL)

        assert result is not None
        assert result["user_id"] == UUID(MOCK_USER_ID)
        assert result["email"] == MOCK_USER_EMAIL
        assert result["hashed_password"] == "$2b$hashed"
        assert result["full_name"] == MOCK_USER_FULL_NAME
        assert result["is_active"] is True

    async def test_not_found_returns_none(self):
        from app.services import user_service

        mock_conn, mock_cur = _make_mock_conn(fetchone_return=None)

        with _patch_get_connection(mock_conn):
            result = await user_service.get_user_by_email("nobody@example.com")

        assert result is None

    async def test_queries_by_email_parameter(self):
        from app.services import user_service

        mock_conn, mock_cur = _make_mock_conn(fetchone_return=None)

        with _patch_get_connection(mock_conn):
            await user_service.get_user_by_email("specific@example.com")

        call_args = mock_cur.execute.await_args
        assert "specific@example.com" in str(call_args)


# ---------------------------------------------------------------------------
# get_user_by_id
# ---------------------------------------------------------------------------


class TestGetUserById:
    async def test_found_returns_user_model(self):
        from app.services import user_service

        row = _user_row()
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=row)

        with _patch_get_connection(mock_conn):
            result = await user_service.get_user_by_id(MOCK_USER_ID)

        assert isinstance(result, User)
        assert str(result.user_id) == MOCK_USER_ID
        assert result.email == MOCK_USER_EMAIL

    async def test_not_found_returns_none(self):
        from app.services import user_service

        mock_conn, mock_cur = _make_mock_conn(fetchone_return=None)

        with _patch_get_connection(mock_conn):
            result = await user_service.get_user_by_id("nonexistent-id")

        assert result is None

    async def test_queries_by_user_id_parameter(self):
        from app.services import user_service

        mock_conn, mock_cur = _make_mock_conn(fetchone_return=None)

        with _patch_get_connection(mock_conn):
            await user_service.get_user_by_id(MOCK_USER_ID)

        call_args = mock_cur.execute.await_args
        assert MOCK_USER_ID in str(call_args)


# ---------------------------------------------------------------------------
# update_last_login
# ---------------------------------------------------------------------------


class TestUpdateLastLogin:
    async def test_executes_update_and_commits(self):
        from app.services import user_service

        mock_conn, mock_cur = _make_mock_conn()

        with _patch_get_connection(mock_conn):
            await user_service.update_last_login(MOCK_USER_ID)

        mock_cur.execute.assert_awaited_once()
        mock_conn.commit.assert_awaited_once()

    async def test_passes_user_id_to_query(self):
        from app.services import user_service

        mock_conn, mock_cur = _make_mock_conn()

        with _patch_get_connection(mock_conn):
            await user_service.update_last_login(MOCK_USER_ID)

        call_args = mock_cur.execute.await_args
        assert MOCK_USER_ID in str(call_args)


# ---------------------------------------------------------------------------
# get_user_preferences
# ---------------------------------------------------------------------------


class TestGetUserPreferences:
    async def test_found_returns_style_and_size_prefs(self):
        from app.services import user_service

        style = {"style": "casual"}
        size = {"shirt": "M"}
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=(style, size))

        with _patch_get_connection(mock_conn):
            result = await user_service.get_user_preferences(MOCK_USER_ID)

        assert result["style_preferences"] == style
        assert result["size_info"] == size

    async def test_not_found_returns_empty_dicts(self):
        from app.services import user_service

        mock_conn, mock_cur = _make_mock_conn(fetchone_return=None)

        with _patch_get_connection(mock_conn):
            result = await user_service.get_user_preferences("nonexistent-user")

        assert result == {"style_preferences": {}, "size_info": {}}

    async def test_passes_user_id_to_query(self):
        from app.services import user_service

        mock_conn, mock_cur = _make_mock_conn(fetchone_return=None)

        with _patch_get_connection(mock_conn):
            await user_service.get_user_preferences(MOCK_USER_ID)

        call_args = mock_cur.execute.await_args
        assert MOCK_USER_ID in str(call_args)


# ---------------------------------------------------------------------------
# update_user_preferences
# ---------------------------------------------------------------------------


class TestUpdateUserPreferences:
    async def test_update_style_only_runs_one_execute(self):
        from app.services import user_service

        mock_conn, mock_cur = _make_mock_conn()

        with _patch_get_connection(mock_conn):
            await user_service.update_user_preferences(
                MOCK_USER_ID, style_prefs={"style": "formal"}
            )

        # Only one UPDATE (style), not two
        assert mock_cur.execute.await_count == 1

    async def test_update_size_only_runs_one_execute(self):
        from app.services import user_service

        mock_conn, mock_cur = _make_mock_conn()

        with _patch_get_connection(mock_conn):
            await user_service.update_user_preferences(
                MOCK_USER_ID, size_info={"shirt": "L"}
            )

        assert mock_cur.execute.await_count == 1

    async def test_update_both_runs_two_executes(self):
        from app.services import user_service

        mock_conn, mock_cur = _make_mock_conn()

        with _patch_get_connection(mock_conn):
            await user_service.update_user_preferences(
                MOCK_USER_ID,
                style_prefs={"style": "casual"},
                size_info={"shirt": "M"},
            )

        assert mock_cur.execute.await_count == 2

    async def test_update_neither_runs_zero_executes(self):
        from app.services import user_service

        mock_conn, mock_cur = _make_mock_conn()

        with _patch_get_connection(mock_conn):
            await user_service.update_user_preferences(MOCK_USER_ID)

        assert mock_cur.execute.await_count == 0

    async def test_always_commits(self):
        from app.services import user_service

        mock_conn, mock_cur = _make_mock_conn()

        with _patch_get_connection(mock_conn):
            await user_service.update_user_preferences(
                MOCK_USER_ID, style_prefs={"style": "sporty"}
            )

        mock_conn.commit.assert_awaited_once()

    async def test_style_prefs_serialized_as_json(self):
        from app.services import user_service

        style = {"style": "casual", "colors": ["blue"]}
        mock_conn, mock_cur = _make_mock_conn()

        with _patch_get_connection(mock_conn):
            await user_service.update_user_preferences(MOCK_USER_ID, style_prefs=style)

        call_args = mock_cur.execute.await_args
        # The JSON string of the style dict should appear in the execute call
        assert json.dumps(style) in str(call_args)
