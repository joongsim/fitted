# app/main.py
from datetime import datetime
from typing import Optional
import boto3
import os
import logging
from contextlib import asynccontextmanager

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Depends,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
import asyncio

from typing import Annotated

from pydantic import BaseModel, Field
from pydantic import StringConstraints
from app.services import weather_service
from app.services import llm_service
from app.core.config import config
from app.services import analysis_service
from app.services import user_service
from app.core import auth
from app.models.user import UserCreate, User, Token
from app.models.product import ProductRecommendation
from app.models.wardrobe import WardrobeItemUpdate
from app.services import db_service

logger = logging.getLogger(__name__)


class RecommendRequest(BaseModel):
    location: Annotated[
        str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)
    ]
    include_explanation: bool = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize DB pool, then recommendation service
    await db_service.init_pool()
    from app.services.recommendation_service import init_recommendation_service

    init_recommendation_service()  # one S3 call (or 404) per Lambda warm instance
    yield
    # Shutdown: Close DB pool
    await db_service.close_pool()


app = FastAPI(lifespan=lifespan)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"message": "Welcome to the Fitted Wardrobe Assistant!"}


# --- Authentication Endpoints ---


@app.post("/auth/register", response_model=User)
async def register(user_in: UserCreate):
    """Register a new user."""
    # Check if user already exists
    existing_user = await user_service.get_user_by_email(user_in.email)
    if existing_user:
        logger.warning("Registration attempted for already-registered email.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User with this email already exists",
        )

    user = await user_service.create_user(user_in)
    if not user:
        logger.error("User creation returned None — check db_service logs for details.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user",
        )
    logger.info("User registered successfully: user_id=%s", user.user_id)
    return user


@app.post("/auth/login", response_model=Token)
async def login(response: Response, form_data: OAuth2PasswordRequestForm = Depends()):
    """
    Login user and set HTTP-only cookie.
    Accepts standard OAuth2 form data (username/password).
    """
    user = await user_service.get_user_by_email(form_data.username)
    if not user or not auth.verify_password(
        form_data.password, user["hashed_password"]
    ):
        logger.warning("Failed login attempt — bad credentials.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user["is_active"]:
        logger.warning("Login attempt by inactive user: user_id=%s", user["user_id"])
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user",
        )

    # Create access token
    access_token = auth.create_access_token(data={"sub": str(user["user_id"])})

    # Set HTTP-only cookie for browser/FastHTML
    # secure=False for now since we are on HTTP (no SSL yet)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        max_age=config.access_token_expire_minutes * 60,
        expires=config.access_token_expire_minutes * 60,
        samesite="lax",
        secure=False,
    )

    # Update last login
    await user_service.update_last_login(str(user["user_id"]))
    logger.info("User logged in successfully: user_id=%s", user["user_id"])

    return {"access_token": access_token, "token_type": "bearer"}


@app.post("/auth/logout")
async def logout(response: Response):
    """Logout user by clearing the auth cookie."""
    response.delete_cookie("access_token")
    return {"message": "Successfully logged out"}


# --- User Profile Endpoints ---


@app.get("/users/me", response_model=User)
async def get_me(user_id: str = Depends(auth.get_current_user_id)):
    """Get current user profile."""
    user = await user_service.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user


@app.get("/users/me/preferences")
async def get_my_preferences(user_id: str = Depends(auth.get_current_user_id)):
    """Get current user style and size preferences."""
    return await user_service.get_user_preferences(user_id)


@app.patch("/users/me/preferences")
async def update_my_preferences(
    style_prefs: Optional[dict] = None,
    size_info: Optional[dict] = None,
    user_id: str = Depends(auth.get_current_user_id),
):
    """Update current user style or size preferences."""
    await user_service.update_user_preferences(user_id, style_prefs, size_info)
    return {"message": "Preferences updated successfully"}


@app.get("/debug/config")
def debug_config():
    """Debug endpoint to verify configuration (sensitive values masked)"""
    try:
        openrouter_key = config.openrouter_api_key
        has_openrouter = bool(openrouter_key)
        openrouter_preview = "OpenRouter API Key exists" if openrouter_key else None
    except Exception as e:
        has_openrouter = False
        openrouter_preview = f"Error: {str(e)}"

    try:
        weather_key = config.weather_api_key
        has_weather = bool(weather_key)
        weather_preview = "Weather API Key exists" if weather_key else None
    except Exception as e:
        has_weather = False
        weather_preview = f"Error: {str(e)}"

    from app.services import storage_service

    return {
        "weather_bucket_name": config.weather_bucket_name,
        "storage_service_bucket": storage_service.WEATHER_BUCKET,
        "storage_service_is_local": storage_service.IS_LOCAL,
        "has_openrouter_api_key": has_openrouter,
        "openrouter_key_preview": openrouter_preview,
        "has_weather_api_key": has_weather,
        "weather_key_preview": weather_preview,
        "aws_execution_env": os.environ.get("AWS_EXECUTION_ENV"),
    }


