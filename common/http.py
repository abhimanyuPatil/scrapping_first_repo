"""HTTP utilities with exponential backoff and session management."""
from __future__ import annotations

import time
from typing import Optional

import requests

from .config import RETRIES, HEADERS, TIMEOUT_SECONDS


def create_session() -> requests.Session:
    """Create a requests Session with default headers.
    
    Returns a session object that reuses TCP/TLS connections across multiple
    requests, improving performance when scraping multiple pages from the same
    domain.
    
    Returns:
        Configured requests.Session instance
        
    Example:
        session = create_session()
        response = session.get("https://example.com")
    """
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def fetch_with_retry(
    session: requests.Session,
    url: str,
    timeout: int = TIMEOUT_SECONDS,
    retries: int = RETRIES,
    log: Optional[object] = None,
) -> str:
    """
    Fetch a URL with exponential backoff retry.
    
    Implements the retry pattern with exponential backoff, capped at 60 seconds.
    On failure, waits 2s → 4s → 8s → 16s (capped at 60s) before retrying.
    
    Args:
        session: requests.Session to use for the request
        url: URL to fetch
        timeout: request timeout in seconds (default: 30)
        retries: max number of attempts (default: 4)
        log: optional logger for structured logging. If provided, must have
             .info() and .warning() methods that accept extra= kwargs.
        
    Returns:
        Response text (HTML, JSON, etc.) on success
        
    Raises:
        RuntimeError: if all retry attempts are exhausted
        
    Example:
        from common import create_session, fetch_with_retry, setup_logger
        
        log = setup_logger("my_scraper")
        session = create_session()
        html = fetch_with_retry(session, "https://example.com", log=log)
    """
    last_exc: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            if log:
                log.info("Fetching URL", extra={"url": url, "attempt": attempt})
            
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.text

        except requests.RequestException as exc:
            last_exc = exc
            # Exponential backoff: 2^1=2s, 2^2=4s, 2^3=8s, 2^4=16s, capped at 60s
            wait = min(2 ** attempt, 60)
            
            if log:
                log.warning(
                    "Fetch failed",
                    extra={
                        "url": url,
                        "attempt": attempt,
                        "wait_s": wait,
                        "error": str(exc)
                    },
                )
            
            # Only sleep if there are more attempts left
            if attempt < retries:
                time.sleep(wait)

    # All attempts exhausted
    raise RuntimeError(
        f"All {retries} attempts failed for {url}"
    ) from last_exc
