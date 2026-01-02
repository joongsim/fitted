# app/services/llm_service.py
import httpx
import json
import re

from openai import AsyncOpenAI
from app.core.config import config
from typing import Optional, List, Dict, Any
from app.models.outfit import OutfitSuggestion


def get_client():
    """Get or create OpenRouter client."""
    try:
        api_key = config.openrouter_api_key
    except Exception as e:
        print(f"Warning: Could not get OpenRouter API key: {e}")
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
        location: Location name
        temp_c: Current temperature in Celsius
        condition: Current weather condition
        forecast: Optional forecast data for next few days
        user_context: Optional user preferences (for future use)

    Returns:
        Outfit suggestion dictionary with keys: top, bottom, outerwear, accessories
    """

    # Build context-aware prompt
    prompt_parts = [
        f"Location: {location}",
        f"Current temperature: {temp_c}°C",
        f"Current condition: {condition}",
    ]

    # Add forecast context if available
    if forecast and len(forecast) > 0:
        prompt_parts.append("\nForecast for the next few days:")
        for day in forecast[:3]:  # Only use first 3 days
            prompt_parts.append(
                f"- {day['date']}: {day['min_temp_c']}°C to {day['max_temp_c']}°C, "
                f"{day['condition']}, {day.get('chance_of_rain', 0)}% chance of rain"
            )

    # Add time of day context
    from datetime import datetime

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

    # Add user preferences if available (for future use)
    if user_context:
        prompt_parts.append(f"\nUser preferences: {user_context}")

    # Build final prompt
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
        # Get OpenRouter client
        client = get_client()
        if not client:
            return _get_fallback_suggestion(temp_c, condition, forecast is not None)
        
        # Call OpenRouter API using AsyncOpenAI client
        response = await client.chat.completions.create(
            model="google/gemini-3-flash-preview",
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
        
        # Robust parsing
        data = None
        try:
            # 1. Direct parse
            data = json.loads(raw_content)
        except json.JSONDecodeError:
            # 2. Try extracting from markdown blocks
            json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_content, re.DOTALL)
            if json_match:
                try:
                    data = json.loads(json_match.group(1))
                except json.JSONDecodeError:
                    pass

        if not data:
            return _get_fallback_suggestion(temp_c, condition, forecast is not None)

        # Validate with Pydantic
        try:
            validated = OutfitSuggestion(**data)
            return validated.model_dump()
        except Exception as e:
            print(f"Validation error: {e}")
            return _get_fallback_suggestion(temp_c, condition, forecast is not None)

    except Exception as e:
        print(f"Error calling LLM: {e}")
        # Fallback to rule-based suggestion
        return _get_fallback_suggestion(temp_c, condition, forecast is not None)


def _get_fallback_suggestion(temp_c: float, condition: str, has_forecast: bool) -> Dict[str, str]:
    """
    Provide simple rule-based outfit suggestion as fallback.
    
    Args:
        temp_c: Temperature in Celsius
        condition: Weather condition
        has_forecast: Whether forecast data is available
        
    Returns:
        Basic outfit suggestion dictionary
    """
    outfit = {
        "top": "Comfortable t-shirt",
        "bottom": "Jeans",
        "outerwear": "None",
        "accessories": "None"
    }
    
    # Temperature-based suggestions
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
    
    # Condition-based accessories
    condition_lower = condition.lower()
    accs = []
    if any(word in condition_lower for word in ['rain', 'drizzle', 'shower']):
        outfit["outerwear"] = "Raincoat" if outfit["outerwear"] == "None" else f"{outfit['outerwear']} and raincoat"
        accs.append("Umbrella")
    elif any(word in condition_lower for word in ['snow', 'sleet', 'blizzard']):
        accs.append("Waterproof boots")
    elif 'sun' in condition_lower or 'clear' in condition_lower:
        accs.append("Sunglasses")
    
    if accs:
        outfit["accessories"] = ", ".join(accs)
        
    if has_forecast and outfit["accessories"] == "None":
        outfit["accessories"] = "Check forecast for changes"
    elif has_forecast:
        outfit["accessories"] += " (Check forecast for changes)"
    
    return outfit

