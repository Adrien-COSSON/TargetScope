# backend/utils/logger.py

import logging
import logging.handlers
from pathlib import Path

from backend.config import settings

# Project root: backend/utils/logger.py -> parents[2]
DEFAULT_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"


def setup_logging(log_dir: Path = DEFAULT_LOG_DIR) -> None:
    """Logging configuration for the whole project.

    Call once at application startup (FastAPI lifespan).
    Safe to call several times: handlers are only added once.
    All modules then use logging.getLogger(__name__) directly.
    """
    root_logger = logging.getLogger()

    # Idempotence guard: already configured, do nothing
    if root_logger.handlers:
        return

    log_dir.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Detailed format for files, concise for the console
    file_formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # Handler 1: console
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)  # root filters, handlers pass everything
    console_handler.setFormatter(console_formatter)

    # Handler 2: file with rotation (max 5 MB x 3 files)
    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_dir / "app.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)

    # Root logger: inherited by all modules
    root_logger.setLevel(level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # httpx logs full URLs at INFO, including API keys in query params
    logging.getLogger("httpx").setLevel(logging.WARNING)