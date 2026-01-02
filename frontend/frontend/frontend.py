import reflex as rx
import httpx
import json
from typing import Optional, Dict, Any

# API Configuration
import os
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
# API_BASE_URL = "https://e2d6c3y53g.execute-api.us-west-1.amazonaws.com"

class State(rx.State):
    """The app state."""
    location: str = ""
    is_loading: bool = False
    error_message: str = ""
    
    # Flattened weather data for easier UI binding
    temp_c: str = ""
    condition: str = ""
    humidity: str = ""
    wind_kph: str = ""
    feelslike_c: str = ""
    uv: str = ""
    
    # Track the confirmed location for the display
    display_location: str = ""
    
    # Outfit fields
    outfit_top: str = ""
    outfit_bottom: str = ""
    outfit_outerwear: str = ""
    outfit_accessories: str = ""
    
    has_results: bool = False

    async def get_outfit(self, form_data: Dict[str, Any]):
        """Fetch weather and outfit suggestion from the API."""
        self.location = form_data.get("location", self.location)
        
        if not self.location:
            self.error_message = "Please enter a location."
            return

        self.is_loading = True
        self.error_message = ""
        self.has_results = False

        try:
            print(f"Fetching outfit for location: {self.location}")
            async with httpx.AsyncClient() as client:
                # Call the suggest-outfit endpoint
                # Note: template.yaml shows /suggest-outfit/ with a trailing slash
                url = f"{API_BASE_URL}/suggest-outfit"
                print(f"Calling API: {url}")
                response = await client.post(
                    url,
                    params={"location": self.location},
                    timeout=30.0
                )
                
                print(f"API Response Status: {response.status_code}")
                if response.status_code == 404:
                    # Try with trailing slash if 404
                    print("404 received, trying with trailing slash...")
                    url = f"{API_BASE_URL}/suggest-outfit/"
                    response = await client.post(
                        url,
                        params={"location": self.location},
                        timeout=30.0
                    )
                    print(f"API Response Status (with slash): {response.status_code}")

                if response.status_code != 200:
                    try:
                        error_detail = response.json().get('detail', 'Unknown error')
                    except:
                        error_detail = response.text
                    self.error_message = f"Error: {error_detail}"
                    print(f"API Error Detail: {error_detail}")
                    return

                data = response.json()
                print("API Data received successfully")
                weather = data.get("weather", {}).get("current", {})
                
                # Update flattened state variables
                self.temp_c = str(weather.get("temp_c", ""))
                self.condition = str(weather.get("condition", ""))
                self.humidity = str(weather.get("humidity", ""))
                self.wind_kph = str(weather.get("wind_kph", ""))
                self.feelslike_c = str(weather.get("feelslike_c", ""))
                self.uv = str(weather.get("uv", ""))
                
                # Update display location only on successful fetch
                self.display_location = self.location
                
                outfit = data.get("outfit_suggestion", {})
                if isinstance(outfit, dict):
                    self.outfit_top = outfit.get("top", "None")
                    self.outfit_bottom = outfit.get("bottom", "None")
                    self.outfit_outerwear = outfit.get("outerwear", "None")
                    self.outfit_accessories = outfit.get("accessories", "None")
                else:
                    # Fallback if it's still a string for some reason
                    self.outfit_top = str(outfit)
                    self.outfit_bottom = ""
                    self.outfit_outerwear = ""
                    self.outfit_accessories = ""
                
                self.has_results = True
                
        except Exception as e:
            self.error_message = f"Connection error: {str(e)}"
        finally:
            self.is_loading = False

def metric_card(label: str, value: rx.Var, icon: str) -> rx.Component:
    """A small card for weather metrics."""
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.icon(tag=icon, size=16),
                rx.text(label, size="1", color_scheme="gray"),
                spacing="2",
                align="center",
            ),
            rx.text(value, size="4", weight="bold"),
            spacing="1",
            align="start",
        ),
        padding="3",
        variant="surface",
    )

def outfit_item(label: str, value: rx.Var) -> rx.Component:
    """A themed container for an outfit item."""
    return rx.box(
        rx.vstack(
            rx.text(label, size="1", color_scheme="gray", weight="bold", text_transform="uppercase"),
            rx.text(value, size="3"),
            spacing="1",
            align_items="start",
        ),
        padding="3",
        background="gray.300",
        border_radius="md",
        width="100%",
    )

def index() -> rx.Component:
    return rx.center(
        rx.vstack(
            # Header
            rx.heading("Fitted", size="8", margin_bottom="4"),
            rx.text("Your AI Weather Stylist", color_scheme="gray", margin_bottom="8"),

            # Search Input
            rx.form(
                rx.hstack(
                    rx.input(
                        placeholder="Enter city (e.g., San Francisco)",
                        name="location",
                        on_change=State.set_location,
                        width="100%",
                    ),
                    rx.button(
                        rx.cond(State.is_loading, rx.spinner(size="1"), rx.text("Get Outfit")),
                        type="submit",
                        disabled=State.is_loading,
                    ),
                    width="100%",
                    spacing="2",
                ),
                on_submit=State.get_outfit,
                width="100%",
            ),

            # Error Message
            rx.cond(
                State.error_message != "",
                rx.callout(
                    State.error_message,
                    icon="triangle_alert",
                    color_scheme="red",
                    role="alert",
                    margin_top="4",
                    width="100%",
                ),
            ),

            # Results
            rx.cond(
                State.has_results,
                rx.vstack(
                    # Main Weather Card
                    rx.card(
                        rx.vstack(
                            rx.hstack(
                                rx.vstack(
                                    rx.heading(State.temp_c + "°C", size="9"),
                                    rx.text(State.condition, size="4", color_scheme="gray"),
                                    align="start",
                                ),
                                rx.spacer(),
                                rx.vstack(
                                    rx.text(State.display_location, weight="bold", size="5"),
                                    rx.text("Current Weather", size="2", color_scheme="gray"),
                                    align="end",
                                ),
                                width="100%",
                                align="center",
                            ),
                        ),
                        width="100%",
                        padding="6",
                        margin_top="6",
                    ),

                    # Metrics Grid
                    rx.grid(
                        metric_card("Feels Like", State.feelslike_c + "°C", "thermometer"),
                        metric_card("Humidity", State.humidity + "%", "droplets"),
                        metric_card("Wind", State.wind_kph + " kph", "wind"),
                        metric_card("UV Index", State.uv, "sun"),
                        columns="2",
                        spacing="4",
                        width="100%",
                        margin_top="4",
                    ),

                    # Outfit Suggestion
                    rx.vstack(
                        rx.heading("Stylist Recommendation", size="5", margin_top="6", align_self="start"),
                        rx.vstack(
                            outfit_item("Top", State.outfit_top),
                            outfit_item("Bottom", State.outfit_bottom),
                            rx.cond(
                                State.outfit_outerwear != "None",
                                outfit_item("Outerwear", State.outfit_outerwear),
                            ),
                            rx.cond(
                                State.outfit_accessories != "None",
                                outfit_item("Accessories", State.outfit_accessories),
                            ),
                            width="100%",
                            spacing="3",
                        ),
                        width="100%",
                        spacing="3",
                    ),
                    width="100%",
                ),
            ),

            width="100%",
            max_width="450px",
            padding="4",
            align="center",
        ),
        min_height="100vh",
        background_color=rx.color("gray", 1),
    )

app = rx.App(
    theme=rx.theme(
        appearance="dark",
        accent_color="indigo",
    )
)
app.add_page(index, title="Fitted - AI Weather Stylist")