@app.post("/suggest-outfit")
async def suggest_outfit(
    location: str,
    include_forecast: bool = Query(True, description="Include forecast in suggestion"),
):
    """
    Suggest outfit based on current weather and optional forecast.

    Args:
        location: Location name
        include_forecast: Whether to include forecast data
    """
    logger.info(
        "Outfit suggestion requested: location=%s include_forecast=%s",
        location,
        include_forecast,
    )
    try:
        # Get weather data (with or without forecast)
        if include_forecast:
            weather_data = await weather_service.get_weather_with_forecast(
                location, days=1
            )
            forecast = weather_data.get("forecast", {}).get("forecastday", [])
            formatted_forecast = [
                {
                    "date": day["date"],
                    "min_temp_c": day["day"]["mintemp_c"],
                    "max_temp_c": day["day"]["maxtemp_c"],
                    "min_temp_f": day["day"]["mintemp_f"],
                    "max_temp_f": day["day"]["maxtemp_f"],
                    "condition": day["day"]["condition"]["text"],
                    "chance_of_rain": day["day"].get("daily_chance_of_rain", 0),
                }
                for day in forecast
            ]
        else:
            weather_data = await weather_service.get_weather_data(location)
            formatted_forecast = None

        # Extract current weather
        temp_c = weather_data["current"]["temp_c"]
        condition = weather_data["current"]["condition"]["text"]

        # Get LLM suggestion with forecast context
        outfit_suggestion = await llm_service.get_outfit_suggestion(
            location=location,
            temp_c=temp_c,
            condition=condition,
            forecast=formatted_forecast,
        )

        return {
            "weather": {
                "location": weather_data.get("location", {}),
                "current": {
                    "temp_c": temp_c,
                    "temp_f": weather_data["current"]["temp_f"],
                    "condition": condition,
                    "humidity": weather_data["current"]["humidity"],
                    "wind_kph": weather_data["current"]["wind_kph"],
                    "feelslike_f": weather_data["current"]["feelslike_f"],
                    "uv": weather_data["current"]["uv"],
                },
                "forecast": formatted_forecast if include_forecast else None,
            },
            "outfit_suggestion": outfit_suggestion,
        }

    except Exception as e:
        logger.error(
            "Unhandled error generating outfit for location=%s.",
            location,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail=f"Error generating outfit: {str(e)}"
        )


@app.post("/analyze-weather")
async def analyze_weather(bucket: Optional[str] = None, key: Optional[str] = None):
    """Legacy endpoint - queries individual S3 file. Use /analytics/* endpoints for better performance."""
    # Use configured bucket if not provided
    if bucket is None:
        bucket = config.weather_bucket_name

    # If key is not provided, try to find the latest file for today
    if key is None:
        today = datetime.now().strftime("%Y-%m-%d")
        prefix = f"raw/weather/dt={today}/"

        try:
            s3 = boto3.client("s3")
            response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)

            if "Contents" not in response:
                raise HTTPException(
                    status_code=404, detail=f"No weather data found for today ({today})"
                )

            # Get the most recent file
            latest_file = sorted(
                response["Contents"], key=lambda x: x["LastModified"], reverse=True
            )[0]
            key = latest_file["Key"]
            logger.info("Found latest weather file: %s", key)

        except Exception as e:
            if isinstance(e, HTTPException):
                raise e
            raise HTTPException(
                status_code=500, detail=f"Error finding weather data: {str(e)}"
            )

    try:
        analysis_service.query_weather_file(bucket, key)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error analyzing weather file: {str(e)}"
        )

    return {"message": "Weather analysis completed.", "bucket": bucket, "key": key}


