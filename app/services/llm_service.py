# app/services/llm_service.py
import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv() # Load environment variables from .env file

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
if not OPENROUTER_API_KEY:
    raise ValueError("OPENROUTER_API_KEY not found in environment variables.")

# Initialize the OpenAI client to use OpenRouter
# The base_url points to OpenRouter's API endpoint
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

async def get_outfit_suggestion(location: str, temp_c: float, condition: str) -> str:
    """
    Generates an outfit suggestion using an LLM via OpenRouter.
    """
    prompt = (
        f"Suggest an outfit for {location} with weather: {temp_c}°C, {condition}. "
        "Consider casual style. Provide a concise suggestion, e.g., 'Wear a light jacket, t-shirt, and jeans.'"
    )

    try:
        chat_completion = await client.chat.completions.create(
            model="openai/gpt-4o-mini", # Using the specified model
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

