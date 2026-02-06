"""FastHTML frontend for Fitted - AI Weather Stylist"""

# ruff: noqa: F405
from fasthtml.common import *  # noqa: F403, F405 star import ok for fasthtml
import httpx
import os

# API Configuration
API_BASE_URL = os.environ.get(
    "API_BASE_URL", "https://dtv7713h25.execute-api.us-west-1.amazonaws.com"
)

# Custom CSS for light theme styling (green palette)
custom_css = Style("""
/* css */
    :root {
        --pico-background-color: #fffbeb; 
        --pico-card-background-color: #ffffff;
        --pico-color: #1e293b; /* Dark text for light background */
        --pico-muted-color: #64748b;
        --pico-primary-hover: #15803d;
        --pico-border-radius: 0;
    }
    
    body {
        background-color: #fffbeb;
        width: 100%;
        height: 100%;
        margin: 0;
        padding: 0;
        min-height: 200vh;
        display: flex;
        justify-content: center;
    }
    
    .container {
        max-width: 450px;
        width: 100%;
        padding: 1rem;
        background-color: transparent;
    }
    
    .header {
        text-align: center;
        margin-bottom: 2rem;
    }
    
    .header h1 {
        font-size: 3rem;
        margin-bottom: 0.5rem;
        background-color: #95FB62;
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        margin-top: 10%;
    }
    
    .header p {
        color: var(--pico-muted-color);
    }
    
    .search-form {
        display: flex;
        flex-direction: column;
        gap: 0.5rem;
        margin-bottom: 1.5rem;
    }
    
    .search-form input {
        width: 100%;
        background-color: var(--pico-card-background-color);
        border: 2px solid #000000;
        color: var(--pico-color);
    }
    
    .search-form button {
        width: 100%;
        background-color: #95FB62 !important;
        border: 2px solid #000000 !important;
        --pico-background-color: #16a34a;
    }
    
    .search-form button:hover {
        background-color:rgb(0, 145, 255) !important;
        border: 2px solid #000000 !important;
        --pico-background-color: rgb(0, 145, 255);
    }
    
    .error-message {
        background-color: #fef2f2;
        border: 1px solid #fecaca;
        color: #dc2626;
        padding: 1rem;
        border-radius: 0;
        margin-bottom: 1rem;
    }
    
    .weather-card {
        background-color: var(--pico-card-background-color);
        border-radius: 0;
        padding: 1.5rem;
        margin-bottom: 1rem;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
    }
    
    .weather-main {
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    
    .weather-temp {
        font-size: 3.5rem;
        font-weight: bold;
        margin: 0;
        line-height: 1;
        color: var(--pico-primary);
    }
    
    .weather-condition {
        color: #000000;
        margin-top: 0.25rem;
    }
    
    .weather-location {
        text-align: right;
    }
    
    .weather-location .location-name {
        font-weight: bold;
        font-size: 1.25rem;
        color: #000000;
    }
    
    .weather-location .location-label {
        color: var(--pico-muted-color);
        font-size: 0.875rem;
    }
    
    .retro-card {
        background-color: #ffffff;
        border: 2px solid #000000;
        border-radius: 0;
        color: #16a34a;
        box-shadow: none;
    }

/* Clean up existing classes (remove colors/borders so they use the shared class) */
    .weather-card {
        padding: 1.5rem;
        margin-bottom: 1rem;
        /* Background and border are now handled by .retro-card */
    }

    .metric-card {
        padding: 1rem;
        /* Background and border are now handled by .retro-card */
    }

    /* Update text elements to inherit the green color from the card */
    .weather-temp, 
    .weather-condition, 
    .weather-location .location-label,
    .metric-label,
    .metric-value {
        color: inherit; /* This ensures they all become green */
    }
    .metrics-grid {
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 1rem;
        margin-bottom: 1.5rem;
    }
    
    .metric-label {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        font-size: 0.875rem;
        color: rgb(0, 0, 0);
        margin-bottom: 0.25rem;
    }
    
    .metric-value {
        font-size: 1.25rem;
        font-weight: bold;
    }
    
    .outfit-section h2 {
        font-size: 1.25rem;
        margin-bottom: 1rem;
    }
    
    .outfit-item {
        background-color: #f0fdf4;
        border-radius: 0;
        padding: 1rem;
        margin-bottom: 0.75rem;
        border: 2px solid #000000;
    }
    
    .outfit-item-label {
        color: var(--pico-muted-color);
        font-size: 0.75rem;
        text-transform: uppercase;
        font-weight: bold;
        margin-bottom: 0.25rem;
    }
    
    .outfit-item-value {
        font-size: 1rem;
    }
    
    .loading {
        display: inline-block;
        width: 1rem;
        height: 1rem;
        border: 2px solid #16a34a;
        border-radius: 50%;
        border-top-color: transparent;
        animation: spin 0.8s linear infinite;
        cursor: pointer;
        user-select: none;
    }
    
    @keyframes spin {
        to { transform: rotate(360deg); }
    }
    
    .htmx-request .loading-text {
        display: none;
    }
    
    .htmx-request .loading-spinner {
        display: inline-block;
    }
    
    .loading-spinner {
        display: none;
    }
""")

