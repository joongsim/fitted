# Token Refresh Beforeware Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `POST /auth/refresh` backend endpoint and a FastHTML `beforeware` that silently refreshes tokens near expiry, and redirects expired/unrecoverable sessions to `/login`.

**Architecture:** The backend validates the current JWT, checks user is still active, and issues a fresh 24h token. The frontend beforeware runs before every request, decodes the JWT `exp` claim without crypto, and proactively calls the refresh endpoint when ≤ 60 minutes remain. Expired tokens and failed refreshes trigger an HTMX-aware redirect to `/login`.

**Tech Stack:** FastAPI, python-jose, FastHTML `Beforeware`, httpx, pytest `TestClient`

---

## File Map

| File | Change |
|---|---|
| `app/main.py` | Add `POST /auth/refresh` endpoint after `/auth/logout` |
| `frontend/app.py` | Add `import base64, json, time`; add `_decode_jwt_exp`, `_login_redirect`, `refresh_token_if_needed`; pass `before=Beforeware(...)` to `AppClass(...)` |
| `tests/test_api_endpoints.py` | Add `class TestAuthRefresh` |
| `tests/test_frontend.py` | Add `class TestRefreshTokenBeforeware` |

---

## Task 1: Backend — `POST /auth/refresh` (TDD)

**Files:**
- Modify: `tests/test_api_endpoints.py`
- Modify: `app/main.py` (after line ~216, the `/auth/logout` endpoint)

### Reference: how existing endpoint tests are structured

Before writing tests, read `tests/test_api_endpoints.py:46-90` to understand the `client` fixture and `_auth_headers` helper, and `tests/test_api_endpoints.py:82-90` for the autouse `_patch_password_changed_at` fixture (it patches `app.core.auth.get_password_changed_at` to return `None`, preventing DB hits).

Also read `app/main.py:166-209` (`/auth/login`) — the refresh endpoint mirrors its cookie-set logic.

---

- [ ] **Step 1: Write the failing tests**

Add `class TestAuthRefresh` to `tests/test_api_endpoints.py`:

```python
# ---------------------------------------------------------------------------
# POST /auth/refresh
# ---------------------------------------------------------------------------


class TestAuthRefresh:
    def test_valid_token_returns_new_token_and_sets_cookie(self, client):
        from app.core.auth import create_access_token
        from app.core.config import config

        token = create_access_token({"sub": MOCK_USER_ID})

        with patch(
            "app.main.user_service.get_user_by_id",
            new_callable=AsyncMock,
            return_value=MOCK_USER_OBJ,
        ):
            resp = client.post("/auth/refresh", cookies={"access_token": token})

        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        # Cookie must be (re)set
        assert "access_token" in resp.cookies

    def test_expired_token_returns_401(self, client):
        from datetime import datetime, timedelta
        from jose import jwt as _jwt

        expired = _jwt.encode(
            {"sub": MOCK_USER_ID, "exp": datetime.utcnow() - timedelta(hours=1)},
            "dev-secret-key-change-me-in-prod",
            algorithm="HS256",
        )
        resp = client.post("/auth/refresh", cookies={"access_token": expired})
        assert resp.status_code == 401

    def test_password_reset_invalidated_token_returns_401(self, client):
        from datetime import datetime, timedelta
        from jose import jwt as _jwt

        token = _jwt.encode(
            {
                "sub": MOCK_USER_ID,
                "exp": datetime.utcnow() + timedelta(hours=2),
                "iat": 1000,   # issued at t=1000
            },
            "dev-secret-key-change-me-in-prod",
            algorithm="HS256",
        )
        changed_at = datetime.utcfromtimestamp(2000)   # changed after token issued
        # Note: the file-scoped autouse _patch_password_changed_at fixture returns None.
        # The nested patch below is innermost and takes precedence over it during this test.

        with patch(
            "app.core.auth.get_password_changed_at",
            new_callable=AsyncMock,
            return_value=changed_at,
        ):
            with patch(
                "app.main.user_service.get_user_by_id",
                new_callable=AsyncMock,
                return_value=MOCK_USER_OBJ,
            ):
                resp = client.post("/auth/refresh", cookies={"access_token": token})

        assert resp.status_code == 401

    def test_inactive_user_returns_401(self, client):
        from app.core.auth import create_access_token
        from unittest.mock import MagicMock

        token = create_access_token({"sub": MOCK_USER_ID})
        inactive_user = MagicMock()
        inactive_user.is_active = False

        with patch(
            "app.main.user_service.get_user_by_id",
            new_callable=AsyncMock,
            return_value=inactive_user,
        ):
            resp = client.post("/auth/refresh", cookies={"access_token": token})

        assert resp.status_code == 401

    def test_missing_token_returns_401(self, client):
        resp = client.post("/auth/refresh")
        assert resp.status_code == 401

    def test_bearer_header_accepted_when_no_cookie(self, client):
        from app.core.auth import create_access_token

        token = create_access_token({"sub": MOCK_USER_ID})

        with patch(
            "app.main.user_service.get_user_by_id",
            new_callable=AsyncMock,
            return_value=MOCK_USER_OBJ,
        ):
            resp = client.post(
                "/auth/refresh",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=. pytest tests/test_api_endpoints.py::TestAuthRefresh -v
```

