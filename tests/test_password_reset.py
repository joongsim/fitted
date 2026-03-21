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


# --- JWT invalidation tests ---
# Note: password_changed_at comes from DB as a timezone-aware datetime (TIMESTAMPTZ + psycopg3).
# In the auth check, we use calendar.timegm() to convert it to UTC epoch safely regardless
# of server local timezone.

class TestJWTInvalidationAfterPasswordReset:
    async def test_token_issued_before_password_change_is_rejected(self):
        import os
        from unittest.mock import patch
        from app.core.auth import create_access_token, get_current_user_id
        from datetime import timezone
        from fastapi import HTTPException

        # Create a token, then set password_changed_at to 5 seconds in the future.
        # psycopg3 returns TIMESTAMPTZ as timezone-aware datetime — use datetime.now(timezone.utc).
        future_change = datetime.now(timezone.utc) + timedelta(seconds=5)
        token = create_access_token({"sub": MOCK_USER_ID})
        request = _make_request_with_token(token)

        async def fake_get_password_changed_at(user_id):
            return future_change

        with patch.dict(os.environ, {"DEV_MODE": "false"}):
            with patch("app.core.auth.get_password_changed_at", side_effect=fake_get_password_changed_at):
                with pytest.raises(HTTPException) as exc_info:
                    await get_current_user_id(request)
        assert exc_info.value.status_code == 401

    async def test_token_issued_after_password_change_is_allowed(self):
        import os
        from unittest.mock import patch
        from app.core.auth import create_access_token, get_current_user_id

        # password_changed_at is far in the past — token was issued after it
        past_change = datetime(2020, 1, 1)  # naive UTC, far past
        token = create_access_token({"sub": MOCK_USER_ID})
        request = _make_request_with_token(token)

        async def fake_get_password_changed_at(user_id):
            return past_change

        with patch.dict(os.environ, {"DEV_MODE": "false"}):
            with patch("app.core.auth.get_password_changed_at", side_effect=fake_get_password_changed_at):
                user_id = await get_current_user_id(request)
        assert user_id == MOCK_USER_ID

    async def test_null_password_changed_at_allows_all_tokens(self):
        import os
        from unittest.mock import patch
        from app.core.auth import create_access_token, get_current_user_id

        token = create_access_token({"sub": MOCK_USER_ID})
        request = _make_request_with_token(token)

        async def fake_get_password_changed_at(user_id):
            return None  # NULL — user has never reset

        with patch.dict(os.environ, {"DEV_MODE": "false"}):
            with patch("app.core.auth.get_password_changed_at", side_effect=fake_get_password_changed_at):
                user_id = await get_current_user_id(request)
        assert user_id == MOCK_USER_ID

    async def test_token_issued_at_exact_same_second_as_password_change_is_allowed(self):
        """Strict < means token_iat == changed_at_ts is allowed (not rejected)."""
        import calendar
        import os
        from unittest.mock import patch
        from app.core.auth import create_access_token, get_current_user_id

        token = create_access_token({"sub": MOCK_USER_ID})
        request = _make_request_with_token(token)

        # Decode the token's iat and set changed_at to the exact same second
        from jose import jwt
        from app.core.config import config
        payload = jwt.decode(token, config.jwt_secret_key, algorithms=["HS256"])
        token_iat = payload["iat"]
        # Convert iat (UTC epoch) back to a naive UTC datetime
        from datetime import datetime as _dt
        same_second = _dt.utcfromtimestamp(token_iat)

        async def fake_get_password_changed_at(user_id):
            return same_second

        with patch.dict(os.environ, {"DEV_MODE": "false"}):
            with patch("app.core.auth.get_password_changed_at", side_effect=fake_get_password_changed_at):
                user_id = await get_current_user_id(request)
        assert user_id == MOCK_USER_ID


def _make_request_with_token(token: str):
    from unittest.mock import MagicMock
    mock_request = MagicMock()
    mock_request.cookies = {"access_token": token}
    mock_request.headers = {}
    mock_request.url.path = "/test"
    return mock_request


# --- Rate limiter setup test ---

def test_app_has_rate_limiter_state():
    """The FastAPI app should have a slowapi limiter attached to app.state."""
    from app.main import app
    assert hasattr(app.state, "limiter")


# --- Endpoint tests ---

import secrets as _secrets


