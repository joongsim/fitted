"""Tests for app/core/auth.py — JWT creation/validation and password hashing."""
import os
from datetime import timedelta
from unittest.mock import MagicMock, patch

import bcrypt
import pytest
from fastapi import HTTPException
from jose import jwt

from app.core.auth import (
    create_access_token,
    get_current_user_id,
    get_password_hash,
    verify_password,
)
from app.core.config import config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

KNOWN_SECRET = "dev-secret-key-change-me-in-prod"
KNOWN_ALGORITHM = "HS256"


def _make_request(cookie_token=None, bearer_token=None):
    """Build a minimal FastAPI-style Request mock."""
    cookies = {}
    headers = {}
    if cookie_token:
        cookies["access_token"] = cookie_token
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    mock_request = MagicMock()
    mock_request.cookies = cookies
    mock_request.headers = headers
    mock_request.url.path = "/test"
    return mock_request


def _make_token(sub: str, secret: str = KNOWN_SECRET, algorithm: str = KNOWN_ALGORITHM, **extra_claims) -> str:
    """Encode a JWT directly with python-jose for test setup."""
    payload = {"sub": sub, **extra_claims}
    return jwt.encode(payload, secret, algorithm=algorithm)


# ---------------------------------------------------------------------------
# verify_password
# ---------------------------------------------------------------------------


class TestVerifyPassword:
    def test_correct_password_returns_true(self):
        hashed = bcrypt.hashpw(b"mypassword", bcrypt.gensalt()).decode()
        assert verify_password("mypassword", hashed) is True

    def test_wrong_password_returns_false(self):
        hashed = bcrypt.hashpw(b"mypassword", bcrypt.gensalt()).decode()
        assert verify_password("wrongpassword", hashed) is False

    def test_empty_string_password_against_non_empty_hash_returns_false(self):
        hashed = bcrypt.hashpw(b"mypassword", bcrypt.gensalt()).decode()
        assert verify_password("", hashed) is False

    def test_password_with_special_characters_matches_its_own_hash(self):
        pw = "P@$$w0rd!#%^&*()_+-=[]{}|;':\",./<>?"
        hashed = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
        assert verify_password(pw, hashed) is True

    def test_unicode_password_matches_its_own_hash(self):
        pw = "密码パスワード🔑"
        hashed = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
        assert verify_password(pw, hashed) is True

    def test_case_sensitive_password_does_not_match(self):
        hashed = bcrypt.hashpw(b"MyPassword", bcrypt.gensalt()).decode()
        assert verify_password("mypassword", hashed) is False


# ---------------------------------------------------------------------------
# get_password_hash
# ---------------------------------------------------------------------------


class TestGetPasswordHash:
    def test_produces_valid_bcrypt_hash(self):
        hashed = get_password_hash("testpassword")
        assert hashed.startswith("$2b$")

    def test_hash_is_different_from_plaintext(self):
        pw = "testpassword"
        hashed = get_password_hash(pw)
        assert hashed != pw

    def test_same_password_produces_different_hashes_due_to_salting(self):
        pw = "testpassword"
        hash1 = get_password_hash(pw)
        hash2 = get_password_hash(pw)
        assert hash1 != hash2

    def test_roundtrip_hash_then_verify(self):
        pw = "SecureRoundTrip99!"
        hashed = get_password_hash(pw)
        assert verify_password(pw, hashed) is True

    def test_wrong_password_fails_roundtrip(self):
        hashed = get_password_hash("correct")
        assert verify_password("incorrect", hashed) is False

    def test_returns_decodable_string(self):
        hashed = get_password_hash("anypassword")
        assert isinstance(hashed, str)
        # Must be decodable back to bytes without error
        hashed.encode("utf-8")


# ---------------------------------------------------------------------------
# create_access_token
# ---------------------------------------------------------------------------


class TestCreateAccessToken:
    def test_returns_a_non_empty_string(self):
        token = create_access_token({"sub": "user-123"})
        assert isinstance(token, str)
        assert len(token) > 0

    def test_token_contains_sub_claim(self):
        token = create_access_token({"sub": "user-abc"})
        payload = jwt.decode(token, KNOWN_SECRET, algorithms=[KNOWN_ALGORITHM])
        assert payload["sub"] == "user-abc"

    def test_token_contains_exp_claim(self):
        token = create_access_token({"sub": "user-abc"})
        payload = jwt.decode(token, KNOWN_SECRET, algorithms=[KNOWN_ALGORITHM])
        assert "exp" in payload

    def test_custom_expires_delta_is_used(self):
        from datetime import datetime

        delta = timedelta(seconds=5)
        before = datetime.utcnow()
        token = create_access_token({"sub": "u"}, expires_delta=delta)
        payload = jwt.decode(token, KNOWN_SECRET, algorithms=[KNOWN_ALGORITHM])
        exp = datetime.utcfromtimestamp(payload["exp"])
        # Expiry should be within a couple of seconds of before + 5s
        assert abs((exp - before).total_seconds() - 5) < 2

    def test_default_expiry_uses_configured_minutes(self):
        from datetime import datetime

        token = create_access_token({"sub": "u"})
        payload = jwt.decode(token, KNOWN_SECRET, algorithms=[KNOWN_ALGORITHM])
        exp = datetime.utcfromtimestamp(payload["exp"])
        expected_minutes = config.access_token_expire_minutes
        now = datetime.utcnow()
        delta_minutes = (exp - now).total_seconds() / 60
        # Allow ±1 minute tolerance
        assert abs(delta_minutes - expected_minutes) < 1

    def test_extra_claims_included_in_token(self):
        token = create_access_token({"sub": "u", "role": "admin"})
        payload = jwt.decode(token, KNOWN_SECRET, algorithms=[KNOWN_ALGORITHM])
        assert payload["role"] == "admin"

    def test_original_dict_is_not_mutated(self):
        data = {"sub": "user-no-mutate"}
        original = data.copy()
        create_access_token(data)
        assert data == original


