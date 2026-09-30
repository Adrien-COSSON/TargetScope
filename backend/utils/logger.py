# backend\utils\logger.py

# Import libraries
import logging
import logging.handlers
from pathlib import Path

from backend.config import settings


def setup_logging(log_dir: str = "logs") -> None:
    """Logging configuration for the whole project.

    Call once at application startup (main.py or FastAPI lifespan).
    All modules then use logging.getLogger(__name__) directly.
    """
    Path(log_dir).mkdir(exist_ok=True)

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

    # Handler 1 : console
    console_handler = logging.StreamHandler()
    console_handler.setLevel(
        logging.DEBUG
    )  # root filtre, handlers laissent tout passer
    console_handler.setFormatter(console_formatter)

    # Handler 2 : file with rotation (max 5 Mo × 3 files)
    file_handler = logging.handlers.RotatingFileHandler(
        filename=f"{log_dir}/app.log",
        maxBytes=5 * 1024 * 1024,  # 5 Mo
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)

    # Root logger — inherits across all modules
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
