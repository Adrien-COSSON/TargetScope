# backend\config.py

# Import libraries
from pydantic_settings import BaseSettings
from typing import Optional
from pathlib import Path

class Settings(BaseSettings):
    anthropic_api_key: str
    pubmed_api_key: Optional[str] = None
    log_level: Optional[str] = "INFO" 
    
    class Config:
        env_file = Path(__file__).parent.parent / ".env"

settings = Settings()