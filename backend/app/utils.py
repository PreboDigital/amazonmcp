"""
Shared utility functions.
"""

import logging
import uuid as uuid_mod
from datetime import datetime, timezone
from fastapi import HTTPException

logger = logging.getLogger(__name__)


def parse_uuid(value: str, field_name: str = "id") -> uuid_mod.UUID:
    """
    Parse a string as UUID, raising a 400 HTTPException on invalid input
    instead of letting a bare ValueError bubble up as a 500.
    """
    try:
        return uuid_mod.UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid UUID for '{field_name}': {value!r}",
        )


def safe_error_detail(exc: Exception, fallback: str = "An internal error occurred. Please try again later.") -> str:
    """
    Return a sanitized error message safe for client consumption.
    Logs the real exception detail server-side.
    """
    logger.error(f"Operation failed: {exc}", exc_info=True)
    return fallback


def utcnow() -> datetime:
    """
    Return the current UTC time as a naive datetime (no tzinfo).
    Replaces the deprecated ``datetime.utcnow()``.
    Naive datetimes are used because our DB columns are TIMESTAMP WITHOUT TIME ZONE.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
