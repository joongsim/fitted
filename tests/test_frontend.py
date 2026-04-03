"""Tests for the FastHTML frontend routes in frontend/app.py.

FastHTML apps are standard ASGI apps, so we drive them with Starlette's
TestClient. Sessions are cookie-based (Starlette's SessionMiddleware).

Strategy:
- Mount the app with TestClient (follow_redirects=False so we can assert
  on redirect responses).
- For routes that call the backend API, mock httpx.AsyncClient so no real
  HTTP requests are made.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# A minimal outfit + weather payload that the backend /suggest-outfit returns.
MOCK_BACKEND_OUTFIT_RESPONSE = {
    "weather": {
        "location": {"name": "London", "region": "Greater London", "country": "UK"},
        "current": {
            "temp_c": 12.0,
            "temp_f": 53.6,
            "condition": "Partly cloudy",
            "humidity": 72,
            "wind_kph": 13.0,
            "feelslike_f": 50.9,
            "uv": 1.0,
        },
        "forecast": [
            {
                "min_temp_f": 46.4,
                "max_temp_f": 57.2,
                "condition": "Partly cloudy",
                "date": "2025-12-07",
            }
        ],
    },
    "outfit_suggestion": {
        "top": "Navy t-shirt",
        "bottom": "Beige chinos",
        "outerwear": "Light jacket",
        "accessories": "Sunglasses",
    },
}


def _make_http_response(status_code: int, body: dict) -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    return resp


@pytest.fixture
def app():
    """Import the frontend app (suppresses boto3 SSM calls at import time)."""
    with patch("boto3.client"):
        from frontend.app import app as frontend_app

        return frontend_app


@pytest.fixture
def client(app):
    """Unauthenticated test client (no session token)."""
    return TestClient(app, raise_server_exceptions=False, follow_redirects=False)


@pytest.fixture
def authed_client(app):
    """Authenticated test client — session contains an access_token."""
    import time
    from jose import jwt as _jwt

    # Use a real JWT with a far-future expiry so the beforeware doesn't
    # attempt a refresh or redirect during tests that don't need to test that.
    token = _jwt.encode(
        {"sub": "test-user", "exp": int(time.time()) + 7200, "iat": int(time.time())},
        "any-secret",
        algorithm="HS256",
    )
    tc = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    with patch("httpx.AsyncClient") as mock_http:
        mock_instance = AsyncMock()
        mock_http.return_value.__aenter__.return_value = mock_instance
        mock_instance.post.return_value = _make_http_response(
            200, {"access_token": token}
        )
        tc.post("/login", data={"username": "user@example.com", "password": "pw"})
    return tc


# ---------------------------------------------------------------------------
# GET / — home page
# ---------------------------------------------------------------------------


class TestHomePage:
    def test_home_returns_200(self, client):
        response = client.get("/")
        assert response.status_code == 200

    def test_home_contains_fitted_branding(self, client):
        response = client.get("/")
        assert b"fitted" in response.content.lower()

    def test_home_contains_get_outfit_form(self, client):
        response = client.get("/")
        assert b"get-outfit" in response.content

    def test_home_shows_login_link_when_not_authenticated(self, client):
        response = client.get("/")
        assert b"Login" in response.content or b"login" in response.content


# ---------------------------------------------------------------------------
# GET /login
# ---------------------------------------------------------------------------


class TestLoginPage:
    def test_login_page_returns_200(self, client):
        response = client.get("/login")
        assert response.status_code == 200

    def test_login_page_contains_form(self, client):
        response = client.get("/login")
        assert b"password" in response.content.lower()

    def test_login_page_redirects_to_home_when_already_authenticated(
        self, authed_client
    ):
        response = authed_client.get("/login")
        # Already logged-in users are redirected away from /login
        # FastHTML's RedirectResponse uses 303 by default; Starlette may use 307
        assert response.status_code in (302, 303, 307)


# ---------------------------------------------------------------------------
# POST /login
# ---------------------------------------------------------------------------


class TestLoginPost:
    def test_successful_login_redirects_to_home(self, client):
        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.return_value = _make_http_response(
                200, {"access_token": "tok-abc"}
            )
            response = client.post(
                "/login", data={"username": "user@example.com", "password": "pw"}
            )
        assert response.status_code in (302, 303)
        assert response.headers.get("location", "") in ("/", "http://testserver/")

    def test_failed_login_shows_error_message(self, client):
        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.return_value = _make_http_response(
                401, {"detail": "Incorrect email or password"}
            )
            response = client.post(
                "/login", data={"username": "bad@example.com", "password": "wrong"}
            )
        assert response.status_code == 200
        assert b"Invalid email or password" in response.content

    def test_network_error_shows_error_message(self, client):
        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.side_effect = Exception("Connection refused")
            response = client.post(
                "/login", data={"username": "user@example.com", "password": "pw"}
            )
        assert response.status_code == 200
        assert b"could not reach the server" in response.content


# ---------------------------------------------------------------------------
# GET /register
# ---------------------------------------------------------------------------


class TestRegisterPage:
    def test_register_page_returns_200(self, client):
        response = client.get("/register")
        assert response.status_code == 200

    def test_register_page_contains_form(self, client):
        response = client.get("/register")
        assert b"Create Account" in response.content or b"Register" in response.content

    def test_register_page_redirects_when_already_authenticated(self, authed_client):
        response = authed_client.get("/register")
        # FastHTML/Starlette may issue 303 or 307 for RedirectResponse
        assert response.status_code in (302, 303, 307)


# ---------------------------------------------------------------------------
# POST /register
# ---------------------------------------------------------------------------


class TestRegisterPost:
    def test_successful_registration_redirects_to_login(self, client):
        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.return_value = _make_http_response(
                200,
                {
                    "user_id": "123e4567-e89b-12d3-a456-426614174000",
                    "email": "new@example.com",
                    "is_active": True,
                    "created_at": "2024-01-01T00:00:00",
                },
            )
            response = client.post(
                "/register",
                data={
                    "full_name": "New User",
                    "email": "new@example.com",
                    "password": "pw",
                },
            )
        assert response.status_code in (302, 303)
        assert "/login" in response.headers.get("location", "")

    def test_failed_registration_shows_error_message(self, client):
        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.return_value = _make_http_response(
                400, {"detail": "User with this email already exists"}
            )
            response = client.post(
                "/register",
                data={
                    "full_name": "Existing",
                    "email": "existing@example.com",
                    "password": "pw",
                },
            )
        assert response.status_code == 200
        assert b"already exists" in response.content

    def test_network_error_shows_server_error(self, client):
        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.side_effect = Exception("Connection refused")
            response = client.post(
                "/register",
                data={"full_name": "User", "email": "u@example.com", "password": "pw"},
            )
        assert response.status_code == 200
        assert b"could not reach the server" in response.content


# ---------------------------------------------------------------------------
# GET /logout
# ---------------------------------------------------------------------------


class TestLogout:
    def test_logout_redirects_to_login(self, authed_client):
        response = authed_client.get("/logout")
        # FastHTML/Starlette uses 307 for default RedirectResponse on GET
        assert response.status_code in (302, 303, 307)
        location = response.headers.get("location", "")
        assert "/login" in location

    def test_logout_clears_access_token_from_session(self, authed_client):
        # After logout, hitting /login should NOT redirect (token gone)
        authed_client.get("/logout")
        response = authed_client.get("/login")
        # Should render the login page, not redirect
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# POST /get-outfit
# ---------------------------------------------------------------------------


class TestGetOutfit:
    def test_successful_outfit_request_returns_html_fragment(self, client):
        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.return_value = _make_http_response(
                200, MOCK_BACKEND_OUTFIT_RESPONSE
            )
            response = client.post("/get-outfit", data={"location": "London"})
        assert response.status_code == 200
        # Should contain weather/outfit HTML
        assert b"London" in response.content or b"Navy t-shirt" in response.content

    def test_empty_location_returns_error_message(self, client):
        response = client.post("/get-outfit", data={"location": ""})
        assert response.status_code == 200
        assert b"Please enter a location" in response.content

    def test_whitespace_only_location_returns_error_message(self, client):
        response = client.post("/get-outfit", data={"location": "   "})
        assert response.status_code == 200
        assert b"Please enter a location" in response.content

    def test_backend_error_shows_error_message(self, client):
        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.return_value = _make_http_response(
                500, {"detail": "Internal server error"}
            )
            response = client.post("/get-outfit", data={"location": "London"})
        assert response.status_code == 200
        assert b"Error" in response.content or b"error" in response.content

    def test_network_exception_shows_connection_error(self, client):
        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.side_effect = Exception("Connection refused")
            response = client.post("/get-outfit", data={"location": "London"})
        assert response.status_code == 200
        assert (
            b"Connection error" in response.content
            or b"could not reach" in response.content
        )

    def test_authenticated_request_sends_bearer_token(self, authed_client):
        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.return_value = _make_http_response(
                200, MOCK_BACKEND_OUTFIT_RESPONSE
            )
            authed_client.post("/get-outfit", data={"location": "London"})

        # The post call should have included an Authorization header
        call_kwargs = mock_instance.post.call_args
        headers = call_kwargs[1].get("headers", {})
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ")

    def test_unauthenticated_request_sends_no_bearer_token(self, client):
        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.return_value = _make_http_response(
                200, MOCK_BACKEND_OUTFIT_RESPONSE
            )
            client.post("/get-outfit", data={"location": "London"})

        call_kwargs = mock_instance.post.call_args
        headers = call_kwargs[1].get("headers", {})
        assert "Authorization" not in headers

    def test_authenticated_outfit_response_includes_shop_button(self, authed_client):
        """Logged-in users see a 'Get Recommendations' button after outfit results."""
        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.return_value = _make_http_response(
                200, MOCK_BACKEND_OUTFIT_RESPONSE
            )
            response = authed_client.post("/get-outfit", data={"location": "London"})
        assert response.status_code == 200
        assert b"get-recommendations" in response.content

    def test_unauthenticated_outfit_response_excludes_shop_button(self, client):
        """Anonymous users do not see the 'Get Recommendations' button."""
        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.return_value = _make_http_response(
                200, MOCK_BACKEND_OUTFIT_RESPONSE
            )
            response = client.post("/get-outfit", data={"location": "London"})
        assert response.status_code == 200
        assert b"get-recommendations" not in response.content


# ---------------------------------------------------------------------------
# GET /recommendations
# ---------------------------------------------------------------------------

MOCK_BACKEND_RECOMMENDATIONS_RESPONSE = {
    "user_id": "abc-123",
    "location": "London",
    "weather": {"temp_c": 12.0, "condition": "Partly cloudy", "location": "London"},
    "count": 2,
    "recommendations": [
        {
            "item_id": "item-1",
            "title": "Navy Slim-Fit Chinos",
            "price": 45.0,
            "product_url": "https://poshmark.com/listing/1",
            "image_url": "https://example.com/img1.jpg",
            "similarity_score": 0.87,
            "attributes": {"brand": "Gap", "category": "bottoms"},
            "llm_explanation": None,
        },
        {
            "item_id": "item-2",
            "title": "White Oxford Shirt",
            "price": 30.0,
            "product_url": "https://poshmark.com/listing/2",
            "image_url": None,
            "similarity_score": 0.82,
            "attributes": {"brand": "J.Crew", "category": "tops"},
            "llm_explanation": None,
        },
    ],
}


class TestRecommendationsPage:
    def test_recommendations_page_redirects_when_unauthenticated(self, client):
        response = client.get("/recommendations")
        assert response.status_code in (302, 303, 307)
        assert "/login" in response.headers.get("location", "")

    def test_recommendations_page_returns_200_when_authenticated(self, authed_client):
        response = authed_client.get("/recommendations")
        assert response.status_code == 200

    def test_recommendations_page_contains_form(self, authed_client):
        response = authed_client.get("/recommendations")
        assert b"get-recommendations" in response.content

    def test_recommendations_page_shows_nav_link(self, authed_client):
        response = authed_client.get("/recommendations")
        assert (
            b"Recs" in response.content
            or b"recommendations" in response.content.lower()
        )


# ---------------------------------------------------------------------------
# POST /get-recommendations
# ---------------------------------------------------------------------------


class TestGetRecommendations:
    def test_unauthenticated_returns_login_prompt(self, client):
        response = client.post("/get-recommendations", data={"location": "London"})
        assert response.status_code == 200
        assert b"log in" in response.content.lower()

    def test_successful_response_returns_product_cards(self, authed_client):
        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.return_value = _make_http_response(
                200, MOCK_BACKEND_RECOMMENDATIONS_RESPONSE
            )
            response = authed_client.post(
                "/get-recommendations", data={"location": "London"}
            )
        assert response.status_code == 200
        assert b"Navy Slim-Fit Chinos" in response.content
        assert b"White Oxford Shirt" in response.content

    def test_successful_response_includes_weather_meta(self, authed_client):
        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.return_value = _make_http_response(
                200, MOCK_BACKEND_RECOMMENDATIONS_RESPONSE
            )
            response = authed_client.post(
                "/get-recommendations", data={"location": "London"}
            )
        assert response.status_code == 200
        assert b"London" in response.content
        assert b"Partly cloudy" in response.content

    def test_backend_error_returns_error_message(self, authed_client):
        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.return_value = _make_http_response(
                500, {"detail": "Internal server error"}
            )
            response = authed_client.post(
                "/get-recommendations", data={"location": "London"}
            )
        assert response.status_code == 200
        assert b"Error" in response.content or b"error" in response.content

    def test_empty_recommendations_shows_no_results_message(self, authed_client):
        empty_resp = {
            **MOCK_BACKEND_RECOMMENDATIONS_RESPONSE,
            "recommendations": [],
            "count": 0,
        }
        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.return_value = _make_http_response(200, empty_resp)
            response = authed_client.post(
                "/get-recommendations", data={"location": "London"}
            )
        assert response.status_code == 200
        assert b"No recommendations" in response.content

    def test_network_error_shows_connection_error(self, authed_client):
        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.side_effect = Exception("Connection refused")
            response = authed_client.post(
                "/get-recommendations", data={"location": "London"}
            )
        assert response.status_code == 200
        assert (
            b"Connection error" in response.content
            or b"could not reach" in response.content
        )

    def test_sends_bearer_token_to_backend(self, authed_client):
        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.return_value = _make_http_response(
                200, MOCK_BACKEND_RECOMMENDATIONS_RESPONSE
            )
            authed_client.post("/get-recommendations", data={"location": "London"})

        call_kwargs = mock_instance.post.call_args
        headers = call_kwargs[1].get("headers", {})
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ")

    def test_sends_correct_json_body_to_backend(self, authed_client):
        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.return_value = _make_http_response(
                200, MOCK_BACKEND_RECOMMENDATIONS_RESPONSE
            )
            authed_client.post("/get-recommendations", data={"location": "Paris"})

        call_kwargs = mock_instance.post.call_args
        payload = call_kwargs[1].get("json", {})
        assert payload.get("location") == "Paris"
        assert payload.get("include_explanation") is False

    def test_product_card_placeholder_shown_when_no_image(self, authed_client):
        """Items without image_url render the placeholder emoji."""
        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.post.return_value = _make_http_response(
                200, MOCK_BACKEND_RECOMMENDATIONS_RESPONSE
            )
            response = authed_client.post(
                "/get-recommendations", data={"location": "London"}
            )
        assert response.status_code == 200
        # White Oxford Shirt has no image_url — placeholder div should appear
        assert b"product-card-placeholder" in response.content


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
    # The beforeware only base64-decodes exp — it does NOT verify the signature.
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


# ---------------------------------------------------------------------------
# GET /wardrobe/{item_id}/status
# ---------------------------------------------------------------------------


class TestWardrobeItemStatus:
    def test_unauthenticated_returns_empty(self, client):
        """No access_token in session → route returns empty string."""
        response = client.get("/wardrobe/item-123/status")
        assert response.status_code == 200
        assert response.text == ""

    def test_returns_html_on_backend_200(self, authed_client):
        """Backend returns 200 with HTML body → route returns that HTML."""
        html_body = '<span class="badge">ready</span>'
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html_body

        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.get.return_value = mock_resp
            response = authed_client.get("/wardrobe/item-123/status")

        assert response.status_code == 200
        assert html_body.encode() in response.content

    def test_returns_empty_on_backend_non_200(self, authed_client):
        """Backend returns 404 → route returns empty string."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Not Found"

        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.get.return_value = mock_resp
            response = authed_client.get("/wardrobe/item-123/status")

        assert response.status_code == 200
        assert response.text == ""

    def test_returns_empty_on_exception(self, authed_client):
        """httpx.ConnectError during backend call → route returns empty string."""
        import httpx as _httpx

        with patch("httpx.AsyncClient") as mock_http:
            mock_instance = AsyncMock()
            mock_http.return_value.__aenter__.return_value = mock_instance
            mock_instance.get.side_effect = _httpx.ConnectError("refused")
            response = authed_client.get("/wardrobe/item-456/status")

        assert response.status_code == 200
        assert response.text == ""
