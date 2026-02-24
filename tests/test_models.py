"""Tests for all Pydantic models in app/models/."""
from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Test data shared across model tests
# ---------------------------------------------------------------------------

VALID_LOCATION = {
    "name": "London",
    "region": "Greater London",
    "country": "United Kingdom",
    "lat": 51.52,
    "lon": -0.11,
    "tz_id": "Europe/London",
    "localtime_epoch": 1765056792,
    "localtime": "2025-12-07 06:33",
}

VALID_CONDITION = {
    "text": "Partly cloudy",
    "icon": "//cdn.weatherapi.com/weather/64x64/day/116.png",
    "code": 1003,
}

VALID_CURRENT = {
    "last_updated_epoch": 1765056600,
    "last_updated": "2025-12-07 06:30",
    "temp_c": 12.0,
    "temp_f": 53.6,
    "is_day": 1,
    "condition": VALID_CONDITION,
    "wind_mph": 8.1,
    "wind_kph": 13.0,
    "humidity": 72,
    "cloud": 50,
    "feelslike_c": 10.5,
    "feelslike_f": 50.9,
    "uv": 1.0,
}

VALID_WEATHER_RESPONSE = {
    "location": VALID_LOCATION,
    "current": VALID_CURRENT,
}


# ===========================================================================
# UserCreate / User / Token models
# ===========================================================================


class TestUserCreate:
    def test_valid_user_create_with_all_fields(self):
        from app.models.user import UserCreate

        u = UserCreate(email="alice@example.com", password="password123", full_name="Alice")
        assert u.email == "alice@example.com"
        assert u.password == "password123"
        assert u.full_name == "Alice"

    def test_valid_user_create_without_full_name(self):
        from app.models.user import UserCreate

        u = UserCreate(email="bob@example.com", password="pw")
        assert u.full_name is None

    def test_invalid_email_raises_validation_error(self):
        from app.models.user import UserCreate

        with pytest.raises(ValidationError) as exc_info:
            UserCreate(email="not-an-email", password="pw")
        assert "email" in str(exc_info.value).lower()

    def test_missing_email_raises_validation_error(self):
        from app.models.user import UserCreate

        with pytest.raises(ValidationError):
            UserCreate(password="pw")

    def test_missing_password_raises_validation_error(self):
        from app.models.user import UserCreate

        with pytest.raises(ValidationError):
            UserCreate(email="test@example.com")

    def test_empty_string_password_is_accepted(self):
        """Pydantic itself doesn't enforce non-empty strings unless we add a validator."""
        from app.models.user import UserCreate

        u = UserCreate(email="test@example.com", password="")
        assert u.password == ""


class TestUser:
    def _make_user(self, **overrides):
        from app.models.user import User

        base = {
            "user_id": UUID("123e4567-e89b-12d3-a456-426614174000"),
            "email": "user@example.com",
            "full_name": "Test User",
            "is_active": True,
            "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "last_login": None,
        }
        base.update(overrides)
        return User(**base)

    def test_valid_user_parses_uuid(self):
        user = self._make_user()
        assert isinstance(user.user_id, UUID)

    def test_valid_user_parses_datetime(self):
        user = self._make_user()
        assert isinstance(user.created_at, datetime)

    def test_last_login_optional_defaults_to_none(self):
        user = self._make_user()
        assert user.last_login is None

    def test_last_login_accepts_datetime(self):
        dt = datetime(2024, 6, 15, tzinfo=timezone.utc)
        user = self._make_user(last_login=dt)
        assert user.last_login == dt

    def test_is_active_defaults_not_enforced_but_required(self):
        """is_active is required (no default)."""
        from app.models.user import User

        with pytest.raises(ValidationError):
            User(
                user_id=UUID("123e4567-e89b-12d3-a456-426614174000"),
                email="user@example.com",
                created_at=datetime(2024, 1, 1),
            )

    def test_from_attributes_config_allows_orm_objects(self):
        """from_attributes = True means we can pass an ORM-like object."""
        from app.models.user import User

        class FakeORM:
            user_id = UUID("123e4567-e89b-12d3-a456-426614174000")
            email = "orm@example.com"
            full_name = "ORM User"
            is_active = True
            created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
            last_login = None

        user = User.model_validate(FakeORM())
        assert user.email == "orm@example.com"

    def test_uuid_string_coerced_to_uuid(self):
        from app.models.user import User

        user = User(
            user_id="123e4567-e89b-12d3-a456-426614174000",
            email="coerce@example.com",
            is_active=True,
            created_at=datetime(2024, 1, 1),
        )
        assert isinstance(user.user_id, UUID)


