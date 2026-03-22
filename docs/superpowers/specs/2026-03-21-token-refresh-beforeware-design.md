# Token Refresh Beforeware — Design Spec

**Date:** 2026-03-21
**Branch:** separate feature branch from preferences work

---

## Problem

JWTs are 24-hour tokens stored in an httponly session cookie. The frontend's page-level guards only check `if "access_token" not in session` — they don't verify whether the token is still valid. When a token expires mid-session the user gets a 401 from the API with no redirect, as seen in production logs.

---

## Goal

- Silently refresh tokens for active users before they expire
- Redirect expired/unrecoverable sessions to `/login` cleanly

---

## Approach

FastHTML `beforeware` on the frontend (Approach A). A single hook runs before every request, checks token expiry, and refreshes proactively. One place to maintain; no per-route changes needed.

---

## Backend: `POST /auth/refresh`

**File:** `app/main.py`

- Accepts the current token from the `access_token` cookie or `Authorization: Bearer` header
- Decodes and validates it (must not be expired, must pass the existing `iat` < `password_changed_at` check)
- Issues a new token with a fresh 24h expiry
- Returns the new token as JSON and sets a new `access_token` httponly cookie
- Returns 401 if the token is expired or invalid — no grace period

Response shape mirrors `/auth/login`:
```json
{"access_token": "<new_jwt>", "token_type": "bearer"}
```

---

## Frontend: `refresh_token_if_needed` beforeware

**File:** `frontend/app.py`

Registered as a FastHTML `beforeware`. Runs before every request handler.

### Logic

1. If no `access_token` in session → skip (unauthenticated routes handle themselves)
2. Decode the JWT payload by base64-decoding the middle segment (no crypto — just reading `exp`)
3. If token expires in **> 60 minutes** → do nothing
4. If token expires in **≤ 60 minutes** (or is already expired) → call `POST /auth/refresh`
   - **Success (200):** update `session["access_token"]` with new token; set new cookie on response
   - **Failure (401):** clear `session["access_token"]`; return `RedirectResponse("/login")`
5. If the backend is **unreachable**: log a warning; let the request proceed with the existing token

Because the beforeware runs before all route handlers, the token in session is always fresh when HTMX fragments like `/get-recommendations` fire.

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Token already expired when beforeware runs | Refresh attempted → 401 → session cleared → redirect `/login` |
| Backend unreachable during refresh | Warning logged; request proceeds; API may return 401 → existing error message shown |
| Malformed JWT in session (truncated cookie) | `base64` decode fails → treat as expired → session cleared → redirect `/login` |
| Password-reset invalidation | Backend's existing `iat < password_changed_at` check returns 401 on refresh → session cleared → redirect `/login` |

---

## Testing

- **Backend unit test (`POST /auth/refresh`):**
  - Valid token → 200, new token returned, cookie set
  - Expired token → 401
  - Token invalidated by password reset → 401

- **Frontend unit test (`refresh_token_if_needed`):**
  - Token expiring in < 60 min → refresh called, session updated
  - Token expiring in > 60 min → no refresh call
  - Token already expired + 401 from refresh → session cleared
  - Backend unreachable → request proceeds, warning logged

- Existing auth tests unaffected (no changes to login/logout/decode flow)

---

## Out of Scope

- Refresh tokens (long-lived) — not needed given 24h access token lifetime
- HTMX-specific 401 redirect handling — beforeware makes this unnecessary
- Style preferences display — separate feature branch
