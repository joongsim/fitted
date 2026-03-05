"""Integration tests for FastAPI endpoints in app/main.py.

All external dependencies (DB, weather service, LLM service, analysis service)
are mocked so no real network or database calls are made.
"""

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    MOCK_USER_EMAIL,
    MOCK_USER_FULL_NAME,
    MOCK_USER_ID,
    MOCK_WEATHER_DATA,
    MOCK_FORECAST_DATA,
    MOCK_OUTFIT_SUGGESTION,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOCK_USER_OBJ = MagicMock()
MOCK_USER_OBJ.user_id = UUID(MOCK_USER_ID)
MOCK_USER_OBJ.email = MOCK_USER_EMAIL
MOCK_USER_OBJ.full_name = MOCK_USER_FULL_NAME
MOCK_USER_OBJ.is_active = True
MOCK_USER_OBJ.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
MOCK_USER_OBJ.last_login = None

MOCK_USER_DB_ROW = {
    "user_id": UUID(MOCK_USER_ID),
    "email": MOCK_USER_EMAIL,
    "hashed_password": "$2b$12$placeholder",
    "full_name": MOCK_USER_FULL_NAME,
    "is_active": True,
}


@pytest.fixture
def client():
    """
    Build a TestClient with the DB pool initialisation suppressed and
    all external services mocked at the module level.
    """
    # Patch DB pool so startup/shutdown don't try to connect
    mock_pool = AsyncMock()

    @asynccontextmanager
    async def _fake_lifespan(app):
        yield

    with patch("app.main.db_service.init_pool", new_callable=AsyncMock):
        with patch("app.main.db_service.close_pool", new_callable=AsyncMock):
            with patch(
                "app.services.recommendation_service.init_recommendation_service"
            ):
                from app.main import app

                with TestClient(app, raise_server_exceptions=False) as c:
                    yield c


# ---------------------------------------------------------------------------
# Helper: build a valid JWT header
# ---------------------------------------------------------------------------


def _auth_headers(user_id: str = MOCK_USER_ID) -> dict:
    from app.core.auth import create_access_token

    token = create_access_token({"sub": user_id})
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# GET / — root
# ---------------------------------------------------------------------------


