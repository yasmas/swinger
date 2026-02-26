"""Configures logging for the paper trading daemon: rotating file + stdout."""

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def setup_logging(log_file: str = "data/live/paper_trader.log",
                  level: str = "INFO",
                  max_days: int = 30):
    """Configure root logger with rotating file handler and stdout.

    Args:
        log_file: Path to the log file (parent dirs created automatically).
        level: Log level string (DEBUG, INFO, WARNING, ERROR).
        max_days: Number of daily log files to retain.
    """
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove any existing handlers to avoid duplicates on re-init
    root.handlers.clear()

    file_handler = TimedRotatingFileHandler(
        log_path, when="midnight", backupCount=max_days, utc=True,
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    stdout_handler = logging.StreamHandler()
    stdout_handler.setFormatter(fmt)
    root.addHandler(stdout_handler)

    # Quiet down noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
