# app/services/llm_service.py
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI

from app.core.config import config
from app.models.outfit import OutfitSuggestion

logger = logging.getLogger(__name__)

_MODEL = "google/gemini-3-flash-preview"


def get_client() -> Optional[AsyncOpenAI]:
    """
    Build and return an AsyncOpenAI client pointed at OpenRouter.

    Returns:
        Configured AsyncOpenAI client, or None if the API key is unavailable.
    """
    try:
        api_key = config.openrouter_api_key
    except Exception:
        logger.warning(
            "OpenRouter API key unavailable — LLM calls will fall back to rule-based suggestions.",
            exc_info=True,
        )
        return None

    return AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )


async def get_outfit_suggestion(
    location: str,
    temp_c: float,
    condition: str,
    forecast: Optional[List[dict]] = None,
    user_context: Optional[dict] = None,
) -> Dict[str, str]:
    """
    Generate outfit suggestion using LLM with forecast context.

    Args:
        location: Location name.
        temp_c: Current temperature in Celsius.
        condition: Current weather condition text.
        forecast: Optional forecast data for next few days.
        user_context: Optional user preferences.

    Returns:
        Outfit suggestion dictionary with keys: top, bottom, outerwear, accessories.
    """
    # Build context-aware prompt
    prompt_parts = [
        f"Location: {location}",
        f"Current temperature: {temp_c}°C",
        f"Current condition: {condition}",
    ]

    if forecast and len(forecast) > 0:
        prompt_parts.append("\nForecast for the next few days:")
        for day in forecast[:3]:
            prompt_parts.append(
                f"- {day['date']}: {day['min_temp_c']}°C to {day['max_temp_c']}°C, "
                f"{day['condition']}, {day.get('chance_of_rain', 0)}% chance of rain"
            )

    current_hour = datetime.now().hour
    if 6 <= current_hour < 12:
        time_context = "morning"
    elif 12 <= current_hour < 17:
        time_context = "afternoon"
    elif 17 <= current_hour < 21:
        time_context = "evening"
    else:
        time_context = "night"

    prompt_parts.append(f"\nTime of day: {time_context}")

    if user_context:
        prompt_parts.append(f"\nUser preferences: {user_context}")

    weather_context = "\n".join(prompt_parts)

    full_prompt = f"""You are a professional fashion stylist. Based on the weather conditions below, suggest a complete, practical outfit.

        Weather Information:
        {weather_context}

        Return the suggestion as a JSON object with exactly these keys:
        - "top": Suggestion for upper body clothing (include color)
        - "bottom": Suggestion for lower body clothing (include color)
        - "outerwear": Suggestion for a jacket or coat (include color), or "None" if not needed
        - "accessories": Practical accessories (e.g., umbrella, sunglasses, hat) as a single string

        Example output:
        {{
            "top": "Navy blue cotton t-shirt",
            "bottom": "Beige chinos",
            "outerwear": "None",
            "accessories": "Sunglasses, leather belt"
        }}

        Do not include any other text or commentary outside the JSON object.
        """

    try:
        client = get_client()
        if not client:
            logger.warning(
                "No LLM client available — using rule-based fallback for location=%s.",
                location,
            )
            return _get_fallback_suggestion(temp_c, condition, forecast is not None)

        logger.info(
            "Calling OpenRouter model=%s for outfit suggestion: location=%s temp_c=%.1f condition=%s",
            _MODEL,
            location,
            temp_c,
            condition,
        )
        response = await client.chat.completions.create(
            model=_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful fashion stylist who provides practical outfit suggestions in JSON format.",
                },
                {"role": "user", "content": full_prompt},
            ],
            temperature=0.7,
            max_tokens=300,
        )

        raw_content = response.choices[0].message.content.strip()
        logger.debug(
            "OpenRouter raw response for location=%s: %.300s", location, raw_content
        )

        usage = getattr(response, "usage", None)
        if usage:
            logger.debug(
                "OpenRouter token usage: prompt=%s completion=%s total=%s",
                getattr(usage, "prompt_tokens", "?"),
                getattr(usage, "completion_tokens", "?"),
                getattr(usage, "total_tokens", "?"),
            )

        # Robust JSON parsing
        data: Optional[Dict[str, Any]] = None
        try:
            data = json.loads(raw_content)
        except json.JSONDecodeError:
            json_match = re.search(
                r"```(?:json)?\s*(\{.*?\})\s*```", raw_content, re.DOTALL
            )
            if json_match:
                try:
                    data = json.loads(json_match.group(1))
                except json.JSONDecodeError:
                    pass

        if not data:
            logger.error(
                "Failed to parse JSON from OpenRouter response for location=%s. "
                "Raw content (truncated): %.300s",
                location,
                raw_content,
            )
            return _get_fallback_suggestion(temp_c, condition, forecast is not None)

        try:
            validated = OutfitSuggestion(**data)
            logger.info(
                "Outfit suggestion generated via LLM for location=%s.", location
            )
            return validated.model_dump()
        except Exception:
            logger.error(
                "Pydantic validation of LLM response failed for location=%s.",
                location,
                exc_info=True,
            )
            return _get_fallback_suggestion(temp_c, condition, forecast is not None)

    except Exception:
        logger.error(
            "Unexpected error calling OpenRouter for location=%s — using fallback.",
            location,
            exc_info=True,
        )
        return _get_fallback_suggestion(temp_c, condition, forecast is not None)


