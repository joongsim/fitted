"""FastHTML frontend for Fitted - AI Weather Stylist"""

# ruff: noqa: F405
import logging

import boto3
import httpx
from fasthtml.common import *  # noqa: F403, F405 star import ok for fasthtml
import os

logger = logging.getLogger(__name__)

# API Configuration
API_BASE_URL = os.environ.get(
    "API_BASE_URL", "http://localhost:8000"
)

def get_ssm_parameter(name: str, default: str = None) -> str:
    """Fetch parameter from SSM Parameter Store or return default."""
    if os.environ.get("USE_SSM", "true").lower() != "false":
        try:
            ssm = boto3.client("ssm", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-west-1"))
            response = ssm.get_parameter(Name=name, WithDecryption=True)
            logger.debug("Fetched SSM parameter: %s", name)
            return response["Parameter"]["Value"]
        except Exception:
            logger.error(
                "Failed to fetch SSM parameter: %s", name, exc_info=True
            )
            return default
    return default

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
        flex-direction: column;
        align-items: center;
    }
    
    .nav-bar {
        width: 100%;
        max-width: 450px;
        display: flex;
        justify-content: space-between;
        padding: 1rem;
        border-bottom: 2px solid #000;
        margin-bottom: 1.5rem;
    }
    .nav-bar a { text-decoration: none; color: #000; font-weight: bold; }
    .nav-bar a.active { color: #16a34a; }

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
        
    .retro-card {
        background-color: #ffffff;
        border: 2px solid #000000;
        border-radius: 0;
        color: #16a34a;
        box-shadow: none;
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
        color: #000000;
        font-size: 0.875rem;
    }
    
    .weather-high-low {
        display: flex;
        gap: 1rem;
        margin-top: 0.5rem;
        color: #000000;
    }


/* Clean up existing classes (remove colors/borders so they use the shared class) */
    .weather-card {
        padding: 1.5rem;
        margin-bottom: 1rem;
        /* Background and border are now handled by .retro-card */
    }

    .metric-card {
        padding: 0.5rem 1rem;
        display: flex;
        align-items: center;
        gap: 0.5rem;
        /* Background and border are now handled by .retro-card */
    }

    /* Update text elements to inherit the green color from the card */
    .weather-temp, 
    .weather-condition {
        color: inherit; /* This ensures they all become green */
    }
    .metric-label,
    .metric-value {
        color: #000000;
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
        margin-bottom: 0;
        white-space: nowrap;
    }
    
    .metric-value {
        font-size: 1rem;
        font-weight: bold;
        margin-left: auto;
    }
    
    .outfit-section h2 {
        font-size: 1.25rem;
        margin-bottom: 1rem;
    }
    
    .outfit-item {
        background-color: #ffffff;
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

    /* Auth Styles */
    .auth-form {
        display: flex;
        flex-direction: column;
        gap: 1rem;
        margin-top: 2rem;
    }
    .auth-form input {
        border: 2px solid #000;
    }
    .auth-form button {
        background-color: #95FB62;
        border: 2px solid #000;
        color: #000;
        font-weight: bold;
    }
    .auth-link {
        text-align: center;
        margin-top: 1rem;
        font-size: 0.875rem;
    }
    .auth-link a { color: #16a34a; font-weight: bold; }
""")

# Get session secret from environment or generate a random one for local use
SESSION_SECRET = get_ssm_parameter("/fitted/session-secret", os.environ.get("SESSION_SECRET", "local-dev-secret-key-change-in-prod"))

AppClass = FastHTMLWithLiveReload if os.environ.get("DEV", "false").lower() == "true" else FastHTML
app = AppClass(
    secret_key=SESSION_SECRET,
    hdrs=(
        Link(
            rel="stylesheet",
            href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css",
        ),
        Script(src="https://unpkg.com/htmx.org@2.0.4"),
        custom_css,
    )
)

def nav_bar(session):
    """Render the navigation bar based on auth state."""
    is_logged_in = "access_token" in session
    links = [A("fitted", href="/")]
    if is_logged_in:
        links.extend([
            A("Wardrobe", href="/wardrobe"),
            A("Logout", href="/logout")
        ])
    else:
        links.extend([
            A("Login", href="/login"),
            A("Register", href="/register")
        ])
    return Div(*links, cls="nav-bar")

def metric_card(label: str, value: str, icon: str) -> Div:
    """A small card for weather metrics."""
    icons = {"thermometer": "🌡️", "droplets": "💧", "wind": "💨", "sun": "☀️"}
    return Div(
        Div(Span(icons.get(icon, "📊")), Span(f"{label}:"), cls="metric-label"),
        Div(value, cls="metric-value"),
        cls="metric-card retro-card",
    )

def outfit_item(label: str, value: str) -> Div:
    """A themed container for an outfit item."""
    return Div(
        Div(label, cls="outfit-item-label"),
        Div(value, cls="outfit-item-value"),
        cls="outfit-item retro-card",
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
    outfit_outerwear = outfit.get("outerwear", "None") if isinstance(outfit, dict) else ""
    outfit_accessories = outfit.get("accessories", "None") if isinstance(outfit, dict) else ""

    outfit_items = [outfit_item("Top", outfit_top), outfit_item("Bottom", outfit_bottom)]
    if outfit_outerwear and outfit_outerwear != "None": outfit_items.append(outfit_item("Outerwear", outfit_outerwear))
    if outfit_accessories and outfit_accessories != "None": outfit_items.append(outfit_item("Accessories", outfit_accessories))

    return Div(
        Div(
            Div(
                Div(
                    Div(location, cls="location-name"),
                    Div(f"{temp_f}°F", cls="weather-temp"),
                    Div(condition, cls="weather-condition"),
                    Div(f"Low: {min_temp_f}°F", cls="weather-low"),
                    Div(f"High: {max_temp_f}°F", cls="weather-high"),
                ),
                cls="weather-main",
                style="margin-bottom: 1.5rem",
            ),
            Details(
                Summary("Weather Details"),
                Div(
                    metric_card("Feels Like", f"{feelslike_f}°F", "thermometer"),
                    metric_card("Humidity", f"{humidity}%", "droplets"),
                    metric_card("Wind", f"{wind_kph} kph", "wind"),
                    metric_card("UV Index", str(uv), "sun"),
                    cls="metrics-list",
                ),
            ),
            cls="weather-card retro-card",
        ),
        Div(*outfit_items, cls="outfit-section"),
        id="results",
    )

def error_message(message: str) -> Div:
    """Render an error message."""
    return Div(f"⚠️ {message}", cls="error-message", id="results")

@app.get("/")
def home(session):
    """Main page."""
    return Title("Fitted - AI Weather Stylist"), Body(
        nav_bar(session),
        Div(
            Div(H1("fitted"), cls="header"),
            Form(
                Div(
                    Input(type="text", name="location", placeholder="Enter city (e.g., San Francisco)", required=True),
                    cls="search-form-input-row",
                ),
                Div(
                    Button(Span("Go", cls="loading-text"), Span(cls="loading loading-spinner"), type="submit"),
                    cls="search-form-btn-row",
                ),
                hx_post="/get-outfit", hx_target="#results", hx_swap="outerHTML", hx_indicator="closest form",
                cls="search-form",
            ),
            Div(id="results"),
            cls="container",
        ),
        data_theme="light",
    )

@app.get("/login")
def login_page(session):
    if "access_token" in session: return RedirectResponse("/")
    return Title("Login - Fitted"), Body(
        nav_bar(session),
        Div(
            H2("Login"),
            Form(
                Input(type="email", name="username", placeholder="Email", required=True),
                Input(type="password", name="password", placeholder="Password", required=True),
                Button("Login", type="submit"),
                hx_post="/login", hx_target="body",
                cls="auth-form"
            ),
            Div("Don't have an account? ", A("Register here", href="/register"), cls="auth-link"),
            cls="container"
        )
    )

@app.post("/login")
async def login(username: str, password: str, session):
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{API_BASE_URL}/auth/login",
                data={"username": username, "password": password},
            )
            if resp.status_code == 200:
                data = resp.json()
                session["access_token"] = data["access_token"]
                logger.info("User logged in via frontend.")
                return RedirectResponse("/", status_code=303)
            logger.warning(
                "Frontend login failed: backend returned status=%d", resp.status_code
            )
            return error_message("Invalid email or password")
        except Exception:
            logger.error("Frontend login request to backend failed.", exc_info=True)
            return error_message("Login failed: could not reach the server")

@app.get("/register")
def register_page(session):
    if "access_token" in session: return RedirectResponse("/")
    return Title("Register - Fitted"), Body(
        nav_bar(session),
        Div(
            H2("Create Account"),
            Form(
                Input(type="text", name="full_name", placeholder="Full Name", required=True),
                Input(type="email", name="email", placeholder="Email", required=True),
                Input(type="password", name="password", placeholder="Password", required=True),
                Button("Register", type="submit"),
                hx_post="/register", hx_target="body",
                cls="auth-form"
            ),
            Div("Already have an account? ", A("Login here", href="/login"), cls="auth-link"),
            cls="container"
        )
    )

@app.post("/register")
async def register(full_name: str, email: str, password: str, session):
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{API_BASE_URL}/auth/register",
                json={"full_name": full_name, "email": email, "password": password},
            )
            if resp.status_code == 200:
                logger.info("New user registered via frontend.")
                return RedirectResponse("/login", status_code=303)
            err = resp.json().get("detail", "Registration failed")
            logger.warning(
                "Frontend registration failed: backend returned status=%d detail=%s",
                resp.status_code,
                err,
            )
            return error_message(err)
        except Exception:
            logger.error(
                "Frontend registration request to backend failed.", exc_info=True
            )
            return error_message("Registration failed: could not reach the server")

@app.get("/logout")
def logout(session):
    session.pop("access_token", None)
    return RedirectResponse("/login")

@app.post("/get-outfit")
async def get_outfit(location: str, session):
    if not location or not location.strip():
        return error_message("Please enter a location.")
    location = location.strip()
    logger.info("Outfit request from frontend for location=%s", location)
    headers = {}
    if "access_token" in session:
        headers["Authorization"] = f"Bearer {session['access_token']}"

    try:
        async with httpx.AsyncClient() as client:
            url = f"{API_BASE_URL}/suggest-outfit"
            response = await client.post(
                url, params={"location": location}, headers=headers, timeout=30.0
            )
            if response.status_code == 404:
                response = await client.post(
                    f"{url}/",
                    params={"location": location},
                    headers=headers,
                    timeout=30.0,
                )
            if response.status_code != 200:
                err = response.json().get("detail", "Unknown error")
                logger.error(
                    "Backend returned error for outfit request: location=%s status=%d detail=%s",
                    location,
                    response.status_code,
                    err,
                )
                return error_message(f"Error: {err}")

            data = response.json()
            weather_data = data.get("weather", {})
            location_info = weather_data.get("location", {})
            weather = weather_data.get("current", {})
            forecast = weather_data.get("forecast")[0]
            outfit = data.get("outfit_suggestion", {})

            parts = [
                location_info.get("name"),
                location_info.get("region"),
                location_info.get("country"),
            ]
            display_location = ", ".join(p for p in parts if p) or location.title()
            return weather_results(display_location, weather, forecast, outfit)
    except Exception:
        logger.error(
            "Connection error fetching outfit for location=%s.", location, exc_info=True
        )
        return error_message("Connection error: could not reach the server")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5001)
