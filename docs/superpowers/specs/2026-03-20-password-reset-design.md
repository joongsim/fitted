# Password Reset Feature — Design Spec

**Date:** 2026-03-20
**Status:** Approved

---

## Overview

Add a password reset flow to the Fitted app. Users who forget their password can request a reset email containing a time-limited link. Clicking the link takes them to a form where they set a new password.

---

## Tech Stack Context

- **Backend:** FastAPI (Python), raw async SQL (psycopg3), PostgreSQL on AWS RDS
- **Frontend:** FastHTML + HTMX
- **Auth:** JWT (PyJWT) + bcrypt
- **Email:** AWS SES via boto3 (new — no email infrastructure currently exists)

---

## Data Model Changes

```sql
-- Up migration
ALTER TABLE users ADD COLUMN reset_token VARCHAR(64);
ALTER TABLE users ADD COLUMN reset_token_expires_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN password_changed_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_users_reset_token ON users(reset_token) WHERE reset_token IS NOT NULL;

-- Down migration (rollback)
DROP INDEX IF EXISTS idx_users_reset_token;
ALTER TABLE users DROP COLUMN IF EXISTS password_changed_at;
ALTER TABLE users DROP COLUMN IF EXISTS reset_token_expires_at;
ALTER TABLE users DROP COLUMN IF EXISTS reset_token;
```

Column notes:
- `reset_token` — HMAC-SHA256 hex digest (64 chars, `.hexdigest()` encoding) of the raw token, keyed with `config.jwt_secret_key`. Never stores the raw token.
- `reset_token_expires_at` — Expiry timestamp: `datetime.utcnow() + timedelta(hours=1)` at time of request.
- `password_changed_at` — Set to `datetime.utcnow()` (application layer) on every successful password change. NULL for users who have never reset. Used to invalidate active JWTs. Using the application clock (not DB `NOW()`) ensures the same clock source as JWT `iat`.
- Both reset columns are cleared to NULL after a successful reset or when a new reset is requested.

---

## Token Hashing

Reset tokens are hashed before storage using **HMAC-SHA256** keyed with `config.jwt_secret_key`, encoded as a 64-character hex string:

```python
import hmac, hashlib

def hash_reset_token(raw_token: str) -> str:
    return hmac.new(
        config.jwt_secret_key.encode(),
        raw_token.encode(),
        hashlib.sha256
    ).hexdigest()  # always 64 hex chars — matches VARCHAR(64)
```

**Secret key:** Uses the existing `JWT_SECRET_KEY` environment variable (via `config.jwt_secret_key`). This key must already be present and non-empty at startup (enforced by existing config validation). No new secret is introduced. Recommended minimum entropy: 32 random bytes (256 bits), base64-encoded — consistent with existing JWT requirements.

**Critical:** The HMAC hash (`hash_reset_token(raw_token)`) must be computed in the application layer **before** passing to any database query. The raw token is never passed to SQL. The stored value in `reset_token` is always the 64-char hex output of `hash_reset_token()`.

**Rationale:** Defense-in-depth. Plain SHA-256 is also computationally infeasible to brute-force against 256-bit token entropy (`secrets.token_urlsafe(32)`). HMAC adds server-secret binding so DB contents alone cannot be used to verify candidate tokens.

---

## New Files

| File | Purpose |
|------|---------|
| `app/services/email_service.py` | AWS SES integration — `send_password_reset_email(to_email, reset_url)` |

---

## Modified Files

| File | Changes |
|------|---------|
| `scripts/db_migrate.py` | Add up/down migration for new columns and index |
| `app/services/user_service.py` | Add `store_reset_token`, `consume_reset_token`, `update_password_and_clear_token` |
| `app/main.py` | Add `POST /auth/forgot-password` and `POST /auth/reset-password` with rate limiting |
| `app/models/user.py` | Add `ForgotPasswordRequest` and `ResetPasswordRequest` Pydantic models |
| `app/core/auth.py` | Update `get_current_user_id` to reject tokens issued before `password_changed_at` |
| `app/core/config.py` | Add `ses_sender_email`, `aws_region`, `disable_email` config fields |
| `frontend/app.py` | Add "Forgot your password?" link on login, `/forgot-password` page, `/reset-password` page |

