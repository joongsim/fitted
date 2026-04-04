# Password Reset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a password reset flow (forgot-password email → time-limited link → set new password) to the Fitted FastAPI + FastHTML app.

**Architecture:** DB-stored HMAC-SHA256 token in a new `reset_token` column; atomic single-statement token consumption; `password_changed_at` column used to invalidate existing JWTs after reset; AWS SES for email with `DISABLE_EMAIL=true` dev fallback.

**Tech Stack:** FastAPI, psycopg3 (raw async SQL), PostgreSQL, FastHTML/HTMX, python-jose, bcrypt, boto3 SES, slowapi (new)

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `scripts/db_migrate.py` | Modify | Add up/down migration SQL for 3 new columns + index |
| `app/core/config.py` | Modify | Add `ses_sender_email`, `aws_region`, `disable_email`, `frontend_url` properties |
| `app/models/user.py` | Modify | Add `ForgotPasswordRequest`, `ResetPasswordRequest` Pydantic models |
| `app/core/auth.py` | Modify | Add `hash_reset_token()`; add `iat` to `create_access_token`; update `get_current_user_id` to check `password_changed_at` |
| `app/services/email_service.py` | **Create** | `send_password_reset_email()` via AWS SES with `DISABLE_EMAIL` dev fallback |
| `app/services/user_service.py` | Modify | Add `store_reset_token()`, `reset_password()` (atomic consume + update in one transaction) |
| `app/main.py` | Modify | Add slowapi setup; `POST /auth/forgot-password`; `POST /auth/reset-password` |
| `frontend/app.py` | Modify | "Forgot your password?" link on login page; `/forgot-password` page; `/reset-password` page |
| `pyproject.toml` | Modify | Add `slowapi>=0.1.9` to dependencies |
| `requirements.txt` | Modify | Add `slowapi>=0.1.9` |
| `tests/test_email_service.py` | **Create** | Unit tests for `send_password_reset_email` |
| `tests/test_password_reset.py` | **Create** | Unit + integration tests for all new auth/user_service/endpoint behaviour |

---

## Codebase Notes (read before implementing)

- **JWT library is `python-jose`** (`from jose import JWTError, jwt`), NOT PyJWT. `python-jose` does NOT auto-include `iat` — you must add it explicitly.
- **DB pattern:** `async with get_connection() as conn:` → `async with conn.cursor() as cur:` → `await cur.execute(sql, params)` → `await cur.fetchone()` → `await conn.commit()`. Rollback on exception with `await conn.rollback()`. See `app/services/user_service.py`.
- **Config pattern:** `self.get_parameter("/fitted/param-name", default="fallback")`. Local dev maps SSM path suffix to env var: `/fitted/ses-sender-email` → `SES_SENDER_EMAIL`. See `app/core/config.py`.
- **Test mocking:** Tests mock `get_connection` and DB cursors. See `tests/test_user_service.py` for the `_make_mock_conn` / `_patch_get_connection` helpers — copy this pattern exactly.
- **pytest-asyncio** is configured with `asyncio_mode = "auto"` — async test functions work without any decorator.
- **Frontend pattern:** FastHTML components like `Form(Input(...), Button(...), hx_post="/route")`. Route handlers receive form fields as function params. See `frontend/app.py:748-868`.
- **`conftest.py`** has `MOCK_USER_ID`, `MOCK_USER_EMAIL`, `patch_config_get_parameter` fixture. Import from there.
- **`reset_password` service function:** The spec defines `consume_reset_token` and `update_password_and_clear_token` as separate functions, but they must run in a single transaction. Implement a single combined `reset_password(token_hash, new_hashed_password, changed_at)` service function that does both atomically under one `get_connection()` context. This is the function the endpoint calls.
- **`_split_statements` already exists** in `scripts/db_migrate.py` at line 219. Do not redefine it — just call it from the new `migrate_password_reset()` function.
- **Timezone:** `password_changed_at` is stored as `TIMESTAMPTZ` and psycopg3 returns it as a timezone-aware datetime. Use `datetime.now(timezone.utc)` (not `datetime.utcnow()`) everywhere to keep all datetimes timezone-aware and comparable. In `get_current_user_id`, use `int(calendar.timegm(password_changed_at.utctimetuple()))` to safely convert any naive-or-aware datetime to UTC epoch seconds for comparison with `iat`.

---

## Task 1: DB Migration