@app.get("/analytics/temperature")
async def analytics_by_temperature(
    min_temp: float = Query(15.0, description="Minimum temperature in Celsius"),
    date: Optional[str] = Query(None, description="Date filter (YYYY-MM-DD)"),
):
    """
    Query weather data where temperature is above a threshold.
    Uses Athena for efficient SQL-based queries on S3 data.
    """
    try:
        results = analysis_service.query_weather_by_temperature(min_temp, date)
        return {
            "query": f"temperature > {min_temp}°C",
            "date": date or "all dates",
            "count": len(results),
            "results": results,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analytics query failed: {str(e)}")


@app.get("/analytics/location/{location}")
async def analytics_location_trend(
    location: str,
    days: int = Query(7, ge=1, le=30, description="Number of days to analyze"),
):
    """
    Get weather trend for a specific location over past N days.
    Returns daily averages, min/max temperatures, and other metrics.
    """
    try:
        results = analysis_service.get_location_weather_trend(location, days)
        return {
            "location": location,
            "days": days,
            "count": len(results),
            "trend": results,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Location trend query failed: {str(e)}"
        )


@app.get("/analytics/summary")
async def analytics_summary(
    date: Optional[str] = Query(
        None, description="Date (YYYY-MM-DD), defaults to today"
    )
):
    """
    Get summary analytics for weather data.
    Includes unique locations, avg/min/max temperatures, total readings.
    """
    try:
        summary = analysis_service.get_weather_analytics_summary(date)
        return {"date": date or datetime.now().strftime("%Y-%m-%d"), "summary": summary}
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Summary analytics failed: {str(e)}"
        )


