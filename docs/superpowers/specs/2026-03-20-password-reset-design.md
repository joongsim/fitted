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

Add two nullable columns to the `users` table via migration:

```sql
ALTER TABLE users ADD COLUMN reset_token VARCHAR(64);
ALTER TABLE users ADD COLUMN reset_token_expires_at TIMESTAMPTZ;
```

- `reset_token` stores the **SHA-256 hash** of the raw token (never the raw token itself)
- `reset_token_expires_at` stores the expiry timestamp (request time + 1 hour)
- Both columns are cleared to NULL after a successful password reset or when a new reset is requested

---

## New Files

| File | Purpose |
|------|---------|
| `app/services/email_service.py` | AWS SES integration — `send_password_reset_email(to_email, reset_url)` |

---

## Modified Files

| File | Changes |
|------|---------|
| `scripts/db_migrate.py` | Add migration for new `reset_token` and `reset_token_expires_at` columns |
| `app/services/user_service.py` | Add `store_reset_token`, `get_user_by_reset_token`, `update_password_and_clear_token` |
| `app/main.py` | Add `POST /auth/forgot-password` and `POST /auth/reset-password` endpoints |
| `app/models/user.py` | Add `ForgotPasswordRequest` and `ResetPasswordRequest` Pydantic models |
| `frontend/app.py` | Add "Forgot your password?" link on login page, `/forgot-password` page, `/reset-password` page |

---

## API Endpoints

### `POST /auth/forgot-password`

**Request:**
```json
{ "email": "user@example.com" }
```

**Behavior:**
1. Look up user by email
2. If not found: return 200 (no user enumeration)
3. If found:
   - Generate `secrets.token_urlsafe(32)` (raw token)
   - Compute `hashlib.sha256(token.encode()).hexdigest()` (token hash)
   - Store hash + `NOW() + 1 hour` via `store_reset_token()`
   - Send email via SES with link: `{frontend_url}/reset-password?token={raw_token}`
4. Return 200 always

**Response:**
```json
{ "message": "If that email is registered, you'll receive a reset link shortly." }
```

---

### `POST /auth/reset-password`

**Request:**
```json
{ "token": "<raw_token>", "new_password": "newpassword123" }
```

**Behavior:**
1. Hash incoming token: `sha256(token)`
2. Query DB: find user where `reset_token = hash AND reset_token_expires_at > NOW()`
3. If not found or expired: return 400
4. Validate `new_password` length (minimum 8 characters)
5. Bcrypt-hash new password
6. Update user: set `hashed_password`, clear `reset_token = NULL`, `reset_token_expires_at = NULL`
7. Return 200

**Response (success):**
```json
{ "message": "Password updated successfully." }
```

**Response (error):**
```json
{ "detail": "Reset link is invalid or has expired." }
```

---

## User Service Functions

### `store_reset_token(email: str, token_hash: str, expires_at: datetime) -> None`
```sql
UPDATE users
SET reset_token = %s, reset_token_expires_at = %s
WHERE email = %s
```

### `get_user_by_reset_token(token_hash: str) -> Optional[dict]`
```sql
SELECT user_id, email FROM users
WHERE reset_token = %s
  AND reset_token_expires_at > NOW()
  AND is_active = TRUE
```

### `update_password_and_clear_token(user_id: UUID, new_hashed_password: str) -> None`
```sql
UPDATE users
SET hashed_password = %s,
    reset_token = NULL,
    reset_token_expires_at = NULL
WHERE user_id = %s
```

---

## Email Service

**File:** `app/services/email_service.py`

```python
import boto3

async def send_password_reset_email(to_email: str, reset_url: str) -> None:
    client = boto3.client("ses", region_name=config.aws_region)
    client.send_email(
        Source=config.ses_sender_email,
        Destination={"ToAddresses": [to_email]},
        Message={
            "Subject": {"Data": "Reset your Fitted password"},
            "Body": {
                "Text": {"Data": f"Click the link below to reset your password (valid for 1 hour):\n\n{reset_url}\n\nIf you did not request this, ignore this email."},
                "Html": {"Data": f"<p>Click <a href='{reset_url}'>here</a> to reset your password (valid for 1 hour).</p><p>If you did not request this, ignore this email.</p>"}
            }
        }
    )
```

**New config values required:**
- `ses_sender_email` — verified sender address in AWS SES
- `aws_region` — already likely configured; confirm it's accessible

---

## Frontend Pages

### Login page (`/login`)
- Add "Forgot your password?" link below the login form
- Links to `/forgot-password`

### Forgot password page (`/forgot-password`)
- Simple form: email input + submit button
- On submit: `POST /auth/forgot-password` via HTMX
- Shows confirmation message: "If that email is registered, you'll receive a reset link shortly."

### Reset password page (`/reset-password`)
- Rendered on `GET /reset-password?token=<raw_token>`
- Form: new password + confirm password inputs
- Token passed as hidden field or in form action
- On submit: `POST /auth/reset-password`
- On success: redirect to `/login` with success flash message
- On error (expired/invalid): show error message with link back to `/forgot-password`

---

## Security Considerations

| Concern | Mitigation |
|---------|-----------|
| User enumeration | `POST /auth/forgot-password` always returns 200 |
| Token exposure | Only SHA-256 hash stored in DB; raw token only in email |
| Token reuse | Token columns cleared to NULL on successful reset |
| Multiple requests | New request overwrites previous token, invalidating old links |
| Token expiry | 1-hour window; `get_user_by_reset_token` filters on `expires_at > NOW()` |
| Oracle attacks | Invalid and expired tokens return identical 400 response |
| Weak passwords | Minimum 8-character validation on `new_password` |
| SES failure | Email send failure returns 500 — never silently succeeds |

---

## Testing Plan

- **Unit tests:**
  - `store_reset_token` — verify hash and expiry stored correctly
  - `get_user_by_reset_token` — valid token, expired token, wrong token
  - `update_password_and_clear_token` — verify password updated and token columns null

- **Integration tests:**
  - `POST /auth/forgot-password`: known email, unknown email (both return 200)
  - `POST /auth/reset-password`: valid token, expired token, already-used token, invalid token
  - Full happy path: request → reset → login with new password

- **Mocking:** SES calls mocked via `unittest.mock.patch("app.services.email_service.boto3.client")`