# ---------------------------------------------------------------------------
# get_current_user_id
# ---------------------------------------------------------------------------


class TestGetCurrentUserIdDevMode:
    async def test_dev_mode_returns_fixed_uuid(self):
        with patch.dict(os.environ, {"DEV_MODE": "true"}):
            request = _make_request()
            user_id = await get_current_user_id(request)
        assert user_id == "00000000-0000-0000-0000-000000000000"

    async def test_dev_mode_case_insensitive_true(self):
        with patch.dict(os.environ, {"DEV_MODE": "TRUE"}):
            request = _make_request()
            user_id = await get_current_user_id(request)
        assert user_id == "00000000-0000-0000-0000-000000000000"

    async def test_dev_mode_false_does_not_bypass(self):
        with patch.dict(os.environ, {"DEV_MODE": "false"}):
            request = _make_request()  # no token
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user_id(request)
        assert exc_info.value.status_code == 401


class TestGetCurrentUserIdCookieToken:
    async def test_valid_cookie_token_returns_user_id(self):
        token = _make_token("user-uuid-1")
        request = _make_request(cookie_token=token)
        with patch.dict(os.environ, {"DEV_MODE": "false"}):
            user_id = await get_current_user_id(request)
        assert user_id == "user-uuid-1"

    async def test_expired_cookie_token_raises_401(self):
        from datetime import datetime
        import time as _time

        # Build a token that expired 1 hour ago
        past_exp = datetime.utcnow() - timedelta(hours=1)
        expired_token = jwt.encode(
            {"sub": "u", "exp": past_exp}, KNOWN_SECRET, algorithm=KNOWN_ALGORITHM
        )
        request = _make_request(cookie_token=expired_token)
        with patch.dict(os.environ, {"DEV_MODE": "false"}):
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user_id(request)
        assert exc_info.value.status_code == 401

    async def test_malformed_cookie_token_raises_401(self):
        request = _make_request(cookie_token="not.a.jwt")
        with patch.dict(os.environ, {"DEV_MODE": "false"}):
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user_id(request)
        assert exc_info.value.status_code == 401

    async def test_cookie_token_signed_with_wrong_secret_raises_401(self):
        bad_token = _make_token("u", secret="wrong-secret")
        request = _make_request(cookie_token=bad_token)
        with patch.dict(os.environ, {"DEV_MODE": "false"}):
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user_id(request)
        assert exc_info.value.status_code == 401

    async def test_cookie_token_missing_sub_claim_raises_401(self):
        # Token without 'sub'
        token = jwt.encode({"data": "no-sub"}, KNOWN_SECRET, algorithm=KNOWN_ALGORITHM)
        request = _make_request(cookie_token=token)
        with patch.dict(os.environ, {"DEV_MODE": "false"}):
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user_id(request)
        assert exc_info.value.status_code == 401
        assert "missing sub" in exc_info.value.detail


class TestGetCurrentUserIdBearerToken:
    async def test_valid_bearer_token_returns_user_id(self):
        token = _make_token("bearer-user-42")
        request = _make_request(bearer_token=token)
        with patch.dict(os.environ, {"DEV_MODE": "false"}):
            user_id = await get_current_user_id(request)
        assert user_id == "bearer-user-42"

    async def test_invalid_bearer_token_raises_401(self):
        request = _make_request(bearer_token="garbage-token")
        with patch.dict(os.environ, {"DEV_MODE": "false"}):
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user_id(request)
        assert exc_info.value.status_code == 401

    async def test_no_token_anywhere_raises_401(self):
        request = _make_request()
        with patch.dict(os.environ, {"DEV_MODE": "false"}):
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user_id(request)
        assert exc_info.value.status_code == 401
        assert "Not authenticated" in exc_info.value.detail

    async def test_cookie_takes_precedence_over_bearer(self):
        """When both cookie and Authorization header are present, cookie wins."""
        cookie_token = _make_token("cookie-user")
        bearer_token = _make_token("bearer-user")
        request = _make_request(cookie_token=cookie_token, bearer_token=bearer_token)
        with patch.dict(os.environ, {"DEV_MODE": "false"}):
            user_id = await get_current_user_id(request)
        assert user_id == "cookie-user"

    async def test_authorization_header_without_bearer_prefix_raises_401(self):
        """Authorization header that doesn't start with 'Bearer ' is ignored."""
        token = _make_token("some-user")
        mock_request = MagicMock()
        mock_request.cookies = {}
        mock_request.headers = {"Authorization": f"Token {token}"}
        mock_request.url.path = "/test"
        with patch.dict(os.environ, {"DEV_MODE": "false"}):
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user_id(mock_request)
        assert exc_info.value.status_code == 401