**Files:**
- Modify: `scripts/db_migrate.py`

- [ ] **Step 1: Write a failing test that verifies the migration SQL is present**

```python
# tests/test_password_reset.py  (create this file)
"""Tests for password reset feature."""


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
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_password_reset.py::test_migration_sql_contains_reset_token_column -v
```

Expected: `ImportError` or `FAILED`

- [ ] **Step 3: Add migration SQL constants to `scripts/db_migrate.py`**

Add these two constants after `SCHEMA_SQL`:

```python
MIGRATION_SQL = """
ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token VARCHAR(64);
ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token_expires_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_users_reset_token
    ON users(reset_token) WHERE reset_token IS NOT NULL;
"""

ROLLBACK_SQL = """
DROP INDEX IF EXISTS idx_users_reset_token;
ALTER TABLE users DROP COLUMN IF EXISTS password_changed_at;
ALTER TABLE users DROP COLUMN IF EXISTS reset_token_expires_at;
ALTER TABLE users DROP COLUMN IF EXISTS reset_token;
"""
```

Also add a `migrate_password_reset()` function after `migrate()`:

```python
def migrate_password_reset() -> None:
    """Apply the password reset migration (adds reset_token columns)."""
    try:
        database_url = config.database_url
    except Exception as e:
        print(f"Error: could not load DATABASE_URL — {e}", file=sys.stderr)
        sys.exit(1)

    print("Connecting to database...")
    try:
        with psycopg.connect(database_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                print("Applying password reset migration...")
                for statement in _split_statements(MIGRATION_SQL):
                    cur.execute(statement)
                print("Password reset migration successful.")
    except Exception as e:
        print(f"Migration failed: {e}", file=sys.stderr)
        sys.exit(1)
```

- [ ] **Step 4: Run the migration tests**

```
pytest tests/test_password_reset.py -v
```

Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/db_migrate.py tests/test_password_reset.py
git commit -m "feat: add password reset migration SQL and tests"
```

---

## Task 2: Config Additions

**Files:**
- Modify: `app/core/config.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_password_reset.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_password_reset.py -k "config" -v
```

Expected: FAILED

- [ ] **Step 3: Add properties to `app/core/config.py`**

Add these properties to the `Config` class, before the closing of the class (after `access_token_expire_minutes`):

```python
    @property
    def ses_sender_email(self) -> str:
        """Get the verified SES sender email address."""
        return self.get_parameter("/fitted/ses-sender-email", default="noreply@example.com")

    @property
    def aws_region(self) -> str:
        """Get the AWS region for SES."""
        return os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION", "us-west-1")

    @property
    def disable_email(self) -> bool:
        """If true, log reset URLs instead of sending via SES (dev/CI mode)."""
        return os.environ.get("DISABLE_EMAIL", "false").lower() == "true"

    @property
    def frontend_url(self) -> str:
        """Base URL of the frontend app (used in reset email links)."""
        return self.get_parameter(
            "/fitted/frontend-url", default="http://localhost:5001"
        )
```

- [ ] **Step 4: Run config tests**

```
pytest tests/test_password_reset.py -k "config" -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add app/core/config.py tests/test_password_reset.py
git commit -m "feat: add password reset config properties"
```

---

## Task 3: Pydantic Models

**Files:**
- Modify: `app/models/user.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_password_reset.py`:

```python
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
```

Add `import pytest` at the top of `tests/test_password_reset.py` if not already there.

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_password_reset.py -k "request" -v
```

Expected: FAILED (ImportError on missing models)

- [ ] **Step 3: Add models to `app/models/user.py`**

```python
from pydantic import BaseModel, EmailStr, field_validator
from uuid import UUID
from datetime import datetime
from typing import Optional, List


# ... existing models unchanged ...


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("token")
    @classmethod
    def token_must_be_43_chars(cls, v: str) -> str:
        if len(v) != 43:
            raise ValueError("token must be exactly 43 characters")
        return v

    @field_validator("new_password")
    @classmethod
    def password_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        if len(v) > 128:
            raise ValueError("password must be at most 128 characters")
        return v
```

Note: add `field_validator` to the existing `from pydantic import BaseModel, EmailStr` import line.

- [ ] **Step 4: Run model tests**

```
pytest tests/test_password_reset.py -k "request" -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add app/models/user.py tests/test_password_reset.py
git commit -m "feat: add ForgotPasswordRequest and ResetPasswordRequest models"
```

