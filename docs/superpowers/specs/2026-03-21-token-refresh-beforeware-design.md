# Token Refresh Beforeware — Design Spec

**Date:** 2026-03-21
**Branch:** separate feature branch from preferences work

---

## Problem

JWTs are 24-hour tokens stored in an httponly session cookie. The frontend's page-level guards only check `if "access_token" not in session` — they don't verify whether the token is still valid. When a token expires mid-session the user gets a 401 from the API with no redirect, as seen in production logs.

---

## Goal

- Silently refresh tokens for active users before they expire
- Redirect expired/unrecoverable sessions to `/login` cleanly, including from HTMX partial requests

---

## Approach

FastHTML `beforeware` on the frontend (Approach A). A single hook runs before every request, checks token expiry, and refreshes proactively. One place to maintain; no per-route changes needed.

---

## Backend: `POST /auth/refresh`

**File:** `app/main.py`

- Accepts the current token from the `access_token` cookie or `Authorization: Bearer` header (cookie takes precedence if both are present)
- Decodes and validates it:
  - Token must **not** be expired
  - Token must pass the existing `iat < password_changed_at` check (rejects tokens issued before a password reset; requires a DB lookup on `users.password_changed_at`)
  - User must exist and be active (DB lookup on `users.is_active`)
- Issues a new token carrying the same `sub` (user_id) claim with a fresh 24h expiry
- Sets a new `access_token` httponly cookie with the same attributes as login: `httponly=True`, `samesite="lax"`, `secure=False`, `max_age` and `expires` set to `access_token_expire_minutes * 60`
- Returns 401 if the token is expired, invalid, or the user is inactive — no grace period

Response shape (mirrors `/auth/login`):
```json
{"access_token": "<new_jwt>", "token_type": "bearer"}
```

---

## Frontend: `refresh_token_if_needed` beforeware

**File:** `frontend/app.py`

Registered as a FastHTML `beforeware`. Runs before every request handler.

### Logic

1. If no `access_token` in session → skip (unauthenticated routes handle themselves)
2. Decode the JWT payload by base64-decoding the middle segment — reads `exp` only, no crypto verification
3. If decode fails (malformed token) → treat as expired: clear `session["access_token"]`, redirect to `/login`
4. If token is **already expired** → clear `session["access_token"]`, redirect to `/login` (no refresh attempt)
5. If token expires in **> 60 minutes** → do nothing
6. If token expires in **≤ 60 minutes** (but not yet expired) → call `POST /auth/refresh` with a **3-second timeout**:
   - **Success (200):** read `access_token` from JSON response body; update `session["access_token"]`; the response cookie is set automatically by the backend
   - **Failure (401):** clear `session["access_token"]`; redirect to `/login`
   - **Unreachable** (connection error, network error, or timeout): log a warning; let the request proceed with the existing token

### HTMX redirect handling

When redirecting to `/login`, check for the `HX-Request: true` header. If present (HTMX partial request), return a response with header `HX-Redirect: /login` and status 200 instead of a `RedirectResponse`, so the browser performs a full-page navigation rather than inserting the login page HTML into a partial target.

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Token already expired | Session cleared → redirect `/login` (HTMX-aware) |
| Token ≤ 60 min to expiry, refresh succeeds | Session updated silently |
| Token ≤ 60 min to expiry, refresh returns 401 | Session cleared → redirect `/login` (HTMX-aware) |
| Backend unreachable during refresh (connection error / 3s timeout) | Warning logged; request proceeds with old token |
| Malformed JWT in session | Treated as expired → session cleared → redirect `/login` (HTMX-aware) |
| Password-reset invalidation | Refresh returns 401 → session cleared → redirect `/login` (HTMX-aware) |

---

## Testing

**Backend (`POST /auth/refresh`):**
- Valid token → 200, new token in body, cookie set with correct attributes
- Expired token → 401
- Token invalidated by password reset (`iat < password_changed_at`) → 401
- Inactive user → 401

**Frontend (`refresh_token_if_needed` beforeware):**
- Token expiring in < 60 min → refresh called, session updated with new token
- Token expiring in > 60 min → no refresh call
- Already expired token → session cleared, redirect `/login`
- Expired token + HTMX request → `HX-Redirect: /login` response
- Backend unreachable (mock timeout) → request proceeds, warning logged
- Malformed JWT → session cleared, redirect `/login`

Existing auth tests unaffected (no changes to login/logout/decode flow).

---

## Out of Scope

- Refresh tokens (long-lived) — not needed given 24h access token lifetime
- Style preferences display — separate feature branch
