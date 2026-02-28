"""Configuration management using AWS SSM Parameter Store."""

import logging
import os
from functools import lru_cache
from typing import Optional

import boto3

logger = logging.getLogger(__name__)

# For local development, try to load .env file
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


class Config:
    """Configuration manager that fetches secrets from SSM Parameter Store."""

    def __init__(self) -> None:
        self._ssm_client = None
        self._use_ssm = os.environ.get("USE_SSM", "false").lower() != "false"

    @property
    def ssm_client(self):
        """Lazy-load SSM client."""
        if self._ssm_client is None:
            region = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get(
                "AWS_REGION", "us-west-1"
            )
            self._ssm_client = boto3.client("ssm", region_name=region)
        return self._ssm_client

    @lru_cache(maxsize=32)
    def get_parameter(self, parameter_name: str, default: str = None) -> str:
        """
        Get a parameter value from SSM Parameter Store or environment variables.

        In Lambda/SSM mode the value is fetched from Parameter Store with
        decryption.  Locally it falls back to the equivalent environment variable
        derived by converting the SSM path suffix to UPPER_SNAKE_CASE.

        Args:
            parameter_name: The SSM parameter name (e.g., '/fitted/openrouter-api-key').
            default: Default value if parameter not found.

        Returns:
            The parameter value.

        Raises:
            ValueError: If the parameter is not found and no default is given.
        """
        if self._use_ssm:
            try:
                response = self.ssm_client.get_parameter(
                    Name=parameter_name,
                    WithDecryption=True,
                )
                logger.debug("Fetched SSM parameter: %s", parameter_name)
                return response["Parameter"]["Value"]
            except Exception:
                logger.error(
                    "Failed to fetch SSM parameter: %s", parameter_name, exc_info=True
                )
                if default is not None:
                    return default
                raise

        # Local fallback: convert SSM path to env var name
        env_var_name = parameter_name.split("/")[-1].upper().replace("-", "_")
        value = os.environ.get(env_var_name, default)

        if value is None:
            raise ValueError(
                f"Parameter {parameter_name} not found in SSM or environment"
            )

        return value

    @property
    def openrouter_api_key(self) -> str:
        """Get OpenRouter API key."""
        return self.get_parameter("/fitted/openrouter-api-key")

    @property
    def weather_api_key(self) -> str:
        """Get Weather API key."""
        return self.get_parameter("/fitted/weather-api-key")

    @property
    def database_url(self) -> str:
        """Get PostgreSQL connection URL."""
        return self.get_parameter("/fitted/database-url")

    @property
    def weather_bucket_name(self) -> Optional[str]:
        """Get Weather Data S3 bucket name from environment (set in systemd service)."""
        value = os.environ.get("WEATHER_BUCKET_NAME")
        logger.debug("WEATHER_BUCKET_NAME: %s", value)
        return value or None

    @property
    def rapidapi_key(self) -> str:
        """Get RapidAPI key for Poshmark API access."""
        return self.get_parameter("/fitted/rapidapi-key")

    @property
    def jwt_secret_key(self) -> str:
        """Get JWT secret key."""
        return self.get_parameter(
            "/fitted/jwt-secret-key", default="dev-secret-key-change-me-in-prod"
        )

    @property
    def jwt_algorithm(self) -> str:
        """Get JWT algorithm."""
        return self.get_parameter("/fitted/jwt-algorithm", default="HS256")

    @property
    def access_token_expire_minutes(self) -> int:
        """Get access token expiration in minutes."""
        return int(
            self.get_parameter("/fitted/access-token-expire-minutes", default="1440")
        )  # 24 hours


# Global config instance
config = Config()
