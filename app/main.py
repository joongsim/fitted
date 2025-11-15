from fastapi import FastAPI
from app.services import weather_service

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Welcome to the Fitted Wardrobe Assistant!"}

@app.post("/suggest-outfit/")
async def suggest_outfit(location: str):
    weather_data = await weather_service.get_weather_data(location)
    # This endpoint will eventually:
    # 1. Get user preferences.
    # 2. Call the LLM to suggest an outfit.
    # 3. Return the suggestion.
    return weather_data
