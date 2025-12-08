# app/main.py
from fastapi import FastAPI
from mangum import Mangum
from app.services import weather_service
from app.services import llm_service # Import the new LLM service
from scripts.analyze_weather import query_weather_file

app = FastAPI()

# Lambda handler - this is what AWS Lambda will call
handler = Mangum(app)

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

@app.post("/analyze-weather/")
async def analyze_weather(bucket: str, key: str):
    query_weather_file(bucket, key)
    return {"message": "Weather analysis completed."}