class TestToken:
    def test_valid_token_model(self):
        from app.models.user import Token

        t = Token(access_token="abc.def.ghi", token_type="bearer")
        assert t.access_token == "abc.def.ghi"
        assert t.token_type == "bearer"

    def test_missing_access_token_raises(self):
        from app.models.user import Token

        with pytest.raises(ValidationError):
            Token(token_type="bearer")

    def test_missing_token_type_raises(self):
        from app.models.user import Token

        with pytest.raises(ValidationError):
            Token(access_token="abc")


class TestTokenData:
    def test_valid_token_data_with_user_id(self):
        from app.models.user import TokenData

        td = TokenData(user_id="abc-123")
        assert td.user_id == "abc-123"

    def test_token_data_user_id_defaults_to_none(self):
        from app.models.user import TokenData

        td = TokenData()
        assert td.user_id is None


# ===========================================================================
# WeatherCondition model
# ===========================================================================


class TestWeatherCondition:
    def test_valid_condition(self):
        from app.models.weather import WeatherCondition

        wc = WeatherCondition(**VALID_CONDITION)
        assert wc.text == "Partly cloudy"
        assert wc.code == 1003

    def test_icon_is_optional(self):
        from app.models.weather import WeatherCondition

        wc = WeatherCondition(text="Clear", code=1000)
        assert wc.icon is None

    def test_missing_text_raises(self):
        from app.models.weather import WeatherCondition

        with pytest.raises(ValidationError):
            WeatherCondition(code=1000)

    def test_missing_code_raises(self):
        from app.models.weather import WeatherCondition

        with pytest.raises(ValidationError):
            WeatherCondition(text="Clear")


# ===========================================================================
# CurrentWeather model
# ===========================================================================


class TestCurrentWeather:
    def test_valid_current_weather(self):
        from app.models.weather import CurrentWeather

        cw = CurrentWeather(**VALID_CURRENT)
        assert cw.temp_c == 12.0
        assert cw.humidity == 72

    def test_temp_c_minimum_boundary_minus_100_accepted(self):
        from app.models.weather import CurrentWeather

        data = {**VALID_CURRENT, "temp_c": -100.0}
        cw = CurrentWeather(**data)
        assert cw.temp_c == -100.0

    def test_temp_c_below_minus_100_raises(self):
        from app.models.weather import CurrentWeather

        data = {**VALID_CURRENT, "temp_c": -100.1}
        with pytest.raises(ValidationError) as exc_info:
            CurrentWeather(**data)
        assert "temp_c" in str(exc_info.value)

    def test_temp_c_maximum_boundary_60_accepted(self):
        from app.models.weather import CurrentWeather

        data = {**VALID_CURRENT, "temp_c": 60.0}
        cw = CurrentWeather(**data)
        assert cw.temp_c == 60.0

    def test_temp_c_above_60_raises(self):
        from app.models.weather import CurrentWeather

        data = {**VALID_CURRENT, "temp_c": 60.1}
        with pytest.raises(ValidationError) as exc_info:
            CurrentWeather(**data)
        assert "temp_c" in str(exc_info.value)
        assert "less than or equal to 60" in str(exc_info.value)

    def test_humidity_minimum_boundary_0_accepted(self):
        from app.models.weather import CurrentWeather

        data = {**VALID_CURRENT, "humidity": 0}
        cw = CurrentWeather(**data)
        assert cw.humidity == 0

    def test_humidity_below_0_raises(self):
        from app.models.weather import CurrentWeather

        data = {**VALID_CURRENT, "humidity": -1}
        with pytest.raises(ValidationError):
            CurrentWeather(**data)

    def test_humidity_maximum_boundary_100_accepted(self):
        from app.models.weather import CurrentWeather

        data = {**VALID_CURRENT, "humidity": 100}
        cw = CurrentWeather(**data)
        assert cw.humidity == 100

    def test_humidity_above_100_raises(self):
        from app.models.weather import CurrentWeather

        data = {**VALID_CURRENT, "humidity": 101}
        with pytest.raises(ValidationError) as exc_info:
            CurrentWeather(**data)
        assert "humidity" in str(exc_info.value)
        assert "less than or equal to 100" in str(exc_info.value)


# ===========================================================================
# Location model
# ===========================================================================