app = FastHTMLWithLiveReload(
    hdrs=(
        Link(
            rel="stylesheet",
            href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css",
        ),
        custom_css,
    )
)


def metric_card(label: str, value: str, icon: str) -> Div:
    """A small card for weather metrics."""
    icons = {
        "thermometer": "🌡️",
        "droplets": "💧",
        "wind": "💨",
        "sun": "☀️",
    }
    return Div(
        Div(Span(icons.get(icon, "📊")), Span(label), cls="metric-label"),
        Div(value, cls="metric-value"),
        cls="metric-card retro-card",
    )


def outfit_item(label: str, value: str) -> Div:
    """A themed container for an outfit item."""
    return Div(
        Div(label, cls="outfit-item-label"),
        Div(value, cls="outfit-item-value"),
        cls="outfit-item",
    )


def weather_results(location: str, weather: dict, forecast: dict, outfit: dict) -> Div:
    """Render weather and outfit results."""
    temp_f = weather.get("temp_f", "")
    min_temp_f = forecast.get("min_temp_f", "")
    max_temp_f = forecast.get("max_temp_f", "")
    condition = weather.get("condition", "")
    humidity = weather.get("humidity", "")
    wind_kph = weather.get("wind_kph", "")
    feelslike_f = weather.get("feelslike_f", "")
    uv = weather.get("uv", "")
    
    outfit_top = outfit.get("top", "None") if isinstance(outfit, dict) else str(outfit)
    outfit_bottom = outfit.get("bottom", "None") if isinstance(outfit, dict) else ""
    outfit_outerwear = (
        outfit.get("outerwear", "None") if isinstance(outfit, dict) else ""
    )
    outfit_accessories = (
        outfit.get("accessories", "None") if isinstance(outfit, dict) else ""
    )

    outfit_items = [
        outfit_item("Top", outfit_top),
        outfit_item("Bottom", outfit_bottom),
    ]

    if outfit_outerwear and outfit_outerwear != "None":
        outfit_items.append(outfit_item("Outerwear", outfit_outerwear))

    if outfit_accessories and outfit_accessories != "None":
        outfit_items.append(outfit_item("Accessories", outfit_accessories))

    return Div(
        # Combined Weather Card
        Div(
            Div(
                Div(
                    Div(location, cls="location-name"),
                    Div(f"{temp_f}°F", cls="weather-temp"),
                    Div(condition, cls="weather-condition"),
                    Div(f"Low: {min_temp_f}°F", cls="weather-low"),
                    Div(f"High: {max_temp_f}°F", cls="weather-high"),
                ),
                # Div(

                # ),
                cls="weather-main",
                style="margin-bottom: 1.5rem",
            ),
            # Metrics Grid inside the card
            Div(
                metric_card("Feels Like", f"{feelslike_f}°F", "thermometer"),
                metric_card("Humidity", f"{humidity}%", "droplets"),
                metric_card("Wind", f"{wind_kph} kph", "wind"),
                metric_card("UV Index", str(uv), "sun"),
                cls="metrics-grid",
            ),
            cls="weather-card retro-card",
        ),
        # Outfit Section
        Div(H2("Stylist Recommendation"), *outfit_items, cls="outfit-section"),
        id="results",
    )


