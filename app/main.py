# app/main.py
from fastapi import FastAPI
from app.services import weather_service
from app.services import llm_service # Import the new LLM service

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Welcome to the Fitted Wardrobe Assistant!"}

@app.post("/suggest-outfit/")
async def suggest_outfit(location: str):
    weather_data = await weather_service.get_weather_data(location)

    # Extract temperature and condition from weather data
    temp_c = weather_data["current"]["temp_c"]
    condition = weather_data["current"]["condition"]["text"]

    # Call the LLM service to get an outfit suggestion
    outfit_suggestion = await llm_service.get_outfit_suggestion(
        location=location,
        temp_c=temp_c,
        condition=condition
    )

    # Return both weather data and the outfit suggestion
    return {
        "location": location,
        "weather": weather_data,
        "outfit_suggestion": outfit_suggestion
    }