class TestLocation:
    def test_valid_location(self):
        from app.models.weather import Location

        loc = Location(**VALID_LOCATION)
        assert loc.name == "London"
        assert loc.lat == 51.52

    def test_missing_name_raises(self):
        from app.models.weather import Location

        data = {k: v for k, v in VALID_LOCATION.items() if k != "name"}
        with pytest.raises(ValidationError) as exc_info:
            Location(**data)
        assert "location" in str(exc_info.value).lower() or "name" in str(exc_info.value)

    def test_all_required_fields_present(self):
        from app.models.weather import Location

        required = ["name", "region", "country", "lat", "lon", "tz_id", "localtime_epoch", "localtime"]
        for field in required:
            data = {k: v for k, v in VALID_LOCATION.items() if k != field}
            with pytest.raises(ValidationError, match="Field required"):
                Location(**data)


# ===========================================================================
# WeatherResponse model
# ===========================================================================


class TestWeatherResponse:
    def test_valid_weather_response(self):
        from app.models.weather import WeatherResponse

        wr = WeatherResponse(**VALID_WEATHER_RESPONSE)
        assert wr.location.name == "London"
        assert wr.current.temp_c == 12.0

    def test_missing_location_raises(self):
        from app.models.weather import WeatherResponse

        with pytest.raises(ValidationError):
            WeatherResponse(current=VALID_CURRENT)

    def test_missing_current_raises(self):
        from app.models.weather import WeatherResponse

        with pytest.raises(ValidationError):
            WeatherResponse(location=VALID_LOCATION)


# ===========================================================================
# WeatherWithForecast model
# ===========================================================================


class TestWeatherWithForecast:
    def test_forecast_optional_defaults_to_none(self):
        from app.models.weather import WeatherWithForecast

        wf = WeatherWithForecast(**VALID_WEATHER_RESPONSE)
        assert wf.forecast is None

    def test_with_valid_forecast(self):
        from app.models.weather import WeatherWithForecast

        forecast_data = {
            "forecastday": [
                {
                    "date": "2025-12-07",
                    "date_epoch": 1765008000,
                    "day": {"maxtemp_c": 14.0, "condition": {"text": "Partly cloudy", "code": 1003}},
                    "astro": {"sunrise": "07:58 AM"},
                }
            ]
        }
        wf = WeatherWithForecast(forecast=forecast_data, **VALID_WEATHER_RESPONSE)
        assert wf.forecast is not None
        assert len(wf.forecast.forecastday) == 1

    def test_forecast_day_hour_is_optional(self):
        from app.models.weather import ForecastDay

        fd = ForecastDay(
            date="2025-12-07",
            date_epoch=1765008000,
            day={"maxtemp_c": 14},
            astro={"sunrise": "07:58 AM"},
        )
        assert fd.hour is None


# ===========================================================================
# OutfitSuggestion model
# ===========================================================================


class TestOutfitSuggestion:
    def test_valid_outfit_suggestion(self):
        from app.models.outfit import OutfitSuggestion

        o = OutfitSuggestion(
            top="Blue t-shirt",
            bottom="Black jeans",
            outerwear="None",
            accessories="Sunglasses",
        )
        assert o.top == "Blue t-shirt"
        assert o.outerwear == "None"

    def test_missing_top_raises(self):
        from app.models.outfit import OutfitSuggestion

        with pytest.raises(ValidationError):
            OutfitSuggestion(bottom="jeans", outerwear="None", accessories="None")

    def test_missing_bottom_raises(self):
        from app.models.outfit import OutfitSuggestion

        with pytest.raises(ValidationError):
            OutfitSuggestion(top="shirt", outerwear="None", accessories="None")

    def test_missing_outerwear_raises(self):
        from app.models.outfit import OutfitSuggestion

        with pytest.raises(ValidationError):
            OutfitSuggestion(top="shirt", bottom="jeans", accessories="None")

    def test_missing_accessories_raises(self):
        from app.models.outfit import OutfitSuggestion

        with pytest.raises(ValidationError):
            OutfitSuggestion(top="shirt", bottom="jeans", outerwear="None")

    def test_extra_fields_are_ignored(self):
        from app.models.outfit import OutfitSuggestion

        # Pydantic v2 ignores extra fields by default
        o = OutfitSuggestion(
            top="shirt",
            bottom="jeans",
            outerwear="jacket",
            accessories="hat",
            unknown_field="extra",
        )
        assert not hasattr(o, "unknown_field")

    def test_model_dump_returns_all_four_keys(self):
        from app.models.outfit import OutfitSuggestion

        o = OutfitSuggestion(
            top="shirt", bottom="jeans", outerwear="None", accessories="None"
        )
        d = o.model_dump()
        assert set(d.keys()) == {"top", "bottom", "outerwear", "accessories"}