@app.get("/analytics/condition/{condition}")
async def analytics_by_condition(
    condition: str,
    date: Optional[str] = Query(None, description="Date filter (YYYY-MM-DD)"),
):
    """
    Query weather data by condition (e.g., 'Rain', 'Clear', 'Cloudy').
    Returns all locations matching the weather condition.
    """
    try:
        results = analysis_service.get_weather_by_condition(condition, date)
        return {
            "condition": condition,
            "date": date or "all dates",
            "count": len(results),
            "results": results,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Condition query failed: {str(e)}")


@app.get("/weather/{location}")
async def get_current_weather(location: str) -> dict:
    """
    Get current weather for a specified location

    Args:
        location (str): string representing the location (city name, coordinates, etc.)

    Returns:
        dict: Current weather data
    """

    try:
        weather_data = await weather_service.get_weather_data(location)
        return {
            "location": weather_data["location"]["name"],
            "country": weather_data["location"]["country"],
            "current": {
                "temperature_c": weather_data["current"]["temp_c"],
                "temperature_f": weather_data["current"]["temp_f"],
                "condition": weather_data["current"]["condition"]["text"],
                "humidity": weather_data["current"]["humidity"],
                "wind_kph": weather_data["current"]["wind_kph"],
                "feels_like_c": weather_data["current"]["feelslike_c"],
            },
            "last_updated": weather_data["current"]["last_updated"],
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch weather: {str(e)}"
        )


@app.get("/weather/{location}/forecast")
async def get_weather_forecast(
    location: str,
    days: int = Query(1, ge=1, le=10, description="Number of forecast days"),
):
    """
    Get weather forecast for a location.

    Args:
        location: City name or coordinates
        days: Number of forecast days (1-10)

    Returns:
        Current weather plus multi-day forecast
    """
    try:
        forecast_data = await weather_service.get_weather_with_forecast(location, days)

        # Format forecast for easier consumption
        formatted_forecast = []
        for day in forecast_data.get("forecast", {}).get("forecastday", []):
            formatted_forecast.append(
                {
                    "date": day["date"],
                    "max_temp_c": day["day"]["maxtemp_c"],
                    "min_temp_c": day["day"]["mintemp_c"],
                    "avg_temp_c": day["day"]["avgtemp_c"],
                    "condition": day["day"]["condition"]["text"],
                    "chance_of_rain": day["day"].get("daily_chance_of_rain", 0),
                    "sunrise": day["astro"]["sunrise"],
                    "sunset": day["astro"]["sunset"],
                }
            )

        return {
            "location": forecast_data["location"]["name"],
            "current": {
                "temp_c": forecast_data["current"]["temp_c"],
                "condition": forecast_data["current"]["condition"]["text"],
            },
            "forecast": formatted_forecast,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch forecast: {str(e)}"
        )


# --- Recommendation Endpoints ---


@app.get("/catalog/search")
async def catalog_search(
    q: str = Query(..., description="Text search query"),
    limit: int = Query(20, ge=1, le=100, description="Max results"),
    _: str = Depends(
        auth.get_current_user_id
    ),  # auth required — compute-intensive endpoint
) -> dict:
    """
    Search catalog_items by semantic similarity to a text query.

    Embeds the query text using CLIP and performs ANN search on catalog_items.
    Intended for internal debugging and embedding quality verification — not a
    public-facing product endpoint. Authentication is enforced to prevent
    unauthenticated callers from triggering CLIP model loads.
    """
    from app.services.embedding_service import encode_text
    from app.services import dev_catalog_service

    logger.info("Catalog search: q=%r limit=%d", q, limit)
    try:
        query_embedding = encode_text(q)
        items = await dev_catalog_service.search(query_embedding, limit=limit)
        return {
            "query": q,
            "count": len(items),
            "results": [
                {
                    "item_id": item.item_id,
                    "title": item.title,
                    "price": item.price,
                    "product_url": item.product_url,
                    "image_url": item.image_url,
                    "attributes": item.attributes,
                }
                for item in items
            ],
        }
    except Exception:
        logger.error("Catalog search failed: q=%r", q, exc_info=True)
        raise HTTPException(status_code=500, detail="Catalog search failed")


@app.post("/recommend-products")
async def recommend_products(
    request: RecommendRequest,
    user_id: str = Depends(auth.get_current_user_id),
) -> dict:
    """
    Full product recommendation pipeline.

    1. Fetches current weather for request.location
    2. Loads user preferences from DB
    3. Runs the recommendation pipeline:
       LLM query → CLIP embed → vector cache → ANN → two-tower reranking
    4. Returns ranked ProductRecommendation list

    Authentication required. user_id is derived from the JWT — the caller
    always receives recommendations based on their own wardrobe and preferences.

    The include_explanation flag adds ~1–2s latency (extra LLM call). Default False.
    """
    from app.services import user_service, weather_service
    from app.services.recommendation_service import get_recommendation_service

    logger.info(
        "Recommend products: user_id=%s location=%s explain=%s",
        user_id,
        request.location,
        request.include_explanation,
    )

    try:
        # Fetch weather + user preferences concurrently
        weather_data, prefs = await asyncio.gather(
            weather_service.get_weather_data(request.location),
            user_service.get_user_preferences(user_id),
        )

        weather_context = {
            "temp_c": weather_data["current"]["temp_c"],
            "condition": weather_data["current"]["condition"]["text"],
            "location": request.location,
        }
        style_preferences = (prefs or {}).get("style_preferences", {})

        # Use the module-level singleton — no S3 call on the hot path
        service = get_recommendation_service()

        recommendations = await service.recommend(
            user_id=user_id,
            location=request.location,
            weather_context=weather_context,
            style_preferences=style_preferences,
            top_k=10,
            include_explanation=request.include_explanation,
        )

        from app.services.affiliate_service import (
            detect_network,
            get_affiliate_config,
            record_affiliate_click,
            rewrite_to_affiliate_url,
        )

        affiliate_cfg = get_affiliate_config()
        recs_out = []
        for rec in recommendations:
            rec_dict = rec.model_dump()
            original_url = rec_dict.get("product_url") or ""
            affiliate_url = rewrite_to_affiliate_url(original_url, **affiliate_cfg)
            network = (
                detect_network(affiliate_url)
                if affiliate_url != original_url
                else "none"
            )
            click_id = await record_affiliate_click(
                user_id=user_id,
                item_id=rec_dict["item_id"],
                original_url=original_url,
                affiliate_url=affiliate_url,
                network=network,
            )
            rec_dict["click_url"] = f"/r/{click_id}"
            recs_out.append(rec_dict)

        return {
            "user_id": user_id,
            "location": request.location,
            "weather": weather_context,
            "count": len(recs_out),
            "recommendations": recs_out,
        }

    except Exception:
        logger.error("Recommendation failed: user_id=%s", user_id, exc_info=True)
        raise HTTPException(status_code=500, detail="Recommendation pipeline failed")


# --- Wardrobe Endpoints ---


@app.get("/wardrobe")
async def list_wardrobe(
    user_id: str = Depends(auth.get_current_user_id),
) -> dict:
    """
    Return all wardrobe items for the authenticated user.

    Each item includes a presigned S3 URL (1 h expiry) when an image exists.
    """
    from app.services import wardrobe_service
    from app.services.storage_service import get_image_presigned_url
    from app.models.wardrobe import WardrobeItemResponse

    items = await wardrobe_service.get_wardrobe_items(user_id)
    results = []
    for item in items:
        image_url = None
        if item["image_s3_key"]:
            image_url = get_image_presigned_url(item["image_s3_key"])
        results.append(
            WardrobeItemResponse(
                item_id=item["item_id"],
                name=item["name"],
                category=item["category"],
                image_url=image_url,
                tags=item["tags"],
                created_at=item["created_at"],
            ).model_dump()
        )

    logger.info("GET /wardrobe: user_id=%s -> %d items", user_id, len(results))
    return {"count": len(results), "items": results}


@app.post("/wardrobe", status_code=status.HTTP_201_CREATED)
async def add_wardrobe_item(
    name: str = Form(..., min_length=1, max_length=255),
    category: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
    user_id: str = Depends(auth.get_current_user_id),
) -> dict:
    """
    Create a new wardrobe item (multipart/form-data).

    Accepts ``name`` and optional ``category`` as form fields, and an optional
    ``image`` file upload.  The image is written to S3 at
    ``wardrobe-images/{user_id}/{item_id}.jpg``; the S3 key is stored on the row.
    The CLIP embedding column is NULL until the wardrobe backfill script runs.
    """
    from app.services import wardrobe_service
    from app.services.storage_service import (
        upload_wardrobe_image,
        get_image_presigned_url,
    )
    from app.models.wardrobe import WardrobeItemResponse
    import uuid

    logger.info(
        "POST /wardrobe: user_id=%s name=%r category=%s has_image=%s",
        user_id,
        name,
        category,
        image is not None,
    )

    # Generate the item_id first so the S3 key is predictable
    item_id_str = str(uuid.uuid4())
    image_s3_key = None

    if image and image.filename:
        file_content = await image.read()
        content_type = image.content_type or "image/jpeg"
        image_s3_key = upload_wardrobe_image(
            file_content=file_content,
            content_type=content_type,
            user_id=user_id,
            item_id=item_id_str,
        )

    item = await wardrobe_service.create_wardrobe_item(
        user_id=user_id,
        name=name,
        category=category,
        image_s3_key=image_s3_key,
    )

    if image_s3_key:
        import asyncio

        async def _embed_wardrobe_image(item_id: str, s3_key: str) -> None:
            try:
                from app.services.embedding_service import encode_image

                vec = encode_image(s3_key)
                async with db_service.get_connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "UPDATE wardrobe_items SET embedding = %s::vector WHERE item_id = %s",
                            (vec.tolist(), item_id),
                        )
                        await conn.commit()
                logger.info("Wardrobe image embedded: item_id=%s", item_id)
            except Exception:
                logger.error(
                    "Failed to embed wardrobe image: item_id=%s",
                    item_id,
                    exc_info=True,
                )

        asyncio.create_task(_embed_wardrobe_image(item["item_id"], image_s3_key))

    image_url = get_image_presigned_url(image_s3_key) if image_s3_key else None
    return WardrobeItemResponse(
        item_id=item["item_id"],
        name=item["name"],
        category=item["category"],
        image_url=image_url,
        tags=item["tags"],
        created_at=item["created_at"],
    ).model_dump()


