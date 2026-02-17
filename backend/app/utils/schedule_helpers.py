"""
Schedule helpers — time, bucket, and sync window utilities.
Version: 1.0.0
"""
from datetime import datetime, timezone

from app.core.config import settings

SYNC_MODE = settings.sync_mode


def get_current_hour_utc() -> int:
    """Get current hour in UTC (0-23)."""
    return datetime.now(timezone.utc).hour


def get_current_minute_bucket() -> int:
    """Get current minute bucket for testing mode (10-min intervals, 0-5)."""
    return datetime.now(timezone.utc).minute // 10


def get_current_bucket() -> int:
    """Get current bucket based on sync mode."""
    if SYNC_MODE == "testing":
        return get_current_minute_bucket()
    return get_current_hour_utc()


def is_within_sync_window(target_hour: int, window_minutes: int = 15) -> bool:
    """Check if current time is within sync window for target hour."""
    now = datetime.now(timezone.utc)
    return now.hour == target_hour and now.minute >= (60 - window_minutes)


def calculate_next_retry_time(consecutive_failures: int, base_hours: int = 4) -> int:
    """Calculate hours until next retry using exponential backoff (capped at 24)."""
    hours = base_hours * (2 ** (consecutive_failures - 1))
    return min(hours, 24)