async def generate_search_query(
    preferences: dict,
    weather: dict,
) -> str:
    """
    Generate a product search query string from user preferences + weather context.

    The returned string is short (5–10 words) and suitable for embedding and
    passing to the catalog search. It is NOT a SQL query or a structured filter —
    it is a natural-language description of what the user might want to wear.

    Args:
        preferences: User's style_preferences dict (colors, styles, occasions, avoid).
        weather: Dict with temp_c, condition, and optionally location.

    Returns:
        A short query string, e.g. "navy chinos casual warm weather".
        Falls back to a template string if the LLM call fails.
    """
    condition = weather.get("condition", "")
    temp_c = weather.get("temp_c", 20)
    styles = ", ".join(preferences.get("styles", ["casual"]))
    colors = ", ".join(preferences.get("colors", []))
    avoid = ", ".join(preferences.get("avoid", []))

    prompt = (
        f"Weather: {condition}, {temp_c}°C. "
        f"User style: {styles}. "
        + (f"Preferred colors: {colors}. " if colors else "")
        + (f"Avoid: {avoid}. " if avoid else "")
        + "Write a 5–10 word clothing search query for this person. "
        "Only output the query, no explanation."
    )

    try:
        client = get_client()
        if not client:
            return _fallback_search_query(preferences, weather)

        logger.info(
            "Generating search query via LLM: temp_c=%.1f condition=%s",
            temp_c,
            condition,
        )
        response = await client.chat.completions.create(
            model=_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=50,
        )
        query = response.choices[0].message.content.strip().strip('"').strip("'")
        logger.info("Generated search query: %r", query)
        return query

    except Exception:
        logger.error(
            "LLM search query generation failed — using fallback", exc_info=True
        )
        return _fallback_search_query(preferences, weather)


def _fallback_search_query(preferences: dict, weather: dict) -> str:
    """
    Rule-based search query fallback when the LLM is unavailable.

    Args:
        preferences: User's style_preferences dict.
        weather: Dict with temp_c, condition.

    Returns:
        A simple template query string.
    """
    style = (
        preferences.get("styles", ["casual"])[0]
        if preferences.get("styles")
        else "casual"
    )
    temp_c = weather.get("temp_c", 20)

    if temp_c < 10:
        weather_desc = "cold weather"
    elif temp_c < 20:
        weather_desc = "cool weather"
    else:
        weather_desc = "warm weather"

    return f"{style} men clothing {weather_desc}"


async def generate_explanation(
    top_items: list[dict],
    weather_context: dict,
    style_preferences: dict,
) -> str:
    """
    Generate a 2–3 sentence natural-language explanation for a set of recommendations.

    Called only when the client passes include_explanation=True, since it adds
    ~1–2s of LLM latency.

    Args:
        top_items: List of dicts with keys title, price, attributes (top 3 is enough).
        weather_context: Dict with temp_c, condition.
        style_preferences: User's style_preferences dict.

    Returns:
        A short explanation string, or an empty string on failure.
    """
    items_summary = "; ".join(
        f"{item['title']} (${item['price']:.0f})" for item in top_items[:3]
    )
    condition = weather_context.get("condition", "")
    temp_c = weather_context.get("temp_c", 20)

    prompt = (
        f"Weather: {condition}, {temp_c}°C. "
        f"User preferences: {style_preferences}. "
        f"Top recommended items: {items_summary}. "
        "In 2–3 sentences, explain why these items suit this person today. "
        "Be concise and conversational."
    )

    try:
        client = get_client()
        if not client:
            return ""

        response = await client.chat.completions.create(
            model=_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=150,
        )
        return response.choices[0].message.content.strip()

    except Exception:
        logger.error("LLM explanation generation failed", exc_info=True)
        return ""


def _get_fallback_suggestion(
    temp_c: float, condition: str, has_forecast: bool
) -> Dict[str, str]:
    """
    Provide simple rule-based outfit suggestion as fallback when LLM is unavailable.

    Args:
        temp_c: Temperature in Celsius.
        condition: Weather condition text.
        has_forecast: Whether forecast data is available.

    Returns:
        Basic outfit suggestion dictionary.
    """
    logger.info(
        "Using rule-based fallback outfit suggestion: temp_c=%.1f condition=%s",
        temp_c,
        condition,
    )
    outfit: Dict[str, str] = {
        "top": "Comfortable t-shirt",
        "bottom": "Jeans",
        "outerwear": "None",
        "accessories": "None",
    }

    if temp_c < 5:
        outfit["top"] = "Warm sweater or thermal top"
        outfit["bottom"] = "Insulated pants or heavy trousers"
        outfit["outerwear"] = "Heavy winter coat"
        outfit["accessories"] = "Gloves, warm hat"
    elif temp_c < 15:
        outfit["top"] = "Long sleeve shirt or light sweater"
        outfit["bottom"] = "Pants or chinos"
        outfit["outerwear"] = "Light jacket or windbreaker"
    elif temp_c < 25:
        outfit["top"] = "Cotton t-shirt or polo"
        outfit["bottom"] = "Jeans or casual pants"
        outfit["outerwear"] = "None"
    else:
        outfit["top"] = "Light, breathable t-shirt"
        outfit["bottom"] = "Shorts or light linen pants"
        outfit["outerwear"] = "None"

    condition_lower = condition.lower()
    accs: List[str] = []
    if any(word in condition_lower for word in ["rain", "drizzle", "shower"]):
        outfit["outerwear"] = (
            "Raincoat"
            if outfit["outerwear"] == "None"
            else f"{outfit['outerwear']} and raincoat"
        )
        accs.append("Umbrella")
    elif any(word in condition_lower for word in ["snow", "sleet", "blizzard"]):
        accs.append("Waterproof boots")
    elif "sun" in condition_lower or "clear" in condition_lower:
        accs.append("Sunglasses")

    if accs:
        outfit["accessories"] = ", ".join(accs)

    if has_forecast and outfit["accessories"] == "None":
        outfit["accessories"] = "Check forecast for changes"
    elif has_forecast:
        outfit["accessories"] += " (Check forecast for changes)"

    return outfit