def error_message(message: str) -> Div:
    """Render an error message."""
    return Div(f"⚠️ {message}", cls="error-message", id="results")


@app.get("/")
def home():
    """Main page."""
    return Html(
        Head(
            Title("Fitted - AI Weather Stylist"),
            Meta(charset="utf-8"),
            Meta(name="viewport", content="width=device-width, initial-scale=1"),
            Link(
                rel="stylesheet",
                href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css",
            ),
            Script(src="https://unpkg.com/htmx.org@2.0.4"),
            custom_css,
        ),
        Body(
            Div(
                # Header
                Div(H1("fitted"), cls="header"),
                # Search Form
                Form(
                    Div(
                        Input(
                            type="text",
                            name="location",
                            placeholder="Enter city (e.g., San Francisco)",
                            required=True,
                            cls="half-width-input",
                        ),
                        cls="search-form-input-row",
                    ),
                    Div(
                        Button(
                            Span("Go", cls="loading-text"),
                            Span(cls="loading loading-spinner"),
                            type="submit",
                            cls="full-width-btn",
                        ),
                        cls="search-form-btn-row",
                    ),
                    hx_post="/get-outfit",
                    hx_target="#results",
                    hx_swap="outerHTML",
                    hx_indicator="closest form",
                    cls="search-form",
                ),
                # Results placeholder
                Div(id="results"),
                cls="container",
            )
        ),
        data_theme="light",
    )


def format_location(location_data: dict, fallback: str) -> str:
    """Format location with proper capitalization and state/region."""
    if location_data:
        name = location_data.get("name", "")
        region = location_data.get("region", "")
        if name and region:
            return f"{name}, {region}"
        elif name:
            return name
    # Fallback: title case the user input
    return fallback.title()


@app.post("/get-outfit")
async def get_outfit(location: str):
    """Fetch weather and outfit suggestion from the API."""
    if not location or not location.strip():
        return error_message("Please enter a location.")

    location = location.strip()

    try:
        async with httpx.AsyncClient() as client:
            url = f"{API_BASE_URL}/suggest-outfit"
            response = await client.post(
                url, params={"location": location}, timeout=30.0
            )

            # Try with trailing slash if 404
            if response.status_code == 404:
                url = f"{API_BASE_URL}/suggest-outfit/"
                response = await client.post(
                    url, params={"location": location}, timeout=30.0
                )

            if response.status_code != 200:
                try:
                    error_detail = response.json().get("detail", "Unknown error")
                except Exception:
                    error_detail = response.text
                return error_message(f"Error: {error_detail}")

            data = response.json()
            weather_data = data.get("weather", {})
            location_info = weather_data.get("location", {})
            weather = weather_data.get("current", {})
            forecast = weather_data.get("forecast", {})
            outfit = data.get("outfit_suggestion", {})

            # display_location = format_location(location_info, location)
            display_location = location_info.get("name", "")
            display_location += ", " + location_info.get("region", "")
            # display_location += ", " + location_info.get("country", "")

            return weather_results(display_location, weather, forecast, outfit)

    except httpx.TimeoutException:
        return error_message("Request timed out. Please try again.")
    except Exception as e:
        return error_message(f"Connection error: {str(e)}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5001)
