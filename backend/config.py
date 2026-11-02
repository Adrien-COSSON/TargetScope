# backend/config.py

import logging
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent.parent / ".env",
        extra="ignore",
    )

    anthropic_api_key: str | None = None  # required only by synthesis.py
    # Model writing the synthesis (Claude API id). Sonnet: good writing at ~2 cents
    # per assessment; change it here or with ANTHROPIC_MODEL in .env
    anthropic_model: str = "claude-sonnet-5-5"
    pubmed_api_key: str | None = None
    contact_email: str | None = Field(
        default=None, validation_alias="TARGETSCOPE_CONTACT_EMAIL"
    )
    log_level: str = "INFO"
    env: str = "development"


settings = Settings()

if not settings.contact_email:
    logger.warning(
        "TARGETSCOPE_CONTACT_EMAIL is not set; API calls will be sent without a contact email."
    )