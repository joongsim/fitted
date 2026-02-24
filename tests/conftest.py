"""Shared fixtures and test data for the Fitted test suite."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID


# ---------------------------------------------------------------------------
# Shared mock data constants
# ---------------------------------------------------------------------------

MOCK_USER_ID = "123e4567-e89b-12d3-a456-426614174000"
MOCK_USER_EMAIL = "test@example.com"
MOCK_USER_PASSWORD = "SecurePassword123"
MOCK_USER_FULL_NAME = "Test User"

# Raw DB row dict (includes hashed_password — for auth flows)
MOCK_USER_DB_ROW = {
    "user_id": UUID(MOCK_USER_ID),
    "email": MOCK_USER_EMAIL,
    "hashed_password": "$2b$12$placeholderhashedpassword1234567",
    "full_name": MOCK_USER_FULL_NAME,
    "is_active": True,
}

# User model dict (no hashed_password — for /users/me responses)
MOCK_USER_DICT = {
    "user_id": UUID(MOCK_USER_ID),
    "email": MOCK_USER_EMAIL,
    "full_name": MOCK_USER_FULL_NAME,
    "is_active": True,
    "created_at": datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    "last_login": None,
}

MOCK_WEATHER_DATA = {
    "location": {
        "name": "London",
        "region": "City of London, Greater London",
        "country": "United Kingdom",
        "lat": 51.52,
        "lon": -0.11,
        "tz_id": "Europe/London",
        "localtime_epoch": 1765056792,
        "localtime": "2025-12-07 06:33",
    },
    "current": {
        "last_updated_epoch": 1765056600,
        "last_updated": "2025-12-07 06:30",
        "temp_c": 12.0,
        "temp_f": 53.6,
        "is_day": 1,
        "condition": {
            "text": "Partly cloudy",
            "icon": "//cdn.weatherapi.com/weather/64x64/day/116.png",
            "code": 1003,
        },
        "wind_mph": 8.1,
        "wind_kph": 13.0,
        "humidity": 72,
        "cloud": 50,
        "feelslike_c": 10.5,
        "feelslike_f": 50.9,
        "uv": 1.0,
    },
}

MOCK_FORECAST_DATA = {
    **MOCK_WEATHER_DATA,
    "forecast": {
        "forecastday": [
            {
                "date": "2025-12-07",
                "date_epoch": 1765008000,
                "day": {
                    "maxtemp_c": 14.0,
                    "maxtemp_f": 57.2,
                    "mintemp_c": 8.0,
                    "mintemp_f": 46.4,
                    "avgtemp_c": 11.0,
                    "avgtemp_f": 51.8,
                    "condition": {
                        "text": "Partly cloudy",
                        "icon": "//cdn.weatherapi.com/weather/64x64/day/116.png",
                        "code": 1003,
                    },
                    "daily_chance_of_rain": 20,
                },
                "astro": {
                    "sunrise": "07:58 AM",
                    "sunset": "03:52 PM",
                },
            }
        ]
    },
}

MOCK_OUTFIT_SUGGESTION = {
    "top": "Navy blue cotton t-shirt",
    "bottom": "Beige chinos",
    "outerwear": "Light jacket",
    "accessories": "Sunglasses",
}

MOCK_PREFERENCES = {
    "style_preferences": {"style": "casual", "colors": ["blue", "grey"]},
    "size_info": {"shirt": "M", "pants": "32"},
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_user_db_row():
    """A raw DB dict row containing hashed_password (used in auth/login tests)."""
    return MOCK_USER_DB_ROW.copy()


@pytest.fixture
def mock_user_dict():
    """A User-shaped dict without hashed_password."""
    return MOCK_USER_DICT.copy()


@pytest.fixture
def mock_weather_data():
    """Standard mock weather API response dict."""
    return MOCK_WEATHER_DATA.copy()


@pytest.fixture
def mock_forecast_data():
    """Standard mock forecast API response dict."""
    import copy
    return copy.deepcopy(MOCK_FORECAST_DATA)


@pytest.fixture
def mock_outfit():
    """Standard mock outfit suggestion dict."""
    return MOCK_OUTFIT_SUGGESTION.copy()


@pytest.fixture
def mock_preferences():
    """Standard mock user preferences dict."""
    import copy
    return copy.deepcopy(MOCK_PREFERENCES)


@pytest.fixture
def valid_jwt_token():
    """
    A freshly-minted JWT using the default dev secret key.
    Re-generates each test so it is always valid (not expired).
    """
    from app.core.auth import create_access_token

    return create_access_token(data={"sub": MOCK_USER_ID})


@pytest.fixture(autouse=False)
def patch_config_get_parameter():
    """
    Patch config.get_parameter so no SSM / env-var look-ups happen.
    Tests that need specific values should override within the test body.
    """
    from unittest.mock import patch

    def _fake_get_parameter(name, default=None):
        defaults = {
            "/fitted/jwt-secret-key": "dev-secret-key-change-me-in-prod",
            "/fitted/jwt-algorithm": "HS256",
            "/fitted/access-token-expire-minutes": "1440",
            "/fitted/openrouter-api-key": "fake-openrouter-key",
            "/fitted/weather-api-key": "fake-weather-key",
            "/fitted/database-url": "postgresql://user:pass@localhost/fitted",
        }
        if name in defaults:
            return defaults[name]
        if default is not None:
            return default
        raise ValueError(f"Parameter {name} not found")

    with patch("app.core.config.config.get_parameter", side_effect=_fake_get_parameter):
        yield