@app.delete("/wardrobe/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_wardrobe_item(
    item_id: str,
    user_id: str = Depends(auth.get_current_user_id),
) -> None:
    """
    Delete a wardrobe item.

    Ownership is enforced: users can only delete their own items.  Returns 404
    when the item does not exist or belongs to a different user.
    """
    from app.services import wardrobe_service

    deleted = await wardrobe_service.delete_wardrobe_item(
        user_id=user_id, item_id=item_id
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Wardrobe item not found"
        )
    logger.info("DELETE /wardrobe/%s: deleted by user_id=%s", item_id, user_id)


@app.put("/wardrobe/{item_id}")
async def update_wardrobe_item_endpoint(
    item_id: str,
    body: WardrobeItemUpdate,
    user_id: str = Depends(auth.get_current_user_id),
) -> dict:
    """
    Update name, category, and/or tags on a wardrobe item.

    All fields are optional — only provided fields are changed (PATCH semantics
    via PUT).  Ownership is enforced: 404 when the item doesn't exist or belongs
    to a different user.
    """
    from app.services import wardrobe_service
    from app.services.storage_service import get_image_presigned_url
    from app.models.wardrobe import WardrobeItemResponse

    item = await wardrobe_service.update_wardrobe_item(
        user_id=user_id,
        item_id=item_id,
        name=body.name,
        category=body.category,
        tags=body.tags,
    )
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Wardrobe item not found"
        )

    image_url = (
        get_image_presigned_url(item["image_s3_key"]) if item["image_s3_key"] else None
    )
    logger.info("PUT /wardrobe/%s: updated by user_id=%s", item_id, user_id)
    return WardrobeItemResponse(
        item_id=item["item_id"],
        name=item["name"],
        category=item["category"],
        image_url=image_url,
        tags=item["tags"],
        created_at=item["created_at"],
    ).model_dump()