---

## Task 4: hash_reset_token + iat in Tokens

**Files:**
- Modify: `app/core/auth.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_password_reset.py`:

```python
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
    from jose import jwt
    token = create_access_token({"sub": "user-1"})
    payload = jwt.decode(token, "dev-secret-key-change-me-in-prod", algorithms=["HS256"])
    assert "iat" in payload
    assert isinstance(payload["iat"], int)
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_password_reset.py -k "hash_reset or iat" -v
```

Expected: FAILED

- [ ] **Step 3: Add `hash_reset_token` and `iat` to `app/core/auth.py`**

At the top of `app/core/auth.py`, add to imports:
```python
import calendar
import hashlib
import hmac
import secrets
import time
```

Add the new function after `get_password_hash`:

```python
def hash_reset_token(raw_token: str) -> str:
    """
    HMAC-SHA256 hash of a reset token keyed with the JWT secret.
    Always returns a 64-character hex string. Never store the raw token.
    """
    return hmac.new(
        config.jwt_secret_key.encode(),
        raw_token.encode(),
        hashlib.sha256,
    ).hexdigest()
```

In `create_access_token`, add `iat` to the payload (python-jose does NOT auto-include it):

Replace the existing `to_encode.update({"exp": expire})` line with:

```python
    to_encode.update({
        "exp": expire,
        "iat": int(time.time()),   # python-jose does not auto-include iat — must add explicitly
    })
    encoded_jwt = jwt.encode(
        to_encode,
        config.jwt_secret_key,
        algorithm=config.jwt_algorithm,
    )
```

The rest of the function body (the `logger.debug(...)` and `return encoded_jwt`) stays unchanged.

- [ ] **Step 4: Run tests**

```
pytest tests/test_password_reset.py -k "hash_reset or iat" -v
pytest tests/test_auth.py -v
```

Expected: all PASS (existing auth tests should still pass)

- [ ] **Step 5: Commit**

```bash
git add app/core/auth.py tests/test_password_reset.py
git commit -m "feat: add hash_reset_token and iat claim to JWT tokens"
```

---

## Task 5: Email Service

**Files:**
- Create: `app/services/email_service.py`
- Create: `tests/test_email_service.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_email_service.py`.

