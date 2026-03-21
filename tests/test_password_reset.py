"""Tests for password reset feature."""
import pytest


def test_migration_sql_contains_reset_token_column():
    from scripts.db_migrate import MIGRATION_SQL
    assert "reset_token" in MIGRATION_SQL


def test_migration_sql_contains_reset_token_expires_at_column():
    from scripts.db_migrate import MIGRATION_SQL
    assert "reset_token_expires_at" in MIGRATION_SQL


def test_migration_sql_contains_password_changed_at_column():
    from scripts.db_migrate import MIGRATION_SQL
    assert "password_changed_at" in MIGRATION_SQL


def test_migration_sql_contains_index():
    from scripts.db_migrate import MIGRATION_SQL
    assert "idx_users_reset_token" in MIGRATION_SQL


def test_rollback_sql_contains_drop_columns():
    from scripts.db_migrate import ROLLBACK_SQL
    assert "reset_token" in ROLLBACK_SQL
    assert "password_changed_at" in ROLLBACK_SQL


# --- Config tests ---


def test_config_has_ses_sender_email_property():
    from app.core.config import Config
    assert hasattr(Config, "ses_sender_email")


def test_config_has_disable_email_property():
    from app.core.config import Config
    assert hasattr(Config, "disable_email")


def test_config_has_frontend_url_property():
    from app.core.config import Config
    assert hasattr(Config, "frontend_url")


def test_config_has_aws_region_property():
    from app.core.config import Config
    assert hasattr(Config, "aws_region")


def test_config_disable_email_defaults_to_false(monkeypatch):
    import os
    monkeypatch.delenv("DISABLE_EMAIL", raising=False)
    from app.core.config import Config
    c = Config()
    assert c.disable_email is False


def test_config_disable_email_true_when_env_set(monkeypatch):
    monkeypatch.setenv("DISABLE_EMAIL", "true")
    from app.core.config import Config
    c = Config()
    assert c.disable_email is True


# --- Pydantic model tests ---


def test_forgot_password_request_valid_email():
    from app.models.user import ForgotPasswordRequest
    req = ForgotPasswordRequest(email="user@example.com")
    assert req.email == "user@example.com"


def test_forgot_password_request_rejects_invalid_email():
    from app.models.user import ForgotPasswordRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ForgotPasswordRequest(email="not-an-email")


def test_reset_password_request_valid():
    from app.models.user import ResetPasswordRequest
    req = ResetPasswordRequest(token="a" * 43, new_password="password123")
    assert req.token == "a" * 43
    assert req.new_password == "password123"


def test_reset_password_request_rejects_short_password():
    from app.models.user import ResetPasswordRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ResetPasswordRequest(token="a" * 43, new_password="short")


def test_reset_password_request_rejects_long_password():
    from app.models.user import ResetPasswordRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ResetPasswordRequest(token="a" * 43, new_password="x" * 129)


def test_reset_password_request_rejects_wrong_token_length():
    from app.models.user import ResetPasswordRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ResetPasswordRequest(token="tooshort", new_password="password123")


def test_reset_password_request_accepts_minimum_password_length():
    from app.models.user import ResetPasswordRequest
    req = ResetPasswordRequest(token="a" * 43, new_password="a" * 8)
    assert len(req.new_password) == 8


def test_reset_password_request_accepts_maximum_password_length():
    from app.models.user import ResetPasswordRequest
    req = ResetPasswordRequest(token="a" * 43, new_password="a" * 128)
    assert len(req.new_password) == 128


def test_reset_password_request_rejects_token_too_long():
    from pydantic import ValidationError
    from app.models.user import ResetPasswordRequest
    with pytest.raises(ValidationError):
        ResetPasswordRequest(token="a" * 44, new_password="validpassword123")


# --- hash_reset_token tests ---


def test_hash_reset_token_returns_64_char_hex_string():
    from app.core.auth import hash_reset_token
    result = hash_reset_token("sometoken")
    assert isinstance(result, str)
    assert len(result) == 64
    # Must be hex
    int(result, 16)


def test_hash_reset_token_same_input_same_output():
    from app.core.auth import hash_reset_token
    assert hash_reset_token("abc") == hash_reset_token("abc")


def test_hash_reset_token_different_inputs_different_outputs():
    from app.core.auth import hash_reset_token
    assert hash_reset_token("token1") != hash_reset_token("token2")


def test_create_access_token_includes_iat_claim():
    from app.core.auth import create_access_token
    from app.core.config import config
    from jose import jwt
    token = create_access_token({"sub": "user-1"})
    payload = jwt.decode(token, config.jwt_secret_key, algorithms=["HS256"])
    assert "iat" in payload
    assert isinstance(payload["iat"], int)


# --- User service tests ---
# Uses the same _make_mock_conn / _patch_get_connection helpers as test_user_service.py

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta
from uuid import UUID
from tests.conftest import MOCK_USER_ID, MOCK_USER_EMAIL


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
    mock_conn.rollback = AsyncMock()
    return mock_conn, mock_cur


