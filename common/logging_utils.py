"""Structured JSON logging for scrapers."""
import json
import logging
import sys


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line, including any extra= kwargs.
    
    Example output:
        {"ts": "2024-01-01 12:00:00", "level": "INFO", "msg": "Fetching page",
         "page": 1, "attempt": 1}
    
    Any fields passed as extra={...} to the logger call are merged into the
    output, making it easy to query logs by structured fields later.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Base fields every log line has
        payload: dict = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        
        # Merge in anything passed as extra={...} to the logger call
        _STANDARD_ATTRS = logging.LogRecord.__dict__.keys() | {
            "message", "asctime", "taskName"
        }
        for key, val in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = val
        
        return json.dumps(payload, default=str)


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Create a logger with structured JSON output to stderr.
    
    Args:
        name: Logger name (e.g., "hockey", "movies", "turtles")
        level: Logging level (default: INFO)
        
    Returns:
        Configured logger instance
        
    Example:
        log = setup_logger("hockey")
        log.info("Fetching page", extra={"page": 1, "attempt": 1})
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.addHandler(handler)
    logger.propagate = False
    return logger
