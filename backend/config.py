# backend\config.py

# Import libraries
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str
    pubmed_api_key: str | None = None
    log_level: str | None = "INFO"
    env: str | None = "development"

    class Config:
        env_file = Path(__file__).parent.parent / ".env"


settings = Settings()