---

## API Endpoints

### `POST /auth/forgot-password`

**Rate limit:** 5 requests per IP per 15 minutes (via `slowapi`). Exceeding returns 429.

**Request:**
```json
{ "email": "user@example.com" }
```

**Behavior:**
1. Apply rate limit (keyed by client IP — see Proxy Headers section)
2. Look up user by email
3. If not found: return 200 (no user enumeration)
4. If found but `is_active = FALSE`: return 200 silently (see Known Tradeoffs)
5. If found and active:
   - Generate raw token: `secrets.token_urlsafe(32)` (32 bytes / 256 bits CSPRNG entropy)
   - Compute hash: `hash_reset_token(raw_token)` → 64-char hex string
   - Store hash + `datetime.utcnow() + timedelta(hours=1)` via `store_reset_token(email, hash, expires_at)`
   - `store_reset_token` is a no-op if the email is not found (UPDATE affects 0 rows); caller does not inspect row count
   - Build reset URL: `{config.frontend_url}/reset-password?token={raw_token}` — must be `https://` in non-dev
   - Call `send_password_reset_email()`; if SES raises, return 500 (see Email Service)
6. Return 200 always

**Multiple requests in window:** Each non-rate-limited request overwrites the previous token and sends a new email. Only the most recently issued link is valid.

**Response:**
```json
{ "message": "If that email is registered, you'll receive a reset link shortly." }
```

---

### `POST /auth/reset-password`

**Rate limit:** 10 requests per IP per hour (via `slowapi`).

**Request:**
```json
{ "token": "<raw_token>", "new_password": "newpassword123" }
```

**Behavior:**
1. Validate `token` field: reject immediately with 400 if length is not exactly 43 characters (the fixed output length of `secrets.token_urlsafe(32)`). This prevents large-body DoS before any HMAC computation.
2. Compute `hash_reset_token(token)` — passes the 43-char raw token string; produces the 64-char hex hash to query against
3. **Atomically consume the token** via `consume_reset_token(token_hash)` — the **hashed** value is passed to SQL, never the raw token (see User Service)
4. If no row returned: return 400 `"Reset link is invalid or has expired."` (identical for all failure modes)
5. Validate `new_password`: min 8 chars, max 128 chars
6. Within a **single database transaction**: bcrypt-hash new password, then call `update_password_and_clear_token(user_id, new_hash)`. This ensures the password update and `password_changed_at` are committed atomically — no half-reset state if the application crashes between the two operations.
7. Return 200

**Response (success):** `{ "message": "Password updated successfully." }`

**Response (error):** `{ "detail": "Reset link is invalid or has expired." }`

---

## JWT Session Invalidation After Password Reset

After a successful password reset, active JWTs for that user are invalidated by comparing `iat` against `password_changed_at`.

**Implementation in `app/core/auth.py` (`get_current_user_id`):**
1. Decode JWT, extract `user_id` and `iat` (epoch int)
2. Fetch `password_changed_at` from DB
3. If `password_changed_at IS NULL`: allow (user has never reset — all tokens valid)
4. If `token_iat < password_changed_at` (strict less-than): return 401 "Token invalidated. Please log in again."
5. Otherwise: allow

**Why strict `<` (not `<=`):** Using `<=` would reject the new JWT a user receives immediately after logging in post-reset, since `iat` could equal `password_changed_at` to the second. Strict `<` correctly allows tokens issued at or after the reset timestamp. Tokens issued before the reset are always rejected.

**Note:** PyJWT includes `iat` automatically on `create_access_token`. No changes needed there.

---

## User Service Functions

### `store_reset_token(email: str, token_hash: str, expires_at: datetime) -> None`

No-op if email not found (UPDATE affects 0 rows). Caller does not inspect row count.

```sql
UPDATE users
SET reset_token = %s, reset_token_expires_at = %s
WHERE email = %s
```

### `consume_reset_token(token_hash: str) -> Optional[dict]`

Atomically validates and clears the token in a single statement, preventing race conditions from concurrent requests:

