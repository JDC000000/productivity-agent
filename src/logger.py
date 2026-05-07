"""Logging setup — writes to stdout (for Terminal) and a rotating file.

Why rotating? launchd will eventually restart the bot if it crashes;
logs would grow unbounded without rotation. 5MB × 3 backups = ~15MB cap.
"""
import logging
from logging.handlers import RotatingFileHandler

from .config import LOG_LEVEL, LOG_PATH


def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(LOG_LEVEL)

    # Clear any handlers attached by libraries before we configure our own.
    root.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Stdout — what you see in Terminal while running ./run.sh
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)

    # File — persisted for later inspection / Friday review
    file_handler = RotatingFileHandler(
        LOG_PATH, maxBytes=5_000_000, backupCount=3
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # Quiet down noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)
