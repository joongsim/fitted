"""Tests for app/services/llm_service.py — OpenRouter LLM outfit suggestions."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_OUTFIT_JSON = json.dumps(
    {
        "top": "Navy blue t-shirt",
        "bottom": "Beige chinos",
        "outerwear": "None",
        "accessories": "Sunglasses",
    }
)

SAMPLE_FORECAST = [
    {
        "date": "2025-12-10",
        "min_temp_c": 8,
        "max_temp_c": 14,
        "condition": "Partly cloudy",
        "chance_of_rain": 20,
    }
]


def _mock_llm_response(content: str):
    """Build a minimal mock that looks like an OpenAI completion response."""
    mock_choice = MagicMock()
    mock_choice.message.content = content
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = None
    return mock_response


# ---------------------------------------------------------------------------
# get_client
# ---------------------------------------------------------------------------


class TestGetClient:
    def test_returns_async_openai_client_when_key_available(self):
        from app.services.llm_service import get_client

        with patch("app.core.config.config.get_parameter", return_value="fake-key"):
            client = get_client()

        assert client is not None

    def test_client_uses_openrouter_base_url(self):
        from app.services.llm_service import get_client
        from openai import AsyncOpenAI

        with patch("app.core.config.config.get_parameter", return_value="fake-key"):
            client = get_client()

        assert isinstance(client, AsyncOpenAI)
        assert "openrouter.ai" in str(client.base_url)

    def test_returns_none_when_api_key_unavailable(self):
        from app.services.llm_service import get_client

        with patch("app.core.config.config.get_parameter", side_effect=Exception("no key")):
            client = get_client()

        assert client is None


# ---------------------------------------------------------------------------
# _get_fallback_suggestion — temperature bands
# ---------------------------------------------------------------------------


class TestGetFallbackSuggestionTemperatureBands:
    def _fallback(self, temp_c, condition="Clear", has_forecast=False):
        from app.services.llm_service import _get_fallback_suggestion

        return _get_fallback_suggestion(temp_c, condition, has_forecast)

    def test_below_5_degrees_returns_heavy_winter_outfit(self):
        # Use "Overcast" so no condition-based accessory is added,
        # letting us assert only on the temperature-band accessories.
        result = self._fallback(temp_c=0.0, condition="Overcast")
        assert "winter coat" in result["outerwear"].lower()
        assert "thermal" in result["top"].lower() or "sweater" in result["top"].lower()
        assert "insulated" in result["bottom"].lower() or "heavy" in result["bottom"].lower()
        assert "gloves" in result["accessories"].lower()

    def test_exactly_4_degrees_is_below_5_band(self):
        result = self._fallback(temp_c=4.9)
        assert "winter coat" in result["outerwear"].lower()

    def test_exactly_5_degrees_falls_in_5_to_15_band(self):
        result = self._fallback(temp_c=5.0)
        assert "jacket" in result["outerwear"].lower() or "windbreaker" in result["outerwear"].lower()

    def test_5_to_15_band_returns_light_jacket(self):
        result = self._fallback(temp_c=10.0)
        assert "jacket" in result["outerwear"].lower() or "windbreaker" in result["outerwear"].lower()
        assert "long sleeve" in result["top"].lower() or "sweater" in result["top"].lower()

    def test_exactly_14_degrees_is_in_5_to_15_band(self):
        result = self._fallback(temp_c=14.9)
        assert "jacket" in result["outerwear"].lower() or "windbreaker" in result["outerwear"].lower()

    def test_exactly_15_degrees_falls_in_15_to_25_band(self):
        result = self._fallback(temp_c=15.0)
        assert result["outerwear"] == "None"

    def test_15_to_25_band_returns_t_shirt_and_jeans(self):
        result = self._fallback(temp_c=20.0)
        assert "t-shirt" in result["top"].lower() or "polo" in result["top"].lower()
        assert "jeans" in result["bottom"].lower() or "casual" in result["bottom"].lower()
        assert result["outerwear"] == "None"

    def test_exactly_24_degrees_is_in_15_to_25_band(self):
        result = self._fallback(temp_c=24.9)
        assert result["outerwear"] == "None"

    def test_exactly_25_degrees_is_hot_band(self):
        result = self._fallback(temp_c=25.0)
        assert "light" in result["top"].lower() or "breathable" in result["top"].lower()
        assert "shorts" in result["bottom"].lower() or "linen" in result["bottom"].lower()
        assert result["outerwear"] == "None"

    def test_above_25_degrees_returns_light_breathable(self):
        result = self._fallback(temp_c=35.0)
        assert "breathable" in result["top"].lower() or "light" in result["top"].lower()
        assert "shorts" in result["bottom"].lower() or "linen" in result["bottom"].lower()


# ---------------------------------------------------------------------------
# _get_fallback_suggestion — condition-based accessories
# ---------------------------------------------------------------------------


class TestGetFallbackSuggestionConditions:
    def _fallback(self, condition, temp_c=20.0, has_forecast=False):
        from app.services.llm_service import _get_fallback_suggestion

        return _get_fallback_suggestion(temp_c, condition, has_forecast)

    def test_rain_condition_adds_umbrella(self):
        result = self._fallback("Heavy Rain")
        assert "Umbrella" in result["accessories"]

    def test_drizzle_condition_adds_umbrella(self):
        result = self._fallback("Light Drizzle")
        assert "Umbrella" in result["accessories"]

    def test_shower_condition_adds_umbrella(self):
        result = self._fallback("Patchy shower possible")
        assert "Umbrella" in result["accessories"]

    def test_rain_adds_raincoat_when_no_outerwear(self):
        result = self._fallback("Rain", temp_c=20.0)
        assert "Raincoat" in result["outerwear"] or "raincoat" in result["outerwear"].lower()

    def test_rain_appends_raincoat_when_outerwear_exists(self):
        result = self._fallback("Rain", temp_c=10.0)
        # At 10°C there is already a jacket
        assert "raincoat" in result["outerwear"].lower()

    def test_snow_condition_adds_waterproof_boots(self):
        result = self._fallback("Blizzard")
        assert "Waterproof boots" in result["accessories"]

    def test_sleet_condition_adds_waterproof_boots(self):
        result = self._fallback("Sleet")
        assert "Waterproof boots" in result["accessories"]

    def test_snow_keyword_in_condition_adds_waterproof_boots(self):
        result = self._fallback("Light Snow")
        assert "Waterproof boots" in result["accessories"]

    def test_clear_condition_adds_sunglasses(self):
        result = self._fallback("Clear")
        assert "Sunglasses" in result["accessories"]

    def test_sunny_keyword_adds_sunglasses(self):
        result = self._fallback("Sunny")
        assert "Sunglasses" in result["accessories"]

    def test_overcast_condition_has_no_special_accessories(self):
        result = self._fallback("Overcast", has_forecast=False)
        assert result["accessories"] == "None"

    def test_condition_matching_is_case_insensitive(self):
        result = self._fallback("HEAVY RAIN")
        assert "Umbrella" in result["accessories"]


# ---------------------------------------------------------------------------
# _get_fallback_suggestion — has_forecast flag
# ---------------------------------------------------------------------------


class TestGetFallbackSuggestionForecastFlag:
    def _fallback(self, has_forecast, condition="Overcast", temp_c=20.0):
        from app.services.llm_service import _get_fallback_suggestion

        return _get_fallback_suggestion(temp_c, condition, has_forecast)

    def test_has_forecast_true_with_no_accessories_sets_check_forecast(self):
        result = self._fallback(has_forecast=True)
        assert "Check forecast for changes" in result["accessories"]

    def test_has_forecast_false_accessories_stays_none(self):
        result = self._fallback(has_forecast=False)
        assert result["accessories"] == "None"

    def test_has_forecast_true_appends_check_forecast_to_existing_accessories(self):
        from app.services.llm_service import _get_fallback_suggestion

        result = _get_fallback_suggestion(20.0, "Clear", True)
        assert "Sunglasses" in result["accessories"]
        assert "Check forecast for changes" in result["accessories"]


# ---------------------------------------------------------------------------
# get_outfit_suggestion — full integration with mocked client
# ---------------------------------------------------------------------------


class TestGetOutfitSuggestion:
    async def test_valid_json_response_returns_parsed_dict(self):
        from app.services import llm_service

        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _mock_llm_response(VALID_OUTFIT_JSON)

        with patch("app.services.llm_service.get_client", return_value=mock_client):
            result = await llm_service.get_outfit_suggestion(
                location="London", temp_c=15.0, condition="Sunny"
            )

        assert result["top"] == "Navy blue t-shirt"
        assert result["bottom"] == "Beige chinos"
        assert result["outerwear"] == "None"
        assert result["accessories"] == "Sunglasses"

    async def test_no_client_falls_back_to_rule_based(self):
        from app.services import llm_service

        with patch("app.services.llm_service.get_client", return_value=None):
            result = await llm_service.get_outfit_suggestion(
                location="London", temp_c=20.0, condition="Clear"
            )

        assert "top" in result
        assert "bottom" in result
        assert "outerwear" in result
        assert "accessories" in result

    async def test_malformed_json_falls_back_to_rule_based(self):
        from app.services import llm_service

        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _mock_llm_response(
            "Here is my suggestion: it's a great day!"
        )

        with patch("app.services.llm_service.get_client", return_value=mock_client):
            result = await llm_service.get_outfit_suggestion(
                location="Tokyo", temp_c=25.0, condition="Sunny"
            )

        assert "top" in result

    async def test_json_in_markdown_code_block_is_parsed(self):
        from app.services import llm_service

        md_json = f"```json\n{VALID_OUTFIT_JSON}\n```"
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _mock_llm_response(md_json)

        with patch("app.services.llm_service.get_client", return_value=mock_client):
            result = await llm_service.get_outfit_suggestion(
                location="Paris", temp_c=18.0, condition="Cloudy"
            )

        assert result["top"] == "Navy blue t-shirt"

    async def test_network_error_falls_back_to_rule_based(self):
        from app.services import llm_service

        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = Exception("Connection refused")

        with patch("app.services.llm_service.get_client", return_value=mock_client):
            result = await llm_service.get_outfit_suggestion(
                location="Berlin", temp_c=5.0, condition="Cloudy"
            )

        assert "top" in result

    async def test_forecast_data_included_in_prompt(self):
        from app.services import llm_service

        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _mock_llm_response(VALID_OUTFIT_JSON)

        with patch("app.services.llm_service.get_client", return_value=mock_client):
            await llm_service.get_outfit_suggestion(
                location="NY", temp_c=10.0, condition="Clear", forecast=SAMPLE_FORECAST
            )

        call_args = mock_client.chat.completions.create.await_args
        prompt_text = str(call_args)
        assert "Partly cloudy" in prompt_text or "2025-12-10" in prompt_text

    async def test_user_context_included_in_prompt(self):
        from app.services import llm_service

        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _mock_llm_response(VALID_OUTFIT_JSON)
        user_ctx = {"style": "formal", "colors": ["navy"]}

        with patch("app.services.llm_service.get_client", return_value=mock_client):
            await llm_service.get_outfit_suggestion(
                location="LA", temp_c=22.0, condition="Sunny", user_context=user_ctx
            )

        call_args = mock_client.chat.completions.create.await_args
        assert "formal" in str(call_args)

    async def test_json_missing_required_key_falls_back_to_rule_based(self):
        from app.services import llm_service

        # 'bottom' key is missing — Pydantic validation fails
        incomplete_json = json.dumps(
            {"top": "T-shirt", "outerwear": "None", "accessories": "None"}
        )
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _mock_llm_response(incomplete_json)

        with patch("app.services.llm_service.get_client", return_value=mock_client):
            result = await llm_service.get_outfit_suggestion(
                location="Rome", temp_c=20.0, condition="Sunny"
            )

        # Falls back to rule-based; still has all required keys
        assert "top" in result
        assert "bottom" in result
        assert "outerwear" in result
        assert "accessories" in result

    async def test_result_is_dict_of_strings(self):
        from app.services import llm_service

        with patch("app.services.llm_service.get_client", return_value=None):
            result = await llm_service.get_outfit_suggestion(
                location="Oslo", temp_c=-5.0, condition="Snow"
            )

        for key in ("top", "bottom", "outerwear", "accessories"):
            assert key in result
            assert isinstance(result[key], str)

    async def test_no_forecast_passes_has_forecast_false_to_fallback(self):
        from app.services import llm_service

        with patch("app.services.llm_service.get_client", return_value=None):
            result = await llm_service.get_outfit_suggestion(
                location="Oslo", temp_c=20.0, condition="Overcast", forecast=None
            )

        # Without forecast and with overcast condition, accessories should be "None"
        assert result["accessories"] == "None"

    async def test_with_forecast_passes_has_forecast_true_to_fallback(self):
        from app.services import llm_service

        with patch("app.services.llm_service.get_client", return_value=None):
            result = await llm_service.get_outfit_suggestion(
                location="Oslo",
                temp_c=20.0,
                condition="Overcast",
                forecast=SAMPLE_FORECAST,
            )

        assert "Check forecast for changes" in result["accessories"]