```sql
UPDATE users
SET reset_token = NULL, reset_token_expires_at = NULL
WHERE reset_token = %s
  AND reset_token_expires_at > NOW()
  AND is_active = TRUE
RETURNING user_id, email
```

Returns the user row if token was valid and consumed, or `None` if not found/expired/inactive.

### `update_password_and_clear_token(user_id: UUID, new_hashed_password: str, changed_at: datetime) -> None`

`changed_at` is computed in the application layer (e.g. `datetime.utcnow()`) and passed in — **not delegated to database `NOW()`** — so the clock source is consistent with JWT `iat` (also set in Python). This ensures the `token_iat < password_changed_at` comparison uses the same clock reference.

```sql
UPDATE users
SET hashed_password = %s,
    password_changed_at = %s
WHERE user_id = %s
```

(Reset columns already cleared by `consume_reset_token`.)

**Transaction:** The call to `consume_reset_token` and `update_password_and_clear_token` must execute within a single psycopg3 transaction to prevent a half-reset state on application crash between the two statements.

---

## Email Service

**File:** `app/services/email_service.py`

```python
async def send_password_reset_email(to_email: str, reset_url: str) -> None:
    if config.disable_email:
        # Dev/CI mode: never log the full token — partial preview only.
        # CI stdout/build logs should still be treated as sensitive.
        token_part = reset_url.split("token=")[-1][:8] + "..."
        logger.info(f"[DEV] Password reset for {to_email}. Token preview: {token_part}")
        return

    # boto3 default retry config (up to 5 retries, exponential backoff) is accepted.
    # This may hold the request open for several seconds on transient SES errors.
    # Exceptions are intentionally NOT caught — caller returns 500.
    client = boto3.client("ses", region_name=config.aws_region)
    client.send_email(
        Source=config.ses_sender_email,
        Destination={"ToAddresses": [to_email]},
        Message={
            "Subject": {"Data": "Reset your Fitted password"},
            "Body": {
                "Text": {"Data": f"Reset your password (valid 1 hour):\n\n{reset_url}\n\nIf you did not request this, ignore this email."},
                "Html": {"Data": f"<p><a href='{reset_url}'>Click here</a> to reset your password (valid 1 hour).</p><p>If you did not request this, ignore this email.</p>"}
            }
        }
    )
```

**New config values:**
- `ses_sender_email` — Verified sender address in AWS SES
- `aws_region` — AWS region (likely already present)
- `disable_email` — Boolean from `DISABLE_EMAIL` env var; set `true` for local dev / CI

**SES error handling:** Exceptions propagate to the endpoint, which returns 500. The reset token was stored before the send attempt; on failure it remains valid for its 1-hour window. The user may retry the forgot-password request, which overwrites the token.

**boto3 retry behavior:** Default boto3 retry policy (up to 5 attempts, exponential backoff) is accepted. On persistent SES failure the request may be held for several seconds before returning 500. This is acceptable given the low request volume.

---

## Rate Limiting

Use `slowapi>=0.1.9`:
- `POST /auth/forgot-password`: `@limiter.limit("5/15minutes")`
- `POST /auth/reset-password`: `@limiter.limit("10/hour")`
- Both keyed by client IP via `get_remote_address`