class TestForgotPasswordEndpoint:
    async def test_returns_200_for_known_active_user(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from unittest.mock import patch, AsyncMock

        with patch("app.services.user_service.get_user_by_email", return_value={
            "user_id": MOCK_USER_ID, "email": MOCK_USER_EMAIL,
            "is_active": True, "hashed_password": "$2b$hash",
        }), patch("app.services.user_service.store_reset_token", new_callable=AsyncMock), \
             patch("app.services.email_service.send_password_reset_email", new_callable=AsyncMock):
            client = TestClient(app)
            resp = client.post("/auth/forgot-password", json={"email": MOCK_USER_EMAIL})
        assert resp.status_code == 200
        assert "reset link" in resp.json()["message"].lower()

    async def test_returns_200_for_unknown_email(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from unittest.mock import patch

        with patch("app.services.user_service.get_user_by_email", return_value=None):
            client = TestClient(app)
            resp = client.post("/auth/forgot-password", json={"email": "nobody@example.com"})
        assert resp.status_code == 200

    async def test_returns_200_for_inactive_user(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from unittest.mock import patch

        with patch("app.services.user_service.get_user_by_email", return_value={
            "user_id": MOCK_USER_ID, "email": MOCK_USER_EMAIL,
            "is_active": False, "hashed_password": "$2b$hash",
        }):
            client = TestClient(app)
            resp = client.post("/auth/forgot-password", json={"email": MOCK_USER_EMAIL})
        assert resp.status_code == 200

    async def test_returns_500_when_ses_raises(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from unittest.mock import patch, AsyncMock
        import botocore.exceptions

        with patch("app.services.user_service.get_user_by_email", return_value={
            "user_id": MOCK_USER_ID, "email": MOCK_USER_EMAIL,
            "is_active": True, "hashed_password": "$2b$hash",
        }), patch("app.services.user_service.store_reset_token", new_callable=AsyncMock), \
             patch("app.services.email_service.send_password_reset_email",
                   side_effect=botocore.exceptions.ClientError(
                       {"Error": {"Code": "MessageRejected", "Message": "Rejected"}},
                       "SendEmail"
                   )):
            client = TestClient(app)
            resp = client.post("/auth/forgot-password", json={"email": MOCK_USER_EMAIL})
        assert resp.status_code == 500


class TestResetPasswordEndpoint:
    def _valid_token(self):
        return _secrets.token_urlsafe(32)  # exactly 43 chars

    async def test_valid_token_returns_200(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from unittest.mock import patch, AsyncMock
        from uuid import UUID

        raw_token = self._valid_token()
        with patch("app.services.user_service.reset_password", new_callable=AsyncMock,
                   return_value={"user_id": UUID(MOCK_USER_ID), "email": MOCK_USER_EMAIL}):
            client = TestClient(app)
            resp = client.post("/auth/reset-password", json={
                "token": raw_token, "new_password": "newpassword123"
            })
        assert resp.status_code == 200

    async def test_invalid_token_returns_400(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from unittest.mock import patch, AsyncMock

        raw_token = self._valid_token()
        with patch("app.services.user_service.reset_password", new_callable=AsyncMock,
                   return_value=None):
            client = TestClient(app)
            resp = client.post("/auth/reset-password", json={
                "token": raw_token, "new_password": "newpassword123"
            })
        assert resp.status_code == 400
        assert "invalid or has expired" in resp.json()["detail"].lower()

    async def test_wrong_token_length_returns_422(self):
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        resp = client.post("/auth/reset-password", json={
            "token": "tooshort", "new_password": "newpassword123"
        })
        assert resp.status_code == 422  # Pydantic validation error

    async def test_short_password_returns_422(self):
        from fastapi.testclient import TestClient
        from app.main import app

        raw_token = self._valid_token()
        client = TestClient(app)
        resp = client.post("/auth/reset-password", json={
            "token": raw_token, "new_password": "short"
        })
        assert resp.status_code == 422


# --- Frontend tests ---
# These tests inspect source code (not live HTTP) to verify the routes exist.

import inspect


class TestFrontendPasswordResetPages:
    def _get_frontend_source(self):
        import os, sys
        with patch.dict(os.environ, {"USE_SSM": "false", "API_BASE_URL": "http://localhost:8000"}):
            if "frontend.app" in sys.modules:
                del sys.modules["frontend.app"]
            import frontend.app as frontend
            return inspect.getsource(frontend)

    def test_login_page_has_forgot_password_link(self):
        """The login page source should contain a link to /forgot-password."""
        source = self._get_frontend_source()
        assert "/forgot-password" in source
        assert "Forgot" in source

    def test_forgot_password_get_route_exists(self):
        """Source should define a GET /forgot-password route."""
        source = self._get_frontend_source()
        assert '"/forgot-password"' in source or "'/forgot-password'" in source
        assert 'type="email"' in source or "type='email'" in source

    def test_forgot_password_post_route_exists(self):
        """Source should define a POST /forgot-password route that calls the API."""
        source = self._get_frontend_source()
        assert "/auth/forgot-password" in source

    def test_reset_password_get_route_exists(self):
        """Source should define a GET /reset-password route with a hidden token field."""
        source = self._get_frontend_source()
        assert "/reset-password" in source
        assert 'type="hidden"' in source or "type='hidden'" in source
        assert 'name="token"' in source or "name='token'" in source

    def test_reset_password_post_route_calls_api(self):
        """Source should define a POST /reset-password route that calls the backend."""
        source = self._get_frontend_source()
        assert "/auth/reset-password" in source