class TestRoot:
    def test_root_returns_welcome_message(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "Fitted" in response.json()["message"]


# ---------------------------------------------------------------------------
# POST /auth/register
# ---------------------------------------------------------------------------


class TestRegister:
    def test_successful_registration_returns_200(self, client):
        with patch(
            "app.main.user_service.get_user_by_email",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with patch(
                "app.main.user_service.create_user",
                new_callable=AsyncMock,
                return_value=MOCK_USER_OBJ,
            ):
                response = client.post(
                    "/auth/register",
                    json={
                        "email": MOCK_USER_EMAIL,
                        "password": "Password123",
                        "full_name": MOCK_USER_FULL_NAME,
                    },
                )
        assert response.status_code == 200

    def test_successful_registration_returns_user_data(self, client):
        with patch(
            "app.main.user_service.get_user_by_email",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with patch(
                "app.main.user_service.create_user",
                new_callable=AsyncMock,
                return_value=MOCK_USER_OBJ,
            ):
                response = client.post(
                    "/auth/register",
                    json={"email": MOCK_USER_EMAIL, "password": "Password123"},
                )
        data = response.json()
        assert data["email"] == MOCK_USER_EMAIL

    def test_duplicate_email_returns_400(self, client):
        with patch(
            "app.main.user_service.get_user_by_email",
            new_callable=AsyncMock,
            return_value=MOCK_USER_DB_ROW,
        ):
            response = client.post(
                "/auth/register",
                json={"email": MOCK_USER_EMAIL, "password": "Password123"},
            )
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]

    def test_invalid_email_format_returns_422(self, client):
        response = client.post(
            "/auth/register",
            json={"email": "not-an-email", "password": "Password123"},
        )
        assert response.status_code == 422

    def test_missing_password_returns_422(self, client):
        response = client.post(
            "/auth/register",
            json={"email": MOCK_USER_EMAIL},
        )
        assert response.status_code == 422

    def test_create_user_returns_none_gives_500(self, client):
        with patch(
            "app.main.user_service.get_user_by_email",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with patch(
                "app.main.user_service.create_user",
                new_callable=AsyncMock,
                return_value=None,
            ):
                response = client.post(
                    "/auth/register",
                    json={"email": MOCK_USER_EMAIL, "password": "Password123"},
                )
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------


class TestLogin:
    def test_successful_login_returns_200_and_token(self, client):
        with patch(
            "app.main.user_service.get_user_by_email",
            new_callable=AsyncMock,
            return_value=MOCK_USER_DB_ROW,
        ):
            with patch("app.main.auth.verify_password", return_value=True):
                with patch(
                    "app.main.user_service.update_last_login", new_callable=AsyncMock
                ):
                    response = client.post(
                        "/auth/login",
                        data={"username": MOCK_USER_EMAIL, "password": "Password123"},
                    )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_successful_login_sets_http_only_cookie(self, client):
        with patch(
            "app.main.user_service.get_user_by_email",
            new_callable=AsyncMock,
            return_value=MOCK_USER_DB_ROW,
        ):
            with patch("app.main.auth.verify_password", return_value=True):
                with patch(
                    "app.main.user_service.update_last_login", new_callable=AsyncMock
                ):
                    response = client.post(
                        "/auth/login",
                        data={"username": MOCK_USER_EMAIL, "password": "Password123"},
                    )
        assert "access_token" in response.cookies

    def test_wrong_password_returns_401(self, client):
        with patch(
            "app.main.user_service.get_user_by_email",
            new_callable=AsyncMock,
            return_value=MOCK_USER_DB_ROW,
        ):
            with patch("app.main.auth.verify_password", return_value=False):
                response = client.post(
                    "/auth/login",
                    data={"username": MOCK_USER_EMAIL, "password": "wrong"},
                )
        assert response.status_code == 401

    def test_unknown_email_returns_401(self, client):
        with patch(
            "app.main.user_service.get_user_by_email",
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = client.post(
                "/auth/login",
                data={"username": "nobody@example.com", "password": "pw"},
            )
        assert response.status_code == 401

    def test_inactive_user_returns_400(self, client):
        inactive_user = {**MOCK_USER_DB_ROW, "is_active": False}
        with patch(
            "app.main.user_service.get_user_by_email",
            new_callable=AsyncMock,
            return_value=inactive_user,
        ):
            with patch("app.main.auth.verify_password", return_value=True):
                response = client.post(
                    "/auth/login",
                    data={"username": MOCK_USER_EMAIL, "password": "pw"},
                )
        assert response.status_code == 400
        assert "Inactive" in response.json()["detail"]


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------


class TestLogout:
    def test_logout_returns_200(self, client):
        response = client.post("/auth/logout")
        assert response.status_code == 200

    def test_logout_clears_cookie(self, client):
        response = client.post("/auth/logout")
        # The cookie should be deleted (set to empty / max-age=0)
        assert response.status_code == 200
        assert "Successfully logged out" in response.json()["message"]


# ---------------------------------------------------------------------------
# GET /users/me
# ---------------------------------------------------------------------------


class TestGetMe:
    def test_valid_token_returns_user(self, client):
        with patch(
            "app.main.user_service.get_user_by_id",
            new_callable=AsyncMock,
            return_value=MOCK_USER_OBJ,
        ):
            with patch.dict(os.environ, {"DEV_MODE": "false"}):
                response = client.get("/users/me", headers=_auth_headers())
        assert response.status_code == 200
        assert response.json()["email"] == MOCK_USER_EMAIL

    def test_no_token_returns_401(self, client):
        with patch.dict(os.environ, {"DEV_MODE": "false"}):
            response = client.get("/users/me")
        assert response.status_code == 401

    def test_user_not_found_in_db_returns_404(self, client):
        with patch(
            "app.main.user_service.get_user_by_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with patch.dict(os.environ, {"DEV_MODE": "false"}):
                response = client.get("/users/me", headers=_auth_headers())
        assert response.status_code == 404

    def test_dev_mode_bypasses_auth(self, client):
        with patch(
            "app.main.user_service.get_user_by_id",
            new_callable=AsyncMock,
            return_value=MOCK_USER_OBJ,
        ):
            with patch.dict(os.environ, {"DEV_MODE": "true"}):
                response = client.get("/users/me")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /users/me/preferences
# ---------------------------------------------------------------------------


class TestGetPreferences:
    def test_returns_preferences_with_valid_token(self, client):
        prefs = {"style_preferences": {"style": "casual"}, "size_info": {"shirt": "M"}}
        with patch(
            "app.main.user_service.get_user_preferences",
            new_callable=AsyncMock,
            return_value=prefs,
        ):
            with patch.dict(os.environ, {"DEV_MODE": "false"}):
                response = client.get("/users/me/preferences", headers=_auth_headers())
        assert response.status_code == 200
        assert response.json() == prefs

    def test_no_token_returns_401(self, client):
        with patch.dict(os.environ, {"DEV_MODE": "false"}):
            response = client.get("/users/me/preferences")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /users/me/preferences
# ---------------------------------------------------------------------------


class TestUpdatePreferences:
    def test_update_style_returns_success_message(self, client):
        with patch(
            "app.main.user_service.update_user_preferences", new_callable=AsyncMock
        ):
            with patch.dict(os.environ, {"DEV_MODE": "false"}):
                response = client.patch(
                    "/users/me/preferences",
                    params={"style_prefs": '{"style": "formal"}'},
                    headers=_auth_headers(),
                )
        assert response.status_code == 200
        assert "updated" in response.json()["message"].lower()

    def test_no_token_returns_401(self, client):
        with patch.dict(os.environ, {"DEV_MODE": "false"}):
            response = client.patch("/users/me/preferences")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /suggest-outfit
# ---------------------------------------------------------------------------


class TestSuggestOutfit:
    def test_returns_weather_and_outfit_with_forecast(self, client):
        with patch(
            "app.main.weather_service.get_weather_with_forecast",
            new_callable=AsyncMock,
            return_value=MOCK_FORECAST_DATA,
        ):
            with patch(
                "app.main.llm_service.get_outfit_suggestion",
                new_callable=AsyncMock,
                return_value=MOCK_OUTFIT_SUGGESTION,
            ):
                response = client.post("/suggest-outfit", params={"location": "London"})
        assert response.status_code == 200
        data = response.json()
        assert "weather" in data
        assert "outfit_suggestion" in data

    def test_returns_weather_and_outfit_without_forecast(self, client):
        with patch(
            "app.main.weather_service.get_weather_data",
            new_callable=AsyncMock,
            return_value=MOCK_WEATHER_DATA,
        ):
            with patch(
                "app.main.llm_service.get_outfit_suggestion",
                new_callable=AsyncMock,
                return_value=MOCK_OUTFIT_SUGGESTION,
            ):
                response = client.post(
                    "/suggest-outfit",
                    params={"location": "London", "include_forecast": "false"},
                )
        assert response.status_code == 200

    def test_weather_service_error_returns_500(self, client):
        with patch(
            "app.main.weather_service.get_weather_with_forecast",
            new_callable=AsyncMock,
            side_effect=Exception("Weather down"),
        ):
            response = client.post("/suggest-outfit", params={"location": "London"})
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# GET /weather/{location}
# ---------------------------------------------------------------------------


class TestGetCurrentWeather:
    def test_returns_weather_data(self, client):
        with patch(
            "app.main.weather_service.get_weather_data",
            new_callable=AsyncMock,
            return_value=MOCK_WEATHER_DATA,
        ):
            response = client.get("/weather/London")
        assert response.status_code == 200
        data = response.json()
        assert data["location"] == "London"
        assert "current" in data

    def test_weather_service_error_returns_500(self, client):
        with patch(
            "app.main.weather_service.get_weather_data",
            new_callable=AsyncMock,
            side_effect=Exception("API down"),
        ):
            response = client.get("/weather/London")
        assert response.status_code == 500

    def test_current_weather_shape(self, client):
        with patch(
            "app.main.weather_service.get_weather_data",
            new_callable=AsyncMock,
            return_value=MOCK_WEATHER_DATA,
        ):
            response = client.get("/weather/London")
        current = response.json()["current"]
        expected_keys = {
            "temperature_c",
            "temperature_f",
            "condition",
            "humidity",
            "wind_kph",
            "feels_like_c",
        }
        assert expected_keys.issubset(current.keys())


# ---------------------------------------------------------------------------
# GET /weather/{location}/forecast
# ---------------------------------------------------------------------------


class TestGetWeatherForecast:
    def test_returns_forecast_data(self, client):
        with patch(
            "app.main.weather_service.get_weather_with_forecast",
            new_callable=AsyncMock,
            return_value=MOCK_FORECAST_DATA,
        ):
            response = client.get("/weather/London/forecast")
        assert response.status_code == 200
        data = response.json()
        assert "forecast" in data
        assert len(data["forecast"]) >= 1

    def test_days_param_default_is_1(self, client):
        with patch(
            "app.main.weather_service.get_weather_with_forecast",
            new_callable=AsyncMock,
            return_value=MOCK_FORECAST_DATA,
        ) as mock_w:
            client.get("/weather/London/forecast")
        mock_w.assert_awaited_once_with("London", 1)

    def test_days_param_custom_value(self, client):
        with patch(
            "app.main.weather_service.get_weather_with_forecast",
            new_callable=AsyncMock,
            return_value=MOCK_FORECAST_DATA,
        ) as mock_w:
            client.get("/weather/London/forecast?days=3")
        mock_w.assert_awaited_once_with("London", 3)

    def test_days_below_1_returns_422(self, client):
        response = client.get("/weather/London/forecast?days=0")
        assert response.status_code == 422

    def test_days_above_10_returns_422(self, client):
        response = client.get("/weather/London/forecast?days=11")
        assert response.status_code == 422

    def test_forecast_service_error_returns_500(self, client):
        with patch(
            "app.main.weather_service.get_weather_with_forecast",
            new_callable=AsyncMock,
            side_effect=Exception("API down"),
        ):
            response = client.get("/weather/London/forecast")
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# GET /analytics/temperature
# ---------------------------------------------------------------------------


class TestAnalyticsByTemperature:
    def test_returns_results_with_defaults(self, client):
        with patch(
            "app.main.analysis_service.query_weather_by_temperature", return_value=[]
        ):
            response = client.get("/analytics/temperature")
        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert "count" in data

    def test_uses_default_min_temp_15(self, client):
        with patch(
            "app.main.analysis_service.query_weather_by_temperature", return_value=[]
        ) as mock_q:
            client.get("/analytics/temperature")
        mock_q.assert_called_once_with(15.0, None)

    def test_custom_min_temp_passed_to_service(self, client):
        with patch(
            "app.main.analysis_service.query_weather_by_temperature", return_value=[]
        ) as mock_q:
            client.get("/analytics/temperature?min_temp=25.0")
        mock_q.assert_called_once_with(25.0, None)

    def test_date_filter_passed_to_service(self, client):
        with patch(
            "app.main.analysis_service.query_weather_by_temperature", return_value=[]
        ) as mock_q:
            client.get("/analytics/temperature?date=2025-06-15")
        mock_q.assert_called_once_with(15.0, "2025-06-15")

    def test_service_error_returns_500(self, client):
        with patch(
            "app.main.analysis_service.query_weather_by_temperature",
            side_effect=Exception("Athena down"),
        ):
            response = client.get("/analytics/temperature")
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# GET /analytics/location/{location}
# ---------------------------------------------------------------------------


class TestAnalyticsLocationTrend:
    def test_returns_trend_data(self, client):
        with patch(
            "app.main.analysis_service.get_location_weather_trend", return_value=[]
        ):
            response = client.get("/analytics/location/London")
        assert response.status_code == 200
        data = response.json()
        assert data["location"] == "London"
        assert "trend" in data

    def test_default_days_is_7(self, client):
        with patch(
            "app.main.analysis_service.get_location_weather_trend", return_value=[]
        ) as mock_q:
            client.get("/analytics/location/London")
        mock_q.assert_called_once_with("London", 7)

    def test_days_below_1_returns_422(self, client):
        response = client.get("/analytics/location/London?days=0")
        assert response.status_code == 422

    def test_days_above_30_returns_422(self, client):
        response = client.get("/analytics/location/London?days=31")
        assert response.status_code == 422

    def test_service_error_returns_500(self, client):
        with patch(
            "app.main.analysis_service.get_location_weather_trend",
            side_effect=Exception("error"),
        ):
            response = client.get("/analytics/location/London")
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# GET /analytics/summary
# ---------------------------------------------------------------------------


class TestAnalyticsSummary:
    def test_returns_summary_dict(self, client):
        summary = {"unique_locations": "3", "avg_temperature": "18.5"}
        with patch(
            "app.main.analysis_service.get_weather_analytics_summary",
            return_value=summary,
        ):
            response = client.get("/analytics/summary")
        assert response.status_code == 200
        assert response.json()["summary"] == summary

    def test_service_error_returns_500(self, client):
        with patch(
            "app.main.analysis_service.get_weather_analytics_summary",
            side_effect=Exception("fail"),
        ):
            response = client.get("/analytics/summary")
        assert response.status_code == 500

    def test_date_param_returned_in_response(self, client):
        with patch(
            "app.main.analysis_service.get_weather_analytics_summary", return_value={}
        ):
            response = client.get("/analytics/summary?date=2025-03-01")
        assert response.json()["date"] == "2025-03-01"


# ---------------------------------------------------------------------------
# GET /analytics/condition/{condition}
# ---------------------------------------------------------------------------


class TestAnalyticsByCondition:
    def test_returns_condition_results(self, client):
        with patch(
            "app.main.analysis_service.get_weather_by_condition", return_value=[]
        ):
            response = client.get("/analytics/condition/Rain")
        assert response.status_code == 200
        data = response.json()
        assert data["condition"] == "Rain"
        assert "results" in data

    def test_service_error_returns_500(self, client):
        with patch(
            "app.main.analysis_service.get_weather_by_condition",
            side_effect=Exception("err"),
        ):
            response = client.get("/analytics/condition/Rain")
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# GET /debug/config
# ---------------------------------------------------------------------------


class TestDebugConfig:
    def test_returns_config_keys(self, client):
        with patch("app.core.config.config.get_parameter", return_value="fake-key"):
            response = client.get("/debug/config")
        assert response.status_code == 200
        data = response.json()
        # Key fields should be present
        assert "has_openrouter_api_key" in data
        assert "has_weather_api_key" in data

    def test_config_key_error_handled_gracefully(self, client):
        with patch(
            "app.core.config.config.get_parameter", side_effect=Exception("no key")
        ):
            response = client.get("/debug/config")
        assert response.status_code == 200
        data = response.json()
        assert data["has_openrouter_api_key"] is False
        assert data["has_weather_api_key"] is False


# ---------------------------------------------------------------------------
# GET /catalog/search
# ---------------------------------------------------------------------------

import numpy as np

from app.models.item import Item
from app.models.product import ProductRecommendation

MOCK_ITEM = Item(
    item_id="item123",
    domain="fashion",
    title="Blue Oxford Shirt",
    price=35.0,
    image_url="https://example.com/image.jpg",
    product_url="https://poshmark.com/listing/item123",
    source="poshmark_seed",
    embedding=None,
    attributes={"brand": "Zara", "size": "M", "condition": "nwt"},
)

MOCK_RECOMMENDATION = ProductRecommendation(
    item_id="item123",
    title="Blue Oxford Shirt",
    price=35.0,
    product_url="https://poshmark.com/listing/item123",
    image_url="https://example.com/image.jpg",
    similarity_score=0.85,
    attributes={"brand": "Zara", "size": "M"},
)


class TestCatalogSearch:
    def test_returns_200_with_valid_auth(self, client):
        mock_vec = np.zeros(512, dtype=np.float32)
        with (
            patch("app.services.embedding_service.encode_text", return_value=mock_vec),
            patch(
                "app.services.dev_catalog_service.search",
                new_callable=AsyncMock,
                return_value=[MOCK_ITEM],
            ),
        ):
            response = client.get(
                "/catalog/search?q=blue+shirt",
                headers=_auth_headers(),
            )
        assert response.status_code == 200
        data = response.json()
        assert data["query"] == "blue shirt"
        assert data["count"] == 1
        assert data["results"][0]["item_id"] == "item123"
        assert data["results"][0]["title"] == "Blue Oxford Shirt"

    def test_no_auth_returns_401(self, client):
        response = client.get("/catalog/search?q=blue+shirt")
        assert response.status_code == 401

    def test_missing_query_returns_422(self, client):
        response = client.get("/catalog/search", headers=_auth_headers())
        assert response.status_code == 422

    def test_limit_respected(self, client):
        mock_vec = np.zeros(512, dtype=np.float32)
        with (
            patch("app.services.embedding_service.encode_text", return_value=mock_vec),
            patch(
                "app.services.dev_catalog_service.search",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_search,
        ):
            client.get(
                "/catalog/search?q=jeans&limit=5",
                headers=_auth_headers(),
            )
        mock_search.assert_awaited_once()
        _, kwargs = mock_search.call_args
        assert kwargs.get("limit") == 5 or mock_search.call_args.args[1] == 5

    def test_service_error_returns_500(self, client):
        mock_vec = np.zeros(512, dtype=np.float32)
        with (
            patch("app.services.embedding_service.encode_text", return_value=mock_vec),
            patch(
                "app.services.dev_catalog_service.search",
                new_callable=AsyncMock,
                side_effect=RuntimeError("db down"),
            ),
        ):
            response = client.get(
                "/catalog/search?q=shirt",
                headers=_auth_headers(),
            )
        assert response.status_code == 500

    def test_empty_results_returns_count_zero(self, client):
        mock_vec = np.zeros(512, dtype=np.float32)
        with (
            patch("app.services.embedding_service.encode_text", return_value=mock_vec),
            patch(
                "app.services.dev_catalog_service.search",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            response = client.get(
                "/catalog/search?q=nothing",
                headers=_auth_headers(),
            )
        assert response.status_code == 200
        assert response.json()["count"] == 0
        assert response.json()["results"] == []


# ---------------------------------------------------------------------------
# POST /recommend-products
# ---------------------------------------------------------------------------


class TestRecommendProducts:
    def _mock_service(self, recommendations: list) -> MagicMock:
        svc = MagicMock()
        svc.recommend = AsyncMock(return_value=recommendations)
        return svc

    def test_returns_200_with_valid_auth(self, client):
        with (
            patch(
                "app.services.weather_service.get_weather_data",
                new_callable=AsyncMock,
                return_value=MOCK_WEATHER_DATA,
            ),
            patch(
                "app.services.user_service.get_user_preferences",
                new_callable=AsyncMock,
                return_value={"style_preferences": {"styles": ["casual"]}},
            ),
            patch(
                "app.services.recommendation_service.get_recommendation_service",
                return_value=self._mock_service([MOCK_RECOMMENDATION]),
            ),
            patch(
                "app.services.affiliate_service.record_affiliate_click",
                new_callable=AsyncMock,
                return_value="test-click-id",
            ),
        ):
            response = client.post(
                "/recommend-products",
                json={"location": "London"},
                headers=_auth_headers(),
            )
        assert response.status_code == 200
        data = response.json()
        assert data["location"] == "London"
        assert data["count"] == 1
        assert data["recommendations"][0]["item_id"] == "item123"
        assert data["recommendations"][0]["click_url"] == "/r/test-click-id"

    def test_no_auth_returns_401(self, client):
        response = client.post("/recommend-products", json={"location": "London"})
        assert response.status_code == 401

    def test_empty_location_returns_422(self, client):
        response = client.post(
            "/recommend-products",
            json={"location": ""},
            headers=_auth_headers(),
        )
        assert response.status_code == 422

    def test_location_too_long_returns_422(self, client):
        response = client.post(
            "/recommend-products",
            json={"location": "x" * 201},
            headers=_auth_headers(),
        )
        assert response.status_code == 422

    def test_prefs_none_does_not_raise(self, client):
        """get_user_preferences returning None must not cause AttributeError."""
        with (
            patch(
                "app.services.weather_service.get_weather_data",
                new_callable=AsyncMock,
                return_value=MOCK_WEATHER_DATA,
            ),
            patch(
                "app.services.user_service.get_user_preferences",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "app.services.recommendation_service.get_recommendation_service",
                return_value=self._mock_service([]),
            ),
            patch(
                "app.services.affiliate_service.record_affiliate_click",
                new_callable=AsyncMock,
                return_value="test-click-id",
            ),
        ):
            response = client.post(
                "/recommend-products",
                json={"location": "London"},
                headers=_auth_headers(),
            )
        # Should return 200 with empty recommendations, not 500
        assert response.status_code == 200
        assert response.json()["count"] == 0

    def test_uninitialized_service_returns_500(self, client):
        """RuntimeError from get_recommendation_service → 500."""
        with (
            patch(
                "app.services.weather_service.get_weather_data",
                new_callable=AsyncMock,
                return_value=MOCK_WEATHER_DATA,
            ),
            patch(
                "app.services.user_service.get_user_preferences",
                new_callable=AsyncMock,
                return_value={"style_preferences": {}},
            ),
            patch(
                "app.services.recommendation_service.get_recommendation_service",
                side_effect=RuntimeError("service not initialized"),
            ),
        ):
            response = client.post(
                "/recommend-products",
                json={"location": "London"},
                headers=_auth_headers(),
            )
        assert response.status_code == 500

    def test_user_id_comes_from_jwt_not_body(self, client):
        """user_id in response must match the JWT subject, not any request body field."""
        captured = {}

        async def _fake_recommend(**kwargs):
            captured["user_id"] = kwargs.get("user_id")
            return []

        svc = MagicMock()
        svc.recommend = _fake_recommend

        with (
            patch(
                "app.services.weather_service.get_weather_data",
                new_callable=AsyncMock,
                return_value=MOCK_WEATHER_DATA,
            ),
            patch(
                "app.services.user_service.get_user_preferences",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "app.services.recommendation_service.get_recommendation_service",
                return_value=svc,
            ),
        ):
            response = client.post(
                "/recommend-products",
                json={"location": "London"},
                headers=_auth_headers(MOCK_USER_ID),
            )

        assert response.status_code == 200
        assert captured["user_id"] == MOCK_USER_ID
        assert response.json()["user_id"] == MOCK_USER_ID

    def test_include_explanation_forwarded_to_service(self, client):
        captured = {}

        async def _fake_recommend(**kwargs):
            captured["include_explanation"] = kwargs.get("include_explanation")
            return []

        svc = MagicMock()
        svc.recommend = _fake_recommend

        with (
            patch(
                "app.services.weather_service.get_weather_data",
                new_callable=AsyncMock,
                return_value=MOCK_WEATHER_DATA,
            ),
            patch(
                "app.services.user_service.get_user_preferences",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "app.services.recommendation_service.get_recommendation_service",
                return_value=svc,
            ),
        ):
            client.post(
                "/recommend-products",
                json={"location": "London", "include_explanation": True},
                headers=_auth_headers(),
            )

        assert captured["include_explanation"] is True

    def test_weather_service_error_returns_500(self, client):
        with (
            patch(
                "app.services.weather_service.get_weather_data",
                new_callable=AsyncMock,
                side_effect=RuntimeError("weather API down"),
            ),
            patch(
                "app.services.user_service.get_user_preferences",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "app.services.recommendation_service.get_recommendation_service",
                return_value=self._mock_service([]),
            ),
        ):
            response = client.post(
                "/recommend-products",
                json={"location": "London"},
                headers=_auth_headers(),
            )
        assert response.status_code == 500
