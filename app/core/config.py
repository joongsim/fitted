"""Configuration management using AWS SSM Parameter Store."""
import os
import boto3
from functools import lru_cache

# For local development, try to load .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class Config:
    """Configuration manager that fetches secrets from SSM Parameter Store."""
    
    def __init__(self):
        self._ssm_client = None
        self._use_ssm = os.environ.get("USE_SSM", "true").lower() != "false"
        
    @property
    def ssm_client(self):
        """Lazy-load SSM client."""
        if self._ssm_client is None:
            region = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION", "us-west-1")
            self._ssm_client = boto3.client("ssm", region_name=region)
        return self._ssm_client
    
    @lru_cache(maxsize=32)
    def get_parameter(self, parameter_name: str, default: str = None) -> str:
        """
        Get a parameter value from SSM Parameter Store or environment variables.
        
        Args:
            parameter_name: The SSM parameter name (e.g., '/fitted/openrouter-api-key')
            default: Default value if parameter not found
            
        Returns:
            The parameter value
        """
        # In Lambda, use SSM Parameter Store
        if self._use_ssm:
            try:
                response = self.ssm_client.get_parameter(
                    Name=parameter_name,
                    WithDecryption=True
                )
                return response['Parameter']['Value']
            except Exception as e:
                print(f"Error fetching parameter {parameter_name} from SSM: {e}")
                if default is not None:
                    return default
                raise
        
        # For local development, fall back to environment variables
        # Convert SSM path to env var name: /fitted/openrouter-api-key -> OPENROUTER_API_KEY
        env_var_name = parameter_name.split('/')[-1].upper().replace('-', '_')
        value = os.environ.get(env_var_name, default)
        
        if value is None:
            raise ValueError(f"Parameter {parameter_name} not found in SSM or environment")
        
        return value
    
    @property
    def openrouter_api_key(self) -> str:
        """Get OpenRouter API key."""
        return self.get_parameter('/fitted/openrouter-api-key')
    
    @property
    def weather_api_key(self) -> str:
        """Get Weather API key."""
        return self.get_parameter('/fitted/weather-api-key')
    
    @property
    def database_url(self) -> str:
        """Get PostgreSQL connection URL."""
        return self.get_parameter('/fitted/database-url')

    @property
    def weather_bucket_name(self) -> str:
        """Get Weather Data S3 bucket name from environment (injected by SAM template)."""
        value = os.environ.get("WEATHER_BUCKET_NAME")
        print(f"WEATHER_BUCKET_NAME: {value}")
        if not value:
            raise ValueError("WEATHER_BUCKET_NAME environment variable is not set")
        return value

    @property
    def jwt_secret_key(self) -> str:
        """Get JWT secret key."""
        return self.get_parameter('/fitted/jwt-secret-key', default='dev-secret-key-change-me-in-prod')

    @property
    def jwt_algorithm(self) -> str:
        """Get JWT algorithm."""
        return self.get_parameter('/fitted/jwt-algorithm', default='HS256')

    @property
    def access_token_expire_minutes(self) -> int:
        """Get access token expiration in minutes."""
        return int(self.get_parameter('/fitted/access-token-expire-minutes', default='1440')) # 24 hours


# Global config instance
config = Config()