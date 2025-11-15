from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Welcome to the Fitted Wardrobe Assistant!"}

@app.post("/suggest-outfit/")
async def suggest_outfit(location: str):
    # This endpoint will eventually:
    # 1. Get weather for the location.
    # 2. Get user preferences.
    # 3. Call the LLM to suggest an outfit.
    # 4. Return the suggestion.
    return {"message": f"Outfit suggestion for {location} coming soon!"}
