"""
Structured logging configuration.

Usage:
    from app.utils.logger import get_logger
    logger = get_logger(__name__)
    logger.info("Processing tool", extra={"tool_id": 42})
"""

import logging
import sys
from typing import Optional


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a pre-configured logger with structured JSON-like formatting.

    Args:
        name: Logger name, typically ``__name__`` of the calling module.

    Returns:
        A ``logging.Logger`` ready for production use.
    """
    logger = logging.getLogger(name or "execution_posting")

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            fmt=(
                "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
            ),
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

    # Prevent duplicate log entries when modules are imported multiple times
    logger.propagate = False

    return logger
