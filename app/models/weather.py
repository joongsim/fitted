from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List


# 1. Define the nested 'WeatherCondition' model first
class WeatherCondition(BaseModel):
    text: str = Field(..., description="Textual description of the weather condition")
    icon: Optional[str] = Field(
        None, description="URL to an icon representing the weather condition"
    )
    code: int = Field(..., description="Code representing the weather condition")


# 2. Define the main 'CurrentWeather' model that uses 'WeatherCondition'
class CurrentWeather(BaseModel):
    last_updated_epoch: int
    last_updated: str
    temp_c: float = Field(..., ge=-100, le=60, description="Temperature in Celsius")
    temp_f: float
    is_day: int
    condition: WeatherCondition
    wind_mph: float
    wind_kph: float
    humidity: int = Field(..., ge=0, le=100)
    cloud: int
    feelslike_c: float
    feelslike_f: float
    uv: float


# 3. Define the 'Location' model
class Location(BaseModel):
    name: str
    region: str
    country: str
    lat: float
    lon: float
    tz_id: str
    localtime_epoch: int
    localtime: str


# 4. Define the main WeatherResponse model that uses 'Location' and 'CurrentWeather'
class WeatherResponse(BaseModel):
    location: Location
    current: CurrentWeather


class ForecastDay(BaseModel):
    """Single day forecast"""

    date: str
    date_epoch: int
    day: dict  # Contains maxtemp_c, mintemp_c, avgtemp_c, condition, etc.
    astro: dict  # Sunrise, sunset, moon phase
    hour: Optional[List[dict]] = None  # Hourly forecast (if needed)


class Forecast(BaseModel):
    """Multi-day forecast"""

    forecastday: List[ForecastDay]


class WeatherWithForecast(BaseModel):
    """Weather response with forecast"""

    location: Location
    current: CurrentWeather
    forecast: Optional[Forecast] = None
