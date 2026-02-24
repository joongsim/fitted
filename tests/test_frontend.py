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
    tc = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    # Inject a fake token into the session via the login endpoint
    with patch("httpx.AsyncClient") as mock_http:
        mock_instance = AsyncMock()
        mock_http.return_value.__aenter__.return_value = mock_instance
        mock_instance.post.return_value = _make_http_response(
            200, {"access_token": "fake-jwt-token"}
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

    def test_login_page_redirects_to_home_when_already_authenticated(self, authed_client):
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
                data={"full_name": "New User", "email": "new@example.com", "password": "pw"},
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
                data={"full_name": "Existing", "email": "existing@example.com", "password": "pw"},
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
        assert b"Connection error" in response.content or b"could not reach" in response.content

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
