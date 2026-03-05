"""FastHTML frontend for Fitted - AI Weather Stylist"""

# ruff: noqa: F405
import logging

import boto3
import httpx
from fasthtml.common import *  # noqa: F403, F405 star import ok for fasthtml
import os

logger = logging.getLogger(__name__)

# API Configuration
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")


def get_ssm_parameter(name: str, default: str = None) -> str:
    """Fetch parameter from SSM Parameter Store or return default."""
    if os.environ.get("USE_SSM", "true").lower() != "false":
        try:
            ssm = boto3.client(
                "ssm", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-west-1")
            )
            response = ssm.get_parameter(Name=name, WithDecryption=True)
            logger.debug("Fetched SSM parameter: %s", name)
            return response["Parameter"]["Value"]
        except Exception:
            logger.error("Failed to fetch SSM parameter: %s", name, exc_info=True)
            return default
    return default


# Custom CSS for light theme styling (green palette)
custom_css = Style(
    """
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

    /* Wardrobe Styles */
    .wardrobe-upload-form {
        display: flex;
        flex-direction: column;
        gap: 0.75rem;
        margin-bottom: 1.5rem;
        padding: 1rem;
        border: 2px solid #000;
        background-color: #fff;
    }
    .wardrobe-upload-form input,
    .wardrobe-upload-form select {
        border: 2px solid #000;
        background-color: #fffbeb;
    }
    .wardrobe-upload-form button {
        background-color: #95FB62;
        border: 2px solid #000;
        color: #000;
        font-weight: bold;
        cursor: pointer;
    }
    .wardrobe-grid {
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 1rem;
        margin-bottom: 2rem;
    }
    .wardrobe-card {
        border: 2px solid #000;
        background-color: #fff;
        padding: 0.75rem;
        position: relative;
    }
    .wardrobe-card img {
        width: 100%;
        aspect-ratio: 1;
        object-fit: cover;
        display: block;
        background-color: #f1f5f9;
        border: 1px solid #e2e8f0;
        margin-bottom: 0.5rem;
    }
    .wardrobe-card-placeholder {
        width: 100%;
        aspect-ratio: 1;
        background-color: #f1f5f9;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 2rem;
        margin-bottom: 0.5rem;
        border: 1px solid #e2e8f0;
    }
    .wardrobe-card-name {
        font-weight: bold;
        font-size: 0.875rem;
        margin-bottom: 0.25rem;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    .wardrobe-card-category {
        font-size: 0.75rem;
        color: #64748b;
        text-transform: uppercase;
        font-weight: bold;
        margin-bottom: 0.5rem;
    }
    .wardrobe-card-delete {
        background: none;
        border: 1px solid #dc2626;
        color: #dc2626;
        font-size: 0.75rem;
        padding: 0.25rem 0.5rem;
        cursor: pointer;
        width: 100%;
    }
    .wardrobe-card-delete:hover { background-color: #fef2f2; }
    .wardrobe-empty {
        text-align: center;
        color: #64748b;
        padding: 2rem;
        border: 2px dashed #000;
        grid-column: 1 / -1;
    }

    /* Product Card Styles */
    .products-section {
        margin-top: 1.5rem;
        padding-top: 1rem;
        border-top: 2px solid #000;
    }
    .products-section h2 {
        font-size: 1rem;
        font-weight: bold;
        margin-bottom: 1rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .products-scroll {
        display: flex;
        gap: 0.75rem;
        overflow-x: auto;
        padding-bottom: 0.5rem;
        scrollbar-width: thin;
    }
    .product-card {
        flex: 0 0 140px;
        border: 2px solid #000;
        background-color: #fff;
        padding: 0.5rem;
    }
    .product-card img {
        width: 100%;
        aspect-ratio: 1;
        object-fit: cover;
        display: block;
        background-color: #f1f5f9;
        margin-bottom: 0.5rem;
    }
    .product-card-placeholder {
        width: 100%;
        aspect-ratio: 1;
        background-color: #f1f5f9;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1.5rem;
        margin-bottom: 0.5rem;
    }
    .product-card-title {
        font-size: 0.75rem;
        font-weight: bold;
        overflow: hidden;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        margin-bottom: 0.25rem;
    }
    .product-card-price {
        font-size: 0.875rem;
        color: #16a34a;
        font-weight: bold;
        margin-bottom: 0.5rem;
    }
    .product-card-actions {
        display: flex;
        gap: 0.25rem;
    }
    .product-card-link {
        flex: 1;
        text-align: center;
        background-color: #95FB62;
        border: 1px solid #000;
        color: #000;
        font-size: 0.7rem;
        font-weight: bold;
        padding: 0.25rem;
        text-decoration: none;
        display: block;
    }
    .product-card-action-btn {
        background: none;
        border: 1px solid #000;
        padding: 0.25rem 0.4rem;
        cursor: pointer;
        font-size: 0.8rem;
    }
    .product-card-action-btn:hover { background-color: #f1f5f9; }

    /* Product Grid & Shop Section */
    .product-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
        gap: 0.75rem;
        margin-top: 1rem;
    }
    .shop-section {
        margin-top: 2rem;
        padding-top: 1rem;
        border-top: 2px solid #000;
    }
    .shop-section h3 {
        font-size: 1rem;
        font-weight: bold;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 0.75rem;
    }
    .shop-btn {
        background-color: #95FB62;
        border: 2px solid #000;
        color: #000;
        padding: 0.5rem 1.2rem;
        cursor: pointer;
        font-size: 0.875rem;
        font-weight: bold;
    }
    .shop-btn:hover { background-color: #7de84a; }
    .rec-meta {
        font-size: 0.8rem;
        color: #64748b;
        margin-bottom: 0.75rem;
    }

    /* Preferences Styles */
    .prefs-form {
        display: flex;
        flex-direction: column;
        gap: 1rem;
        margin-top: 1rem;
    }
    .prefs-form label {
        font-size: 0.875rem;
        font-weight: bold;
        display: block;
        margin-bottom: 0.25rem;
    }
    .prefs-form input {
        border: 2px solid #000;
        width: 100%;
    }
    .prefs-form button {
        background-color: #95FB62;
        border: 2px solid #000;
        color: #000;
        font-weight: bold;
        cursor: pointer;
    }
    .prefs-hint {
        font-size: 0.75rem;
        color: #64748b;
        margin-top: 0.25rem;
    }
    .prefs-success {
        background-color: #f0fdf4;
        border: 1px solid #86efac;
        color: #15803d;
        padding: 0.75rem 1rem;
        margin-top: 1rem;
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
"""
)