# --- Interaction Logging Endpoints ---


class InteractionCreate(BaseModel):
    """Body for POST /interactions."""

    item_id: str
    interaction_type: str  # 'click' | 'save' | 'dismiss'
    weather_context: dict = {}
    query_text: Optional[str] = None


@app.post("/interactions", status_code=status.HTTP_201_CREATED)
async def log_interaction(
    body: InteractionCreate,
    user_id: str = Depends(auth.get_current_user_id),
) -> dict:
    """
    Log a user interaction (click, save, or dismiss) with a catalog item.

    These signals feed the Week 8 training pipeline.  The endpoint is designed
    to be called fire-and-forget from the frontend (``hx_swap="none"``), so it
    returns a minimal 201 response.

    Authentication is required: user_id is derived from the JWT so a user can
    only log interactions for themselves (no IDOR).
    """
    if body.interaction_type not in ("click", "save", "dismiss"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="interaction_type must be one of: click, save, dismiss",
        )

    async with db_service.get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO user_interactions
                    (user_id, item_id, interaction_type, weather_context, query_text)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    body.item_id,
                    body.interaction_type,
                    body.weather_context,
                    body.query_text,
                ),
            )
            await conn.commit()

    logger.info(
        "POST /interactions: user_id=%s item_id=%s type=%s",
        user_id,
        body.item_id,
        body.interaction_type,
    )
    return {"status": "logged"}


# --- Preference Pair Endpoints ---


class PreferencePairCreate(BaseModel):
    """Body for POST /preferences/pairs."""

    item_a_id: str
    item_b_id: str
    preferred: str  # 'a' | 'b'


@app.post("/preferences/pairs", status_code=status.HTTP_201_CREATED)
async def record_preference_pair(
    body: PreferencePairCreate,
    user_id: str = Depends(auth.get_current_user_id),
) -> dict:
    """
    Record a pairwise preference signal ("I prefer item A over item B").

    These signals feed the Bradley-Terry preference reranker
    (``preference_reranker.py``) and the Week 8 training pipeline.

    Authentication required: user_id is from the JWT.
    """
    if body.preferred not in ("a", "b"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="preferred must be 'a' or 'b'",
        )

    async with db_service.get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO preference_pairs
                    (user_id, item_a_id, item_b_id, preferred)
                VALUES (%s, %s, %s, %s)
                """,
                (user_id, body.item_a_id, body.item_b_id, body.preferred),
            )
            await conn.commit()

    logger.info(
        "POST /preferences/pairs: user_id=%s a=%s b=%s preferred=%s",
        user_id,
        body.item_a_id,
        body.item_b_id,
        body.preferred,
    )
    return {"status": "recorded"}


# --- Affiliate Redirect ---


@app.get("/r/{click_id}")
async def affiliate_redirect(click_id: str) -> None:
    """
    Server-side affiliate redirect.

    Marks the affiliate_clicks row as clicked (sets clicked_at = NOW()) and
    issues an HTTP 302 to the affiliate URL.  If the click_id is not found or
    was already clicked, falls back to the stored affiliate URL without
    re-marking.

    No authentication required — the click_id is a single-use opaque token
    generated for each recommendation result.  The user's identity is already
    embedded in the affiliate_clicks row.
    """
    from fastapi.responses import RedirectResponse

    from app.services.affiliate_service import resolve_and_record_click

    affiliate_url = await resolve_and_record_click(click_id)
    if affiliate_url is None:
        # Already clicked or not found — look up the URL without marking again
        async with db_service.get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT affiliate_url FROM affiliate_clicks WHERE click_id = %s",
                    (click_id,),
                )
                row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Click not found")
        affiliate_url = row[0]

    logger.info("GET /r/%s → %s", click_id, affiliate_url[:80])
    return RedirectResponse(url=affiliate_url, status_code=302)
