from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    # LiteLLM Configuration
    litellm_url: str
    litellm_api_key: str = "sk-empty"

    # Proxy Configuration
    proxy_api_key: str
    host: str = "0.0.0.0"
    port: int = 8000
    
    # Logging
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
