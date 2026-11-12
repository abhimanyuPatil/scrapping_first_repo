"""Shared utilities for web scrapers."""

from .config import RETRIES, DELAY_SECONDS, HEADERS, TIMEOUT_SECONDS
from .http import create_session, fetch_with_retry
from .logging_utils import setup_logger, JsonFormatter
from .storage import save_to_csv_atomic, save_to_json_atomic

__all__ = [
    "RETRIES",
    "DELAY_SECONDS",
    "HEADERS",
    "TIMEOUT_SECONDS",
    "create_session",
    "fetch_with_retry",
    "setup_logger",
    "JsonFormatter",
    "save_to_csv_atomic",
    "save_to_json_atomic",
]