# Get session secret from environment or generate a random one for local use
SESSION_SECRET = get_ssm_parameter(
    "/fitted/session-secret",
    os.environ.get("SESSION_SECRET", "local-dev-secret-key-change-in-prod"),
)

AppClass = (
    FastHTMLWithLiveReload
    if os.environ.get("DEV", "false").lower() == "true"
    else FastHTML
)
app = AppClass(
    secret_key=SESSION_SECRET,
    hdrs=(
        Link(
            rel="stylesheet",
            href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css",
        ),
        Script(src="https://unpkg.com/htmx.org@2.0.4"),
        custom_css,
    ),
)


def nav_bar(session):
    """Render the navigation bar based on auth state."""
    is_logged_in = "access_token" in session
    links = [A("fitted", href="/")]
    if is_logged_in:
        links.extend(
            [
                A("Wardrobe", href="/wardrobe"),
                A("Prefs", href="/preferences"),
                A("Recs", href="/recommendations"),
                A("Logout", href="/logout"),
            ]
        )
    else:
        links.extend([A("Login", href="/login"), A("Register", href="/register")])
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


def weather_results(
    location: str,
    weather: dict,
    forecast: dict,
    outfit: dict,
    show_shop_btn: bool = False,
) -> Div:
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

    shop_section = Div(id="rec-results")
    if show_shop_btn:
        shop_section = Div(
            H3("Shop These Looks"),
            Form(
                Input(type="hidden", name="location", value=location),
                Button(
                    Span("Get Recommendations", cls="loading-text"),
                    Span(cls="loading loading-spinner"),
                    type="submit",
                    cls="shop-btn",
                ),
                hx_post="/get-recommendations",
                hx_target="#rec-results",
                hx_swap="outerHTML",
                hx_indicator="closest form",
            ),
            Div(id="rec-results"),
            cls="shop-section",
        )

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
        shop_section,
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
                    Input(
                        type="text",
                        name="location",
                        placeholder="Enter city (e.g., San Francisco)",
                        required=True,
                    ),
                    cls="search-form-input-row",
                ),
                Div(
                    Button(
                        Span("Go", cls="loading-text"),
                        Span(cls="loading loading-spinner"),
                        type="submit",
                    ),
                    cls="search-form-btn-row",
                ),
                hx_post="/get-outfit",
                hx_target="#results",
                hx_swap="outerHTML",
                hx_indicator="closest form",
                cls="search-form",
            ),
            Div(id="results"),
            cls="container",
        ),
        data_theme="light",
    )