@asynccontextmanager
async def _mock_get_connection(mock_conn):
    yield mock_conn


def _patch_get_connection(mock_conn):
    return patch(
        "app.services.user_service.get_connection",
        return_value=_mock_get_connection(mock_conn),
    )


class TestStoreResetToken:
    async def test_executes_update_and_commits(self):
        from app.services import user_service
        mock_conn, mock_cur = _make_mock_conn()
        expires = datetime.now(timezone.utc) + timedelta(hours=1)

        with _patch_get_connection(mock_conn):
            await user_service.store_reset_token(MOCK_USER_EMAIL, "a" * 64, expires)

        mock_cur.execute.assert_awaited_once()
        mock_conn.commit.assert_awaited_once()

    async def test_passes_hash_and_expiry_to_query(self):
        from app.services import user_service
        mock_conn, mock_cur = _make_mock_conn()
        token_hash = "b" * 64
        expires = datetime.now(timezone.utc) + timedelta(hours=1)

        with _patch_get_connection(mock_conn):
            await user_service.store_reset_token(MOCK_USER_EMAIL, token_hash, expires)

        params = mock_cur.execute.await_args.args[1]
        assert token_hash in params
        assert MOCK_USER_EMAIL in params

    async def test_unknown_email_is_noop_no_exception(self):
        from app.services import user_service
        mock_conn, mock_cur = _make_mock_conn()

        with _patch_get_connection(mock_conn):
            # Should not raise even if 0 rows updated
            await user_service.store_reset_token("nobody@example.com", "c" * 64, datetime.now(timezone.utc))

        mock_cur.execute.assert_awaited_once()  # SQL still runs; no error


class TestResetPassword:
    async def test_valid_token_returns_user_dict(self):
        from app.services import user_service
        user_row = (UUID(MOCK_USER_ID), MOCK_USER_EMAIL)
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=user_row)
        changed_at = datetime.now(timezone.utc)

        with _patch_get_connection(mock_conn):
            result = await user_service.reset_password("d" * 64, "$2b$hashed", changed_at)

        assert result is not None
        assert result["user_id"] == UUID(MOCK_USER_ID)
        assert result["email"] == MOCK_USER_EMAIL

    async def test_invalid_or_expired_token_returns_none(self):
        from app.services import user_service
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=None)
        changed_at = datetime.now(timezone.utc)

        with _patch_get_connection(mock_conn):
            result = await user_service.reset_password("e" * 64, "$2b$hashed", changed_at)

        assert result is None

    async def test_commits_transaction_on_success(self):
        from app.services import user_service
        user_row = (UUID(MOCK_USER_ID), MOCK_USER_EMAIL)
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=user_row)

        with _patch_get_connection(mock_conn):
            await user_service.reset_password("f" * 64, "$2b$hashed", datetime.now(timezone.utc))

        mock_conn.commit.assert_awaited_once()

    async def test_rollback_on_exception(self):
        from app.services import user_service
        mock_conn, mock_cur = _make_mock_conn()
        mock_cur.execute.side_effect = Exception("DB error")

        with _patch_get_connection(mock_conn):
            result = await user_service.reset_password("g" * 64, "$2b$hashed", datetime.now(timezone.utc))

        assert result is None
        mock_conn.rollback.assert_awaited_once()

    async def test_two_executes_on_success_consume_then_update(self):
        """consume_reset_token SQL + update_password SQL = 2 execute calls."""
        from app.services import user_service
        user_row = (UUID(MOCK_USER_ID), MOCK_USER_EMAIL)
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=user_row)

        with _patch_get_connection(mock_conn):
            await user_service.reset_password("h" * 64, "$2b$hashed", datetime.now(timezone.utc))

        assert mock_cur.execute.await_count == 2

    async def test_password_changed_at_passed_to_update_not_sql_now(self):
        from app.services import user_service
        user_row = (UUID(MOCK_USER_ID), MOCK_USER_EMAIL)
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=user_row)
        changed_at = datetime(2026, 3, 20, 12, 0, 0)

        with _patch_get_connection(mock_conn):
            await user_service.reset_password("i" * 64, "$2b$hashed", changed_at)

        # The changed_at value must appear as a positional arg in the second execute call.
        # args[1] is the params tuple: (new_hashed_password, changed_at, user_id)
        second_call_params = mock_cur.execute.await_args_list[1].args[1]
        assert changed_at in second_call_params

    async def test_inactive_user_token_returns_none(self):
        """is_active=TRUE guard: token exists but user is inactive → consume returns None."""
        from app.services import user_service
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=None)

        with _patch_get_connection(mock_conn):
            result = await user_service.reset_password("j" * 64, "$2b$hashed", datetime.now(timezone.utc))

        assert result is None