Use `unittest.mock.PropertyMock` to patch the `disable_email` property directly — do NOT use module reloads (they don't reliably propagate to already-imported references):

```python
"""Tests for app/services/email_service.py."""
import logging
from unittest.mock import MagicMock, patch, PropertyMock
import pytest


def _patch_disable_email(value: bool):
    """Patch config.disable_email property for email service tests."""
    from app.core.config import Config
    return patch.object(Config, "disable_email", new_callable=PropertyMock, return_value=value)


class TestSendPasswordResetEmailDevMode:
    async def test_disable_email_skips_boto3(self):
        with _patch_disable_email(True), patch("boto3.client") as mock_boto:
            from app.services import email_service
            await email_service.send_password_reset_email(
                "user@example.com",
                "https://example.com/reset-password?token=abc123"
            )
        mock_boto.assert_not_called()

    async def test_disable_email_logs_partial_token(self, caplog):
        with _patch_disable_email(True), caplog.at_level(logging.INFO, logger="app.services.email_service"):
            from app.services import email_service
            await email_service.send_password_reset_email(
                "user@example.com",
                "https://example.com/reset-password?token=abcdefgh_rest_of_token"
            )
        assert "abcdefgh" in caplog.text
        # Full token must NOT be logged
        assert "abcdefgh_rest_of_token" not in caplog.text


class TestSendPasswordResetEmailSES:
    async def test_calls_ses_send_email_with_correct_params(self):
        mock_ses = MagicMock()
        with _patch_disable_email(False), patch("boto3.client", return_value=mock_ses):
            from app.services import email_service
            await email_service.send_password_reset_email(
                "target@example.com",
                "https://example.com/reset-password?token=mytoken"
            )

        mock_ses.send_email.assert_called_once()
        call_kwargs = mock_ses.send_email.call_args[1]
        assert call_kwargs["Destination"]["ToAddresses"] == ["target@example.com"]
        assert "mytoken" in call_kwargs["Message"]["Body"]["Text"]["Data"]

    async def test_ses_exception_propagates(self):
        import botocore.exceptions
        mock_ses = MagicMock()
        mock_ses.send_email.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "MessageRejected", "Message": "Email rejected"}},
            "SendEmail"
        )
        with _patch_disable_email(False), patch("boto3.client", return_value=mock_ses):
            from app.services import email_service
            with pytest.raises(botocore.exceptions.ClientError):
                await email_service.send_password_reset_email(
                    "bad@example.com",
                    "https://example.com/reset-password?token=x"
                )
```

- [ ] **Step 2: Run to verify tests fail**

```
pytest tests/test_email_service.py -v
```

Expected: `ModuleNotFoundError` or similar

- [ ] **Step 3: Create `app/services/email_service.py`**

```python
import logging

import boto3

from app.core.config import config

logger = logging.getLogger(__name__)


async def send_password_reset_email(to_email: str, reset_url: str) -> None:
    """
    Send a password reset email via AWS SES.

    When DISABLE_EMAIL=true (dev/CI), logs a partial token preview instead of sending.
    SES exceptions are intentionally NOT caught — callers must handle them (return 500).

    Args:
        to_email: Recipient email address.
        reset_url: Full reset URL including the raw token query parameter.
    """
    if config.disable_email:
        # Partial token only — CI logs should still be treated as sensitive.
        token_part = reset_url.split("token=")[-1][:8] + "..."
        logger.info("[DEV] Password reset for %s. Token preview: %s", to_email, token_part)
        return

    # boto3 default retry policy (up to 5 retries, exponential backoff) applies.
    # Exceptions propagate — caller returns HTTP 500.
    client = boto3.client("ses", region_name=config.aws_region)
    client.send_email(
        Source=config.ses_sender_email,
        Destination={"ToAddresses": [to_email]},
        Message={
            "Subject": {"Data": "Reset your Fitted password"},
            "Body": {
                "Text": {
                    "Data": (
                        f"Reset your password (valid 1 hour):\n\n{reset_url}\n\n"
                        "If you did not request this, ignore this email."
                    )
                },
                "Html": {
                    "Data": (
                        f"<p><a href='{reset_url}'>Click here</a> to reset your password "
                        f"(valid 1 hour).</p>"
                        f"<p>If you did not request this, ignore this email.</p>"
                    )
                },
            },
        },
    )
```

- [ ] **Step 4: Run email service tests**

```
pytest tests/test_email_service.py -v
```

Expected: all PASS

- [ ] **Step 5: Update `.env.example`**

Add dev-mode password reset vars to `.env.example`:
```
# Password reset (dev only — set DISABLE_EMAIL=true to skip SES entirely)
DISABLE_EMAIL=true
SES_SENDER_EMAIL=testertestertestingtested@gmail.com
```
The `SES_SENDER_EMAIL` env var overrides the SSM-sourced sender. In dev (`DISABLE_EMAIL=true`) it is never actually used to send — it only appears in logs if an error path leaks it.

- [ ] **Step 6: Commit**

```bash
git add app/services/email_service.py tests/test_email_service.py .env.example
git commit -m "feat: add email service with SES and DISABLE_EMAIL dev mode"
```

---

## Task 6: User Service — Reset Functions

**Files:**
- Modify: `app/services/user_service.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_password_reset.py`:

```python
# --- User service tests ---
# Uses the same _make_mock_conn / _patch_get_connection helpers as test_user_service.py
# Copy those helpers here or import from a shared location.

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

        call_args = mock_cur.execute.await_args
        assert token_hash in str(call_args)
        assert MOCK_USER_EMAIL in str(call_args)

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
        # consume_reset_token's WHERE clause requires is_active=TRUE;
        # simulate DB returning no row (as it would for inactive user)
        mock_conn, mock_cur = _make_mock_conn(fetchone_return=None)

        with _patch_get_connection(mock_conn):
            result = await user_service.reset_password("j" * 64, "$2b$hashed", datetime.now(timezone.utc))

        assert result is None
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_password_reset.py -k "StoreReset or ResetPassword" -v
```

Expected: FAILED (functions don't exist yet)

- [ ] **Step 3: Add functions to `app/services/user_service.py`**

Add these imports at the top if not present:
```python
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID
```

Add these functions at the end of the file:

```python
async def store_reset_token(email: str, token_hash: str, expires_at: datetime) -> None:
    """
    Store a HMAC-SHA256 reset token hash for a user.
    No-op if email is not found (UPDATE affects 0 rows — caller does not check row count).
    """
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET reset_token = %s, reset_token_expires_at = %s WHERE email = %s",
                (token_hash, expires_at, email),
            )
            await conn.commit()
    logger.debug("Stored reset token for email=%s", email)


async def reset_password(
    token_hash: str, new_hashed_password: str, changed_at: datetime
) -> Optional[Dict[str, Any]]:
    """
    Atomically consume a reset token and update the user's password in one transaction.

    The consume step uses UPDATE ... RETURNING to atomically validate and clear the token.
    If the token is not found, expired, or the user is inactive, returns None.
    Both operations run in a single psycopg3 transaction (no half-reset state on crash).

    Args:
        token_hash: HMAC-SHA256 hex digest of the raw token (from hash_reset_token).
        new_hashed_password: bcrypt hash of the new password.
        changed_at: datetime.now(timezone.utc) from the caller — used for password_changed_at.
                    Must use the app clock (not DB NOW()) to match JWT iat clock source.

    Returns:
        Dict with user_id and email if successful, None otherwise.
    """
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            try:
                # Step 1: Atomically consume the token (validate + clear in one statement)
                await cur.execute(
                    """
                    UPDATE users
                    SET reset_token = NULL, reset_token_expires_at = NULL
                    WHERE reset_token = %s
                      AND reset_token_expires_at > NOW()
                      AND is_active = TRUE
                    RETURNING user_id, email
                    """,
                    (token_hash,),
                )
                row = await cur.fetchone()
                if not row:
                    return None

                user_id, email = row[0], row[1]

                # Step 2: Update password and record change timestamp
                await cur.execute(
                    "UPDATE users SET hashed_password = %s, password_changed_at = %s WHERE user_id = %s",
                    (new_hashed_password, changed_at, user_id),
                )
                await conn.commit()
                logger.info("Password reset completed for user_id=%s", user_id)
                return {"user_id": user_id, "email": email}
            except Exception:
                await conn.rollback()
                logger.exception("Password reset failed — transaction rolled back.")
                return None
```

- [ ] **Step 4: Run user service tests**

```
pytest tests/test_password_reset.py -k "StoreReset or ResetPassword" -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/user_service.py tests/test_password_reset.py
git commit -m "feat: add store_reset_token and reset_password user service functions"
```

---

## Task 7: JWT Invalidation

**Files:**
- Modify: `app/core/auth.py`
- Modify: `tests/test_auth.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_password_reset.py`:

```python
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


def _make_request_with_token(token: str):
    from unittest.mock import MagicMock
    mock_request = MagicMock()
    mock_request.cookies = {"access_token": token}
    mock_request.headers = {}
    mock_request.url.path = "/test"
    return mock_request
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_password_reset.py -k "JWTInvalidation" -v
```

Expected: FAILED (no `get_password_changed_at` in auth.py yet)

- [ ] **Step 3: Update `app/core/auth.py`**

`datetime` and `Optional` are already imported in `auth.py` (lines 3–4) — no new imports needed for the helper function below.

Add a new helper function (add after `hash_reset_token`):

```python
async def get_password_changed_at(user_id: str) -> Optional[datetime]:
    """Fetch password_changed_at timestamp for a user. Returns None if never reset."""
    from app.services.db_service import get_connection
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT password_changed_at FROM users WHERE user_id = %s",
                (user_id,),
            )
            row = await cur.fetchone()
            if row:
                return row[0]  # May be None if never reset
            return None
```

Update `get_current_user_id` — add these lines after extracting `user_id` from the JWT payload and before `return user_id`:

```python
        # Check if this token was issued before a password reset
        # (rejects all sessions created before the most recent password change)
        token_iat: int = payload.get("iat", 0)
        password_changed_at = await get_password_changed_at(user_id)
        if password_changed_at is not None:
            # Use calendar.timegm() to treat naive datetime as UTC regardless of server timezone.
            # Use strict < so tokens issued at the same second as the change are allowed
            # (the new post-reset login session must not be immediately rejected)
            changed_at_ts = calendar.timegm(password_changed_at.utctimetuple())
            if token_iat < changed_at_ts:
                logger.warning(
                    "Token predates password change for user_id=%s — rejecting.", user_id
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token invalidated. Please log in again.",
                )
```

- [ ] **Step 4: Run JWT invalidation tests and full auth test suite**

```
pytest tests/test_password_reset.py -k "JWTInvalidation" -v
pytest tests/test_auth.py -v
```

Expected: all PASS. The existing auth tests should still pass because they don't mock `get_password_changed_at` (it will call the DB which isn't set up — but wait, the existing tests mock at the request level without DB, so `get_password_changed_at` will try to use the DB pool which isn't initialized).

After adding `get_password_changed_at` to `get_current_user_id`, all existing auth tests that call this path will fail because `get_password_changed_at` tries to hit the DB. Add an autouse fixture to `tests/test_auth.py` that patches it out:

Open `tests/test_auth.py` and add this fixture at module level (after the imports):

```python
import pytest
from unittest.mock import AsyncMock, patch

@pytest.fixture(autouse=True)
def _patch_password_changed_at():
    """Prevent get_password_changed_at from hitting DB in existing auth tests."""
    with patch(
        "app.core.auth.get_password_changed_at",
        new_callable=AsyncMock,
        return_value=None,  # NULL → user never reset, all tokens valid
    ):
        yield
```

This fixture is `autouse=True` so it applies to every test in `test_auth.py` automatically, without touching individual tests.

After running the full suite (Step 4 run command), if any other test file also fails because `get_password_changed_at` hits the DB, add the same autouse fixture to that test file too. Look for files that import or exercise `get_current_user_id` (e.g. `tests/test_main.py`) and apply the same pattern there.

- [ ] **Step 5: Commit**

```bash
git add app/core/auth.py tests/test_password_reset.py tests/test_auth.py
git commit -m "feat: add JWT session invalidation after password reset"
```

---

## Task 8: Add slowapi + Rate Limiter Setup

**Files:**
- Modify: `pyproject.toml`
- Modify: `requirements.txt`
- Modify: `app/main.py`

- [ ] **Step 1: Write a failing test**

Add to `tests/test_password_reset.py`:

```python
# --- Rate limiter setup test ---

def test_app_has_rate_limiter_state():
    """The FastAPI app should have a slowapi limiter attached to app.state."""
    from app.main import app
    assert hasattr(app.state, "limiter")
```

- [ ] **Step 2: Run to verify it fails**

```
pytest tests/test_password_reset.py -k "rate_limiter" -v
```

Expected: FAILED (no `limiter` on app.state yet)

- [ ] **Step 3: Add slowapi dependency**

In `pyproject.toml`, find the `dependencies = [` list and add:
```
"slowapi>=0.1.9",
```

In `requirements.txt`, add:
```
slowapi>=0.1.9
```

- [ ] **Step 4: Install it**

```
uv pip install "slowapi>=0.1.9"
```

Expected: `Installed slowapi-x.y.z` (or similar pip/uv success output with no errors)

- [ ] **Step 5: Add limiter setup to `app/main.py`**

Add to the imports section at the top of `app/main.py`:
```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
```

After the `app = FastAPI(lifespan=lifespan)` line, add:
```python
# Rate limiter — keyed by real client IP (X-Forwarded-For trusted for Caddy reverse proxy)
limiter = Limiter(key_func=get_remote_address, headers_enabled=True)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

- [ ] **Step 6: Run failing test to verify it now passes**

```
pytest tests/test_password_reset.py -k "rate_limiter" -v
```

Expected: PASS

- [ ] **Step 7: Verify the app still starts (import check)**

```
python -c "from app.main import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml requirements.txt app/main.py
git commit -m "feat: add slowapi rate limiting setup"
```

---

## Task 9: Backend Endpoints

**Files:**
- Modify: `app/main.py`
- Modify: `tests/test_password_reset.py`

- [ ] **Step 1: Write failing endpoint tests**

Add to `tests/test_password_reset.py`:

```python
# --- Endpoint tests ---

import secrets as _secrets
from unittest.mock import patch, AsyncMock


class TestForgotPasswordEndpoint:
    async def test_returns_200_for_known_active_user(self):
        from fastapi.testclient import TestClient
        from app.main import app

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

        with patch("app.services.user_service.get_user_by_email", return_value=None):
            client = TestClient(app)
            resp = client.post("/auth/forgot-password", json={"email": "nobody@example.com"})
        assert resp.status_code == 200

    async def test_returns_200_for_inactive_user(self):
        from fastapi.testclient import TestClient
        from app.main import app

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
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_password_reset.py -k "Endpoint" -v
```

Expected: FAILED (endpoints don't exist)

- [ ] **Step 3: Add endpoints to `app/main.py`**

Add these imports at the top (add to existing import block):
```python
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from app.models.user import ForgotPasswordRequest, ResetPasswordRequest
from app.services import email_service
```

Add the endpoints after `POST /auth/logout`:

```python
@app.post("/auth/forgot-password")
@limiter.limit("5/15minutes")
async def forgot_password(request: Request, body: ForgotPasswordRequest):
    """
    Request a password reset email. Always returns 200 to prevent user enumeration.
    Rate limited to 5 requests per IP per 15 minutes.
    """
    user = await user_service.get_user_by_email(body.email)
    if user and user.get("is_active"):
        raw_token = secrets.token_urlsafe(32)
        token_hash = auth.hash_reset_token(raw_token)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        await user_service.store_reset_token(body.email, token_hash, expires_at)
        reset_url = f"{config.frontend_url}/reset-password?token={raw_token}"
        await email_service.send_password_reset_email(body.email, reset_url)
    return {"message": "If that email is registered, you'll receive a reset link shortly."}


@app.post("/auth/reset-password")
@limiter.limit("10/hour")
async def reset_password(request: Request, body: ResetPasswordRequest):
    """
    Reset a user's password using a valid reset token.
    Token field validated by Pydantic (must be exactly 43 chars).
    Rate limited to 10 requests per IP per hour.
    """
    token_hash = auth.hash_reset_token(body.token)
    changed_at = datetime.now(timezone.utc)
    new_hashed = auth.get_password_hash(body.new_password)

    result = await user_service.reset_password(token_hash, new_hashed, changed_at)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset link is invalid or has expired.",
        )
    logger.info("Password reset successful for user_id=%s", result["user_id"])
    return {"message": "Password updated successfully."}
```

Note: The `forgot_password` endpoint intentionally does NOT catch SES exceptions — they propagate as 500.

- [ ] **Step 4: Run endpoint tests**

```
pytest tests/test_password_reset.py -k "Endpoint" -v
```

Expected: all PASS

- [ ] **Step 5: Run the full test suite to check for regressions**

```
pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all existing tests pass

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_password_reset.py
git commit -m "feat: add forgot-password and reset-password API endpoints"
```

---

## Task 10: Frontend Pages

**Files:**
- Modify: `frontend/app.py`

- [ ] **Step 1: Write a failing test**

Add to `tests/test_password_reset.py`:

```python
# --- Frontend tests ---
# These tests inspect source code (not live HTTP) to verify the routes exist.
# FastHTML routes are functions; calling them in tests requires a running ASGI app
# which is expensive to set up. Source inspection is sufficient to confirm the
# routes were added and contain the expected structure.

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
        # Should include an email input
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
        # Hidden field name must be "token" — token injected server-side, not by JS
        assert 'name="token"' in source or "name='token'" in source

    def test_reset_password_post_route_calls_api(self):
        """Source should define a POST /reset-password route that calls the backend."""
        source = self._get_frontend_source()
        assert "/auth/reset-password" in source
```

- [ ] **Step 2: Run to verify it fails**

```
pytest tests/test_password_reset.py -k "Frontend" -v
```

Expected: FAILED

- [ ] **Step 3: Add "Forgot your password?" link to login page in `frontend/app.py`**

Find the `@app.get("/login")` handler (around line 748). In the `Div(...)` that contains the auth-link at the bottom, add a link:

```python
# In the login page, after the existing "Don't have an account?" div, add:
Div(
    A("Forgot your password?", href="/forgot-password"),
    cls="auth-link",
),
```

The existing login form block ends around line 778. Add the forgot-password link before the closing of the outer `Div(cls="container")`.

- [ ] **Step 4: Add `/forgot-password` GET + POST routes to `frontend/app.py`**

Add after the `@app.get("/logout")` handler:

```python
@app.get("/forgot-password")
def forgot_password_page(session):
    return Title("Forgot Password - Fitted"), Body(
        nav_bar(session),
        Div(
            H2("Reset your password"),
            P("Enter your email address and we'll send you a reset link."),
            Form(
                Input(type="email", name="email", placeholder="Email address", required=True),
                Button("Send reset link", type="submit"),
                hx_post="/forgot-password",
                hx_target="#forgot-result",
                cls="auth-form",
            ),
            Div(id="forgot-result"),
            Div(
                A("Back to login", href="/login"),
                cls="auth-link",
            ),
            cls="container",
        ),
    )


@app.post("/forgot-password")
async def forgot_password(email: str, session):
    async with httpx.AsyncClient() as client:
        try:
            await client.post(
                f"{API_BASE_URL}/auth/forgot-password",
                json={"email": email},
            )
        except Exception:
            logger.error("Frontend forgot-password request to backend failed.", exc_info=True)
    # Always show the same message regardless of outcome (no enumeration)
    return P(
        "If that email is registered, you'll receive a reset link shortly.",
        id="forgot-result",
    )
```

- [ ] **Step 5: Add `/reset-password` GET + POST routes to `frontend/app.py`**

Add after the `/forgot-password` handlers:

```python
@app.get("/reset-password")
def reset_password_page(token: str, session):
    """Render the set-new-password form. Token comes from the email link query param."""
    return Title("Set New Password - Fitted"), Body(
        nav_bar(session),
        Div(
            H2("Set a new password"),
            Form(
                # Token submitted in POST body — NOT re-submitted in URL
                Input(type="hidden", name="token", value=token),
                Input(
                    type="password",
                    name="new_password",
                    placeholder="New password (8–128 characters)",
                    required=True,
                    minlength="8",
                    maxlength="128",
                ),
                Input(
                    type="password",
                    name="confirm_password",
                    placeholder="Confirm new password",
                    required=True,
                ),
                Button("Update password", type="submit"),
                hx_post="/reset-password",
                hx_target="body",
                cls="auth-form",
            ),
            Div(
                A("Back to login", href="/login"),
                cls="auth-link",
            ),
            cls="container",
        ),
    )


@app.post("/reset-password")
async def reset_password(token: str, new_password: str, confirm_password: str, session):
    if new_password != confirm_password:
        return error_message("Passwords do not match")

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{API_BASE_URL}/auth/reset-password",
                json={"token": token, "new_password": new_password},
            )
            if resp.status_code == 200:
                logger.info("Password reset completed via frontend.")
                return RedirectResponse("/login?reset=success", status_code=303)
            err = resp.json().get("detail", "Reset failed")
            logger.warning("Frontend password reset failed: status=%d detail=%s", resp.status_code, err)
            return error_message(f"{err} — try requesting a new reset link.")
        except Exception:
            logger.error("Frontend reset-password request to backend failed.", exc_info=True)
            return error_message("Reset failed: could not reach the server")