@app.get("/login")
def login_page(session):
    if "access_token" in session:
        return RedirectResponse("/")
    return Title("Login - Fitted"), Body(
        nav_bar(session),
        Div(
            H2("Login"),
            Form(
                Input(
                    type="email", name="username", placeholder="Email", required=True
                ),
                Input(
                    type="password",
                    name="password",
                    placeholder="Password",
                    required=True,
                ),
                Button("Login", type="submit"),
                hx_post="/login",
                hx_target="body",
                cls="auth-form",
            ),
            Div(
                "Don't have an account? ",
                A("Register here", href="/register"),
                cls="auth-link",
            ),
            cls="container",
        ),
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
    if "access_token" in session:
        return RedirectResponse("/")
    return Title("Register - Fitted"), Body(
        nav_bar(session),
        Div(
            H2("Create Account"),
            Form(
                Input(
                    type="text",
                    name="full_name",
                    placeholder="Full Name",
                    required=True,
                ),
                Input(type="email", name="email", placeholder="Email", required=True),
                Input(
                    type="password",
                    name="password",
                    placeholder="Password",
                    required=True,
                ),
                Button("Register", type="submit"),
                hx_post="/register",
                hx_target="body",
                cls="auth-form",
            ),
            Div(
                "Already have an account? ",
                A("Login here", href="/login"),
                cls="auth-link",
            ),
            cls="container",
        ),
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
            return weather_results(
                display_location,
                weather,
                forecast,
                outfit,
                show_shop_btn="access_token" in session,
            )
    except Exception:
        logger.error(
            "Connection error fetching outfit for location=%s.", location, exc_info=True
        )
        return error_message("Connection error: could not reach the server")


def wardrobe_card(item: dict) -> Div:
    """A single wardrobe item card with thumbnail and delete button."""
    item_id = item["item_id"]
    image_url = item.get("image_url")
    thumbnail = (
        Img(src=image_url, alt=item["name"])
        if image_url
        else Div("👔", cls="wardrobe-card-placeholder")
    )
    return Div(
        thumbnail,
        Div(item["name"], cls="wardrobe-card-name"),
        Div(item.get("category") or "—", cls="wardrobe-card-category"),
        Button(
            "Delete",
            hx_delete=f"/wardrobe/{item_id}",
            hx_confirm="Remove this item from your wardrobe?",
            hx_target="closest .wardrobe-card",
            hx_swap="outerHTML swap:0.2s",
            cls="wardrobe-card-delete",
        ),
        cls="wardrobe-card",
        id=f"wardrobe-card-{item_id}",
    )


def product_card(item: dict) -> Div:
    """A catalog product card with save/dismiss interaction buttons."""
    item_id = item["item_id"]
    image_url = item.get("image_url")
    thumbnail = (
        Img(src=image_url, alt=item["title"])
        if image_url
        else Div("🛍️", cls="product-card-placeholder")
    )
    # Interaction payloads — sent fire-and-forget (hx_swap="none")
    save_vals = f'{{"item_id":"{item_id}","interaction_type":"save"}}'
    dismiss_vals = f'{{"item_id":"{item_id}","interaction_type":"dismiss"}}'
    return Div(
        thumbnail,
        Div(item["title"], cls="product-card-title"),
        Div(f"${item['price']:.0f}", cls="product-card-price"),
        Div(
            A(
                "View",
                href=item["product_url"],
                target="_blank",
                cls="product-card-link",
            ),
            Button(
                "♥",
                hx_post="/log-interaction",
                hx_vals=save_vals,
                hx_swap="none",
                cls="product-card-action-btn",
                title="Save",
            ),
            Button(
                "✕",
                hx_post="/log-interaction",
                hx_vals=dismiss_vals,
                hx_swap="none",
                cls="product-card-action-btn",
                title="Dismiss",
            ),
            cls="product-card-actions",
        ),
        cls="product-card",
    )


# --- Wardrobe Routes ---


@app.get("/wardrobe")
async def wardrobe_page(session):
    """Wardrobe gallery: upload form + grid of existing items."""
    if "access_token" not in session:
        return RedirectResponse("/login")

    token = session["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    items = []
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{API_BASE_URL}/wardrobe", headers=headers, timeout=15.0
            )
            if resp.status_code == 200:
                items = resp.json().get("items", [])
    except Exception:
        logger.error("Failed to fetch wardrobe items from backend.", exc_info=True)

    grid_children = [wardrobe_card(item) for item in items]
    if not grid_children:
        grid_children = [
            Div("No items yet — upload your first piece below.", cls="wardrobe-empty")
        ]

    return Title("Wardrobe - Fitted"), Body(
        nav_bar(session),
        Div(
            H2("My Wardrobe"),
            Div(*grid_children, id="wardrobe-grid", cls="wardrobe-grid"),
            H2("Add Item"),
            Form(
                Input(
                    type="text",
                    name="name",
                    placeholder="Item name (e.g. Navy Blazer)",
                    required=True,
                ),
                Select(
                    Option("— Category (optional) —", value=""),
                    Option("Tops", value="tops"),
                    Option("Bottoms", value="bottoms"),
                    Option("Outerwear", value="outerwear"),
                    Option("Shoes", value="shoes"),
                    Option("Accessories", value="accessories"),
                    name="category",
                ),
                Input(type="file", name="image", accept="image/*"),
                Button(
                    Span("Upload", cls="loading-text"),
                    Span(cls="loading loading-spinner"),
                    type="submit",
                ),
                hx_post="/wardrobe/upload",
                hx_target="#wardrobe-grid",
                hx_swap="afterbegin",
                hx_indicator="closest form",
                hx_encoding="multipart/form-data",
                cls="wardrobe-upload-form",
            ),
            cls="container",
        ),
        data_theme="light",
    )


@app.post("/wardrobe/upload")
async def wardrobe_upload(
    session, name: str, category: str = "", image: UploadFile = None
):
    """
    HTMX fragment: relay multipart upload to backend, return a single wardrobe card.
    Targets #wardrobe-grid with swap=afterbegin — prepends the new card.
    """
    if "access_token" not in session:
        return error_message("Not logged in")

    token = session["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient() as client:
            files = None
            if image and image.filename:
                file_bytes = await image.read()
                files = {
                    "image": (
                        image.filename,
                        file_bytes,
                        image.content_type or "image/jpeg",
                    )
                }
            resp = await client.post(
                f"{API_BASE_URL}/wardrobe",
                data={"name": name, "category": category or None},
                files=files,
                headers=headers,
                timeout=20.0,
            )
            if resp.status_code == 201:
                item = resp.json()
                logger.info("Wardrobe item created: %s", item.get("item_id"))
                return wardrobe_card(item)
            err = resp.json().get("detail", "Upload failed")
            logger.warning("Backend wardrobe upload failed: %s", err)
            return Div(f"Error: {err}", cls="error-message")
    except Exception:
        logger.error("Wardrobe upload to backend failed.", exc_info=True)
        return Div("Upload failed: could not reach the server", cls="error-message")


@app.delete("/wardrobe/{item_id}")
async def wardrobe_delete(item_id: str, session):
    """HTMX fragment: delete wardrobe item. Returns empty string — HTMX removes the card."""
    if "access_token" not in session:
        return ""
    token = session["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{API_BASE_URL}/wardrobe/{item_id}", headers=headers, timeout=10.0
            )
            if resp.status_code in (204, 404):
                logger.info("Wardrobe item deleted: %s", item_id)
    except Exception:
        logger.error("Wardrobe delete request failed.", exc_info=True)
    return ""  # HTMX replaces the card element with nothing


# --- Interaction Logging (fire-and-forget from product cards) ---


@app.post("/log-interaction")
async def log_interaction(item_id: str, interaction_type: str, session):
    """Relay interaction signal to backend. Returns nothing — hx_swap='none'."""
    if "access_token" not in session:
        return ""
    token = session["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{API_BASE_URL}/interactions",
                json={"item_id": item_id, "interaction_type": interaction_type},
                headers=headers,
                timeout=5.0,
            )
    except Exception:
        logger.debug(
            "Interaction log silently failed: item_id=%s type=%s",
            item_id,
            interaction_type,
        )
    return ""


# --- Recommendations Routes ---


@app.get("/recommendations")
async def recommendations_page(session):
    """Full recommendations page — location form that fires /get-recommendations."""
    if "access_token" not in session:
        return RedirectResponse("/login")

    return Title("Recommendations - Fitted"), Body(
        nav_bar(session),
        Div(
            H2("Shop Recommendations"),
            P(
                "Enter a location to get personalized product picks based on the weather.",
                style="color:#64748b;font-size:0.875rem;margin-bottom:1.5rem;",
            ),
            Form(
                Div(
                    Input(
                        type="text",
                        name="location",
                        placeholder="Enter city (e.g., San Francisco)",
                        required=True,
                    ),
                    cls="search-form-input-row",
                ),
                Div(
                    Button(
                        Span("Get Recommendations", cls="loading-text"),
                        Span(cls="loading loading-spinner"),
                        type="submit",
                    ),
                    cls="search-form-btn-row",
                ),
                hx_post="/get-recommendations",
                hx_target="#rec-results",
                hx_swap="outerHTML",
                hx_indicator="closest form",
                cls="search-form",
            ),
            Div(id="rec-results"),
            cls="container",
        ),
        data_theme="light",
    )


@app.post("/get-recommendations")
async def get_recommendations(location: str, session):
    """HTMX fragment: call POST /recommend-products, return a product card grid."""
    if "access_token" not in session:
        return P("Please log in to get recommendations.", cls="error-message")

    if not location or not location.strip():
        return P("Please enter a location.", cls="error-message")

    location = location.strip()
    token = session["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    logger.info("Recommendations request from frontend for location=%s", location)

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{API_BASE_URL}/recommend-products",
                json={"location": location, "include_explanation": False},
                headers=headers,
                timeout=45.0,
            )
            if resp.status_code != 200:
                err = resp.json().get("detail", "Unknown error")
                logger.error(
                    "Backend returned error for recommendations: location=%s status=%d detail=%s",
                    location,
                    resp.status_code,
                    err,
                )
                return P(f"Error: {err}", cls="error-message", id="rec-results")

            data = resp.json()
            recommendations = data.get("recommendations", [])
            weather = data.get("weather", {})
            temp_c = weather.get("temp_c", 0.0)
            condition = weather.get("condition", "")

    except Exception:
        logger.error(
            "Connection error fetching recommendations for location=%s.",
            location,
            exc_info=True,
        )
        return P(
            "Connection error: could not reach the server.",
            cls="error-message",
            id="rec-results",
        )

    if not recommendations:
        return P(
            "No recommendations found for this location.",
            cls="rec-meta",
            id="rec-results",
        )

    return Div(
        P(
            f"Top picks for {location} ({condition}, {temp_c:.0f}\u00b0C)",
            cls="rec-meta",
        ),
        Div(*[product_card(item) for item in recommendations], cls="product-grid"),
        id="rec-results",
    )


# --- Preferences Routes ---


@app.get("/preferences")
async def preferences_page(session):
    """Style preferences form — populates from current saved preferences."""
    if "access_token" not in session:
        return RedirectResponse("/login")

    token = session["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    style_prefs = {}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{API_BASE_URL}/users/me/preferences", headers=headers, timeout=10.0
            )
            if resp.status_code == 200:
                style_prefs = resp.json().get("style_preferences", {})
    except Exception:
        logger.error("Failed to fetch preferences.", exc_info=True)

    def _join(lst: list) -> str:
        return ", ".join(lst) if lst else ""

    return Title("Preferences - Fitted"), Body(
        nav_bar(session),
        Div(
            H2("Style Preferences"),
            P(
                "These inform your outfit suggestions and product recommendations.",
                style="color:#64748b;font-size:0.875rem;",
            ),
            Form(
                Div(
                    Label("Preferred styles (comma-separated)"),
                    Input(
                        type="text",
                        name="styles",
                        value=_join(style_prefs.get("styles", [])),
                        placeholder="e.g. smart casual, streetwear, ivy prep",
                    ),
                    P(
                        "Guides both outfit generation and catalog search.",
                        cls="prefs-hint",
                    ),
                ),
                Div(
                    Label("Preferred colours"),
                    Input(
                        type="text",
                        name="colors",
                        value=_join(style_prefs.get("colors", [])),
                        placeholder="e.g. navy, white, olive",
                    ),
                ),
                Div(
                    Label("Occasions"),
                    Input(
                        type="text",
                        name="occasions",
                        value=_join(style_prefs.get("occasions", [])),
                        placeholder="e.g. work, weekend, formal",
                    ),
                ),
                Div(
                    Label("Avoid"),
                    Input(
                        type="text",
                        name="avoid",
                        value=_join(style_prefs.get("avoid", [])),
                        placeholder="e.g. loud prints, skinny fit",
                    ),
                ),
                Button("Save Preferences", type="submit"),
                hx_post="/preferences",
                hx_target="#prefs-feedback",
                hx_swap="innerHTML",
                cls="prefs-form",
            ),
            Div(id="prefs-feedback"),
            cls="container",
        ),
        data_theme="light",
    )


@app.post("/preferences")
async def save_preferences(
    session,
    styles: str = "",
    colors: str = "",
    occasions: str = "",
    avoid: str = "",
):
    """HTMX fragment: save preferences and return inline success/error message."""
    if "access_token" not in session:
        return Div("Not logged in", cls="error-message")

    def _parse(s: str) -> list:
        return [x.strip() for x in s.split(",") if x.strip()]

    style_prefs = {
        "styles": _parse(styles),
        "colors": _parse(colors),
        "occasions": _parse(occasions),
        "avoid": _parse(avoid),
    }
    token = session["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.patch(
                f"{API_BASE_URL}/users/me/preferences",
                json={"style_preferences": style_prefs},
                headers=headers,
                timeout=10.0,
            )
            if resp.status_code == 200:
                logger.info("Preferences saved via frontend.")
                return Div("Preferences saved.", cls="prefs-success")
            err = resp.json().get("detail", "Save failed")
            return Div(f"Error: {err}", cls="error-message")
    except Exception:
        logger.error("Preferences save request failed.", exc_info=True)
        return Div("Could not reach the server", cls="error-message")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5001)
