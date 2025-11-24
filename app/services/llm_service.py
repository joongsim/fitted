# app/services/llm_service.py
import os
from openai import AsyncOpenAI

# Try to load .env file for local development (optional - won't exist in Lambda)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # dotenv not installed (Lambda environment)
    pass

def get_api_key():
    """Get API key from environment variables."""
    return os.environ.get("OPENROUTER_API_KEY")

def get_client():
    """Get or create OpenRouter client."""
    api_key = get_api_key()
    if not api_key:
        return None
    
    return AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )

async def get_outfit_suggestion(location: str, temp_c: float, condition: str) -> str:
    """
    Generates an outfit suggestion using an LLM via OpenRouter.
    Works in both local development (with .env) and Lambda (with environment variables).
    """
    # Get client (creates new one each time to ensure fresh API key)
    client = get_client()
    
    if not client:
        print("Warning: OPENROUTER_API_KEY not found in environment variables")
        return "Could not generate an outfit suggestion at this time. API key not configured."
    
    prompt = (
        f"Suggest an outfit for {location} with weather: {temp_c}°C, {condition}. "
        "Consider casual style. Provide a concise suggestion, e.g., 'Wear a light jacket, t-shirt, and jeans.'"
    )

    try:
        chat_completion = await client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful fashion assistant."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=150,
        )
        # Extract the content from the LLM's response
        suggestion = chat_completion.choices[0].message.content
        return suggestion
    except Exception as e:
        print(f"Error calling LLM service: {e}")
        return "Could not generate an outfit suggestion at this time."

