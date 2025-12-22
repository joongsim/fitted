# app/services/llm_service.py
import httpx

from openai import AsyncOpenAI
from app.core.config import config
from typing import Optional, List


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
) -> str:
    """
    Generate outfit suggestion using LLM with forecast context.

    Args:
        location: Location name
        temp_c: Current temperature in Celsius
        condition: Current weather condition
        forecast: Optional forecast data for next few days
        user_context: Optional user preferences (for future use)

    Returns:
        Outfit suggestion text
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

        Please provide:
        0. A summary of the current conditions and today's forecast
        1. A complete outfit (top, bottom, shoes, outerwear if needed)
        2. Any accessories recommendations (umbrella, sunglasses, etc.)
        3. Tips for staying comfortable throughout the day

        Keep the suggestion concise (1-2 sentences) and practical."""

    try:
        # Get OpenRouter client
        client = get_client()
        if not client:
            return _get_fallback_suggestion(temp_c, condition, forecast is not None)
        
        # Call OpenRouter API using AsyncOpenAI client
        response = await client.chat.completions.create(
            model="google/gemini-2.0-flash-exp:free",
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful fashion stylist who provides practical outfit suggestions based on weather conditions.",
                },
                {"role": "user", "content": full_prompt},
            ],
            temperature=0.7,
            max_tokens=300,
        )

        suggestion = response.choices[0].message.content
        return suggestion.strip()

    except Exception as e:
        print(f"Error calling LLM: {e}")
        # Fallback to rule-based suggestion
        return _get_fallback_suggestion(temp_c, condition, forecast is not None)


def _get_fallback_suggestion(temp_c: float, condition: str, has_forecast: bool) -> str:
    """
    Provide simple rule-based outfit suggestion as fallback.
    
    Args:
        temp_c: Temperature in Celsius
        condition: Weather condition
        has_forecast: Whether forecast data is available
        
    Returns:
        Basic outfit suggestion
    """
    outfit = []
    
    # Temperature-based suggestions
    if temp_c < 5:
        outfit.append("Heavy winter coat, warm layers, insulated boots")
    elif temp_c < 15:
        outfit.append("Jacket or sweater, long pants, closed shoes")
    elif temp_c < 25:
        outfit.append("Light jacket or cardigan, comfortable pants or jeans")
    else:
        outfit.append("Light, breathable clothing, shorts or light pants")
    
    # Condition-based accessories
    condition_lower = condition.lower()
    if any(word in condition_lower for word in ['rain', 'drizzle', 'shower']):
        outfit.append("Don't forget an umbrella and waterproof jacket")
    elif any(word in condition_lower for word in ['snow', 'sleet', 'blizzard']):
        outfit.append("Waterproof boots and warm accessories (hat, gloves)")
    elif 'sun' in condition_lower or 'clear' in condition_lower:
        outfit.append("Sunglasses and sun protection recommended")
    
    suggestion = ". ".join(outfit) + "."
    
    if has_forecast:
        suggestion += " Check the forecast for potential weather changes throughout the day."
    
    return suggestion