```

- [ ] **Step 6: Run frontend test**

```
pytest tests/test_password_reset.py -k "Frontend" -v
```

Expected: PASS

- [ ] **Step 7: Run full test suite**

```
pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all existing tests pass, new tests pass

- [ ] **Step 8: Commit**

```bash
git add frontend/app.py tests/test_password_reset.py
git commit -m "feat: add forgot-password and reset-password frontend pages"
```

---

## Task 11: Apply DB Migration

> **Human step required** — run this against your actual database.

- [ ] **Step 1: Run the migration against your dev/staging database**

```bash
python -c "from scripts.db_migrate import migrate_password_reset; migrate_password_reset()"
```

Expected output:
```
Connecting to database...
Applying password reset migration...
Password reset migration successful.
```

- [ ] **Step 2: Verify columns exist**

Connect to psql and run:
```sql
\d users
```

Expected: `reset_token`, `reset_token_expires_at`, `password_changed_at` columns visible.

- [ ] **Step 3: Add `DISABLE_EMAIL=true` to your `.env` for local testing**

```
DISABLE_EMAIL=true
```

> **No commit needed for Task 11** — the migration runs against the live database; `.env` is gitignored. All code changes were committed in Tasks 1–10.

---

## Post-Implementation Checklist

- [ ] Run the complete test suite: `pytest tests/ -v`
- [ ] Manually test the full happy path end-to-end (start both backend and frontend, request reset, follow link, set password, log in)
- [ ] Verify "Forgot your password?" link appears on `/login`
- [ ] Verify old session JWT is rejected after password reset (log in, note the cookie, reset password, try to use old cookie)
- [ ] Before production: request AWS SES production access and verify `ses_sender_email` in SES
- [ ] Set `FRONTEND_URL` (and optionally `SES_SENDER_EMAIL`) in SSM Parameter Store for production: `/fitted/frontend-url`, `/fitted/ses-sender-email`
