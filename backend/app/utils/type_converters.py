"""
Type converters — shared value conversion utilities.
Version: 1.0.0
"""
from typing import Any, Optional


def to_float(value: Any) -> Optional[float]:
    """Convert value to float, returning None if invalid or zero."""
    if value is None:
        return None
    try:
        val = float(value)
        return val if val != 0 else None
    except (ValueError, TypeError):
        return None


def to_int(value: Any) -> Optional[int]:
    """Convert value to int, returning None if invalid."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None
