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
        self._use_ssm = os.environ.get("AWS_EXECUTION_ENV") is not None  # Running in Lambda
        
    @property
    def ssm_client(self):
        """Lazy-load SSM client."""
        if self._ssm_client is None:
            self._ssm_client = boto3.client('ssm')
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
    def weather_bucket_name(self) -> str:
        """Get Weather Data S3 bucket name."""
        return os.environ.get('WEATHER_BUCKET_NAME')


# Global config instance
config = Config()