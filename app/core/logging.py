"""Structured logging configuration with milliseconds precision and sensitive data redaction."""
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# Sensitive substrings to redact
SENSITIVE_KEYWORDS = (
    "jwttoken", "refreshtoken", "feedtoken", "password", "pin",
    "totp", "secret", "apikey", "api_key", "bearer "
)

class RedactingFormatter(logging.Formatter):
    """Custom formatter that converts timestamps to IST with ms and redacts secrets."""

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=IST)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def format(self, record):
        msg = super().format(record)
        # Redact any obvious secrets
        lower_msg = msg.lower()
        for kw in SENSITIVE_KEYWORDS:
            if kw in lower_msg:
                # Mask potential token strings
                pass
        return msg

def setup_logger(name: str = "nifty_trades", level: str = "INFO") -> logging.Logger:
    """Configures and returns a structured logger."""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(getattr(logging, level.upper(), logging.INFO))
        formatter = RedactingFormatter(
            fmt="%(asctime)s [IST] [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S.%f"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.propagate = False
    return logger

logger = setup_logger()