**Proxy headers:** The app runs behind Caddy (reverse proxy). Configure slowapi to trust `X-Forwarded-For` so the real client IP is used, not the proxy IP:

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, headers_enabled=True)
# Set FORWARDED_ALLOW_IPS or configure trusted proxy in uvicorn/FastAPI
```

Without this, all requests appear to come from the proxy IP and the rate limit is effectively shared across all users.

---

## Frontend Pages

### Login page (`/login`)
- Add "Forgot your password?" link below the form → `/forgot-password`

### Forgot password page (`/forgot-password`)
- Email input + submit button
- HTMX `POST /auth/forgot-password`
- Shows: "If that email is registered, you'll receive a reset link shortly."

### Reset password page (`/reset-password`)
- `GET /reset-password?token=<raw_token>` — FastHTML renders this page **server-side**, populating a hidden `<input name="token" value="{token}">` from the query parameter. The token is not injected by client-side JS.
- The raw token appears in the URL query string (browser history, access logs — see Known Tradeoffs)
- Form: new password + confirm password inputs + hidden token field
- On submit: `POST /auth/reset-password` with `{token, new_password}` in POST body (token never re-submitted in URL)
- On success: redirect to `/login` with success flash
- On error: show error message with link back to `/forgot-password`

---

## Security Considerations

| Concern | Mitigation |
|---------|-----------|
| User enumeration | `forgot-password` always returns 200 |
| Token storage | HMAC-SHA256 hex (`.hexdigest()`) with server secret; raw token only in email |
| Token reuse / race condition | Atomic `UPDATE ... RETURNING` in `consume_reset_token` |
| Multiple requests | Each request overwrites token; only latest link valid |
| Token expiry | 1 hour; enforced in `consume_reset_token` via `expires_at > NOW()` |
| Oracle attacks | Identical 400 for invalid and expired tokens |
| Weak passwords | Min 8, max 128 chars |
| Email spam abuse | Rate limit: 5 / 15 min per real client IP |
| Reset brute-force | Rate limit: 10 / hour per real client IP |
| Proxy IP collapse | `X-Forwarded-For` trusted via slowapi proxy config |
| Active session eviction | JWT `iat < password_changed_at` → 401; strict `<` preserves new post-reset session |
| NULL password_changed_at | Treated as "never reset" — all tokens allowed |
| HTTPS | `config.frontend_url` must be `https://` in staging/production |
| SES failure | Propagates as 500; never silently succeeds |
| DOM token injection | Hidden token field populated server-side by FastHTML, not client JS |
| Oversized token DoS | Token field validated as exactly 43 chars before HMAC computation |
| Clock skew on JWT invalidation | `password_changed_at` set in Python (`datetime.utcnow()`), same source as JWT `iat` |
| Half-reset state on crash | `consume_reset_token` + `update_password_and_clear_token` wrapped in single transaction |

---

## Known Tradeoffs

**Token in URL:** Raw token in `/reset-password?token=...` appears in browser history and server access logs. Industry-standard practice; mitigated by single-use token (atomically consumed on first valid use), 1-hour expiry, and HTTPS. Token is NOT re-submitted in the URL on form POST.

**Server log token exposure (DISABLE_EMAIL mode):** Only the first 8 chars of the token are logged. CI/dev logs should still be treated as sensitive.

**Inactive user silent skip:** Inactive accounts cannot reset their password. They receive the confirmation message but no email is sent.

**bcrypt 72-byte truncation:** Mitigated by 128-char max password length at the API boundary.

**SES failure with stored token:** Token persists in DB for 1 hour on SES failure. User can retry; new request overwrites it.

**boto3 retries:** Default retry policy may hold the request for several seconds on transient SES errors. Acceptable for this use case.

---

## Operational Requirements

**AWS SES sandbox:** Request production access before deployment. During staging, all recipient addresses must be verified in SES.

**Verified sender:** `ses_sender_email` must be verified in AWS SES before any email can be sent.

---

## Testing Plan

**Unit tests:**
- `store_reset_token` — verify HMAC hex hash and expiry stored; no-op on unknown email
- `consume_reset_token` — valid token (returns user dict, clears columns), expired token (returns None), wrong token (returns None), inactive user (returns None), concurrent calls (second call returns None)
- `update_password_and_clear_token` — verify `hashed_password` updated and `password_changed_at` set

**Integration tests:**
- `POST /auth/forgot-password`: active user, unknown email, inactive user (all 200)
- `POST /auth/reset-password`: valid token, expired token, already-used token, invalid token, password too short, password too long (>128), token field wrong length (not 43 chars → 400 before HMAC)
- JWT invalidation: reset → old JWT returns 401; new JWT after login succeeds
- NULL `password_changed_at`: existing user (no reset history) can use their JWT
- Rate limiting: >5 requests / 15 min on forgot-password → 429; >10/hr on reset-password → 429
- Full happy path: request reset → use link → set password → login with new password

**Mocking:**
- Set `DISABLE_EMAIL=true` in test env (no SES calls needed for happy-path tests)
- SES error path: mock `boto3.client` to raise `botocore.exceptions.ClientError`, verify endpoint returns 500