Expected: all 6 tests FAIL with `404 Not Found` (endpoint doesn't exist yet).

- [ ] **Step 3: Implement `POST /auth/refresh` in `app/main.py`**

Add after the `/auth/logout` endpoint (around line 216). The endpoint needs `calendar` imported — add it to the top-of-file imports alongside the existing `from datetime import datetime, timedelta, timezone`.

Add to imports at top of `app/main.py`:
```python
import calendar
```

Add endpoint after `/auth/logout`:

```python
@app.post("/auth/refresh", response_model=Token)
async def refresh_token(request: Request, response: Response):
    """
    Issue a new access token if the current one is still valid.
    Accepts the token from the access_token cookie (preferred) or Authorization header.
    Returns 401 if the token is expired, invalid, or the user is inactive.
    """
    # 1. Extract token — cookie takes precedence
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 2. Decode and verify (jose raises JWTError if expired)
    try:
        payload = jwt.decode(
            token,
            config.jwt_secret_key,
            algorithms=[config.jwt_algorithm],
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id: str = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing sub",
        )

    # 3. Reject tokens issued before a password reset
    token_iat: int = payload.get("iat", 0)
    password_changed_at = await auth.get_password_changed_at(user_id)
    if password_changed_at is not None:
        changed_at_ts = calendar.timegm(password_changed_at.utctimetuple())
        if token_iat < changed_at_ts:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token invalidated. Please log in again.",
            )

    # 4. Confirm user is still active
    user = await user_service.get_user_by_id(user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    # 5. Issue fresh token and set cookie
    new_token = auth.create_access_token(data={"sub": user_id})
    response.set_cookie(
        key="access_token",
        value=new_token,
        httponly=True,
        max_age=config.access_token_expire_minutes * 60,
        expires=config.access_token_expire_minutes * 60,
        samesite="lax",
        secure=False,
    )
    logger.info("Token refreshed for user_id=%s", user_id)
    return {"access_token": new_token, "token_type": "bearer"}
```

Note: `app/main.py` does **not** currently import `jwt` or `JWTError` directly (those live in `app/core/auth.py`). Add this import to the top of `app/main.py` with the other third-party imports:

```python
from jose import JWTError, jwt
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=. pytest tests/test_api_endpoints.py::TestAuthRefresh -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
PYTHONPATH=. pytest tests/test_api_endpoints.py tests/test_auth.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_api_endpoints.py
git commit -m "feat: add POST /auth/refresh endpoint"
```

---

## Task 2: Frontend — `refresh_token_if_needed` beforeware (TDD)

**Files:**
- Modify: `tests/test_frontend.py`
- Modify: `frontend/app.py`

### Reference: how frontend tests work

Read `tests/test_frontend.py:63-90` — the `app`, `client`, and `authed_client` fixtures. The `authed_client` fixture injects a session token via the `/login` endpoint. For beforeware tests, you'll inject real JWTs with specific `exp` values directly by seeding the session differently (see Step 1 below for the helper).

FastHTML uses Starlette's `SessionMiddleware`. The `TestClient` carries cookies between requests. To inject a session with a specific token, post to `/login` mocking the backend to return a known JWT.

---

- [ ] **Step 1: Write the failing tests**

Add `class TestRefreshTokenBeforeware` to `tests/test_frontend.py`. The tests need a helper that creates a real JWT with a specific expiry so you can control the timing:

```python
# ---------------------------------------------------------------------------
# Helpers for beforeware tests
# ---------------------------------------------------------------------------

def _make_jwt_with_exp(exp_offset_seconds: int) -> str:
    """Return a signed JWT whose exp is now + exp_offset_seconds."""
    import time
    from jose import jwt as _jwt

    payload = {
        "sub": "test-user-uuid",
        "exp": int(time.time()) + exp_offset_seconds,
        "iat": int(time.time()),
    }
    # Must match the frontend app's SESSION_SECRET / API_BASE_URL behaviour.
    # The beforeware only base64-decodes exp — it does NOT verify the signature.
    # Use any secret; the beforeware won't reject it.
    return _jwt.encode(payload, "any-secret", algorithm="HS256")


def _make_authed_client_with_token(app, token: str):
    """Return a TestClient whose session contains the given access_token."""
    tc = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    with patch("httpx.AsyncClient") as mock_http:
        mock_instance = AsyncMock()
        mock_http.return_value.__aenter__.return_value = mock_instance
        mock_instance.post.return_value = _make_http_response(
            200, {"access_token": token}
        )
        tc.post("/login", data={"username": "u@example.com", "password": "pw"})
    return tc
```

Then add the test class:

```python
# ---------------------------------------------------------------------------
# Beforeware: refresh_token_if_needed
# ---------------------------------------------------------------------------


class TestRefreshTokenBeforeware:
    def test_token_far_from_expiry_is_not_refreshed(self, app):
        """Token with >60 min remaining — no refresh call should be made."""
        token = _make_jwt_with_exp(7200)  # 2 hours out
        tc = _make_authed_client_with_token(app, token)

        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            tc.get("/wardrobe")

        mock_instance.post.assert_not_called()

    def test_token_near_expiry_triggers_refresh_and_updates_session(self, app):
        """Token with <60 min remaining — refresh endpoint is called."""
        token = _make_jwt_with_exp(1800)  # 30 min out
        new_token = _make_jwt_with_exp(86400)
        tc = _make_authed_client_with_token(app, token)

        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.return_value = _make_http_response(
                200, {"access_token": new_token}
            )
            resp = tc.get("/wardrobe")

        mock_instance.post.assert_called_once()
        call_url = mock_instance.post.call_args[0][0]
        assert "/auth/refresh" in call_url

    def test_already_expired_token_clears_session_and_redirects(self, app):
        """Token already past exp — redirect to /login, no refresh attempt."""
        token = _make_jwt_with_exp(-3600)  # expired 1 hour ago
        tc = _make_authed_client_with_token(app, token)

        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            resp = tc.get("/wardrobe")

        mock_instance.post.assert_not_called()
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers.get("location", "")

    def test_expired_token_htmx_request_returns_hx_redirect(self, app):
        """Expired token on an HTMX partial — HX-Redirect header, not 3xx."""
        token = _make_jwt_with_exp(-3600)
        tc = _make_authed_client_with_token(app, token)

        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            resp = tc.post(
                "/get-recommendations",
                data={"location": "London"},
                headers={"HX-Request": "true"},
            )

        assert resp.status_code == 200
        assert resp.headers.get("HX-Redirect") == "/login"

    def test_refresh_returns_401_clears_session_and_redirects(self, app):
        """Near-expiry token + backend returns 401 — redirect to /login."""
        token = _make_jwt_with_exp(1800)
        tc = _make_authed_client_with_token(app, token)

        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.return_value = _make_http_response(401, {"detail": "Token invalidated"})
            resp = tc.get("/wardrobe")

        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers.get("location", "")

    def test_backend_unreachable_during_refresh_proceeds_with_old_token(self, app):
        """Backend down during refresh — request continues, no redirect."""
        import httpx as _httpx

        token = _make_jwt_with_exp(1800)
        tc = _make_authed_client_with_token(app, token)

        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.side_effect = _httpx.ConnectError("refused")
            resp = tc.get("/")

        # Home page is accessible without auth, so we get 200 not a redirect
        assert resp.status_code == 200

    def test_malformed_jwt_clears_session_and_redirects(self, app):
        """Corrupted token in session — treated as expired, redirect to /login."""
        tc = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.return_value = _make_http_response(
                200, {"access_token": "bad.token"}
            )
            tc.post("/login", data={"username": "u@example.com", "password": "pw"})

        resp = tc.get("/wardrobe")
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers.get("location", "")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=. pytest tests/test_frontend.py::TestRefreshTokenBeforeware -v
```

Expected: tests FAIL (beforeware doesn't exist yet; `/wardrobe` redirects unauthenticated requests, `HX-Redirect` tests fail).

- [ ] **Step 3: Implement the beforeware in `frontend/app.py`**

**3a. Add missing imports** at the top of `frontend/app.py` (after existing imports):

```python
import base64
import json
import time
```

**3b. Add helper functions** before the `AppClass = ...` line:

```python
def _decode_jwt_exp(token: str):
    """Return the exp claim from a JWT payload without verifying the signature."""
    try:
        segments = token.split(".")
        if len(segments) != 3:
            return None
        payload_b64 = segments[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("exp")
    except Exception:
        return None


def _login_redirect(req):
    """Return an HTMX-aware redirect to /login."""
    if req.headers.get("HX-Request") == "true":
        from starlette.responses import Response as _Response
        r = _Response(status_code=200)
        r.headers["HX-Redirect"] = "/login"
        return r
    return RedirectResponse("/login", status_code=303)


async def refresh_token_if_needed(req, session):
    """
    Beforeware: silently refresh JWTs within 60 minutes of expiry.
    Redirects to /login if the token is expired or refresh fails.
    """
    token = session.get("access_token")
    if not token:
        return  # unauthenticated request — nothing to do

    exp = _decode_jwt_exp(token)
    if exp is None:
        # Malformed token
        session.pop("access_token", None)
        return _login_redirect(req)

    now = int(time.time())
    if exp <= now:
        # Already expired
        session.pop("access_token", None)
        return _login_redirect(req)

    if exp - now > 3600:
        return  # >60 minutes remaining — no action needed

    # ≤60 minutes remaining — attempt proactive refresh
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{API_BASE_URL}/auth/refresh",
                cookies={"access_token": token},
                timeout=3.0,
            )
        if resp.status_code == 200:
            session["access_token"] = resp.json()["access_token"]
        else:
            session.pop("access_token", None)
            return _login_redirect(req)
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        logger.warning("Token refresh skipped — backend unreachable: %s", exc)
```

**3c. Register the beforeware** — update the `AppClass(...)` call to include `before=`:

Find:
```python
app = AppClass(
    secret_key=SESSION_SECRET,
    hdrs=(
```

Replace with:
```python
app = AppClass(
    secret_key=SESSION_SECRET,
    before=Beforeware(
        refresh_token_if_needed,
        skip=[r"/login", r"/register", r"/logout", r"/reset-password.*", r"/forgot-password"],
    ),
    hdrs=(
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=. pytest tests/test_frontend.py::TestRefreshTokenBeforeware -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Run full frontend test suite to check for regressions**

```bash
PYTHONPATH=. pytest tests/test_frontend.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Run full test suite**

```bash
make test
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/app.py tests/test_frontend.py
git commit -m "feat: add token refresh beforeware to frontend"
```
