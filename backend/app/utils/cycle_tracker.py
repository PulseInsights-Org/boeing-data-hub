"""
Cycle tracker — Redis-backed detection of sync cycle completion.

Tracks which time buckets have been dispatched in the current cycle.
When all buckets are dispatched, the cycle is marked complete and a
report generation task can be triggered.

Cycle key format: sync_cycle:{YYYY-MM-DD}:{N}
  - N starts at 0 and increments each time a cycle completes within the same day.

Version: 1.0.0
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)

MAX_BUCKETS = settings.sync_max_buckets
CYCLE_TTL = 86400  # 24 hours


def _get_redis(redis_url: str | None = None) -> redis.Redis:
    """Create a Redis client from the configured URL."""
    url = redis_url or settings.redis_url
    return redis.Redis.from_url(url, decode_responses=True)


def _get_cycle_key(r: redis.Redis) -> str:
    """Get the current cycle key, creating one if it doesn't exist."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    counter_key = f"sync_cycle_counter:{today}"

    # Get or initialise the daily cycle counter
    cycle_num = r.get(counter_key)
    if cycle_num is None:
        r.set(counter_key, 0, ex=CYCLE_TTL)
        cycle_num = 0
    else:
        cycle_num = int(cycle_num)

    return f"sync_cycle:{today}:{cycle_num}"


def record_bucket_dispatched(
    bucket: int,
    redis_url: str | None = None,
) -> bool:
    """Record that a bucket has been dispatched and check cycle completion.

    Args:
        bucket: The bucket number that was just dispatched.
        redis_url: Optional Redis URL override.

    Returns:
        True if this bucket completed the cycle (all buckets dispatched).
    """
    r = _get_redis(redis_url)
    cycle_key = _get_cycle_key(r)

    r.sadd(cycle_key, bucket)
    r.expire(cycle_key, CYCLE_TTL)

    completed_count = r.scard(cycle_key)
    is_complete = completed_count >= MAX_BUCKETS

    if is_complete:
        logger.info(
            f"Sync cycle complete! key={cycle_key}, "
            f"buckets={completed_count}/{MAX_BUCKETS}"
        )
        # Increment cycle counter so the next dispatch starts a fresh cycle
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        counter_key = f"sync_cycle_counter:{today}"
        r.incr(counter_key)
    else:
        logger.debug(
            f"Bucket {bucket} recorded. Progress: {completed_count}/{MAX_BUCKETS}"
        )

    return is_complete


def get_cycle_progress(redis_url: str | None = None) -> Dict[str, Any]:
    """Get current cycle progress.

    Returns:
        Dict with cycle_id, buckets_completed, total_buckets, is_complete.
    """
    r = _get_redis(redis_url)
    cycle_key = _get_cycle_key(r)

    members = r.smembers(cycle_key)
    buckets_completed: List[int] = sorted(int(m) for m in members) if members else []
    completed_count = len(buckets_completed)

    return {
        "cycle_id": cycle_key,
        "buckets_completed": buckets_completed,
        "total_buckets": MAX_BUCKETS,
        "is_complete": completed_count >= MAX_BUCKETS,
        "progress_percent": round(completed_count / MAX_BUCKETS * 100, 1)
        if MAX_BUCKETS > 0
        else 0,
    }


def reset_cycle(redis_url: str | None = None) -> str:
    """Manually reset the current cycle and start a new one.

    Returns:
        The new cycle key.
    """
    r = _get_redis(redis_url)
    cycle_key = _get_cycle_key(r)

    # Delete current cycle data
    r.delete(cycle_key)

    # Increment counter
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    counter_key = f"sync_cycle_counter:{today}"
    r.incr(counter_key)

    new_key = _get_cycle_key(r)
    logger.info(f"Cycle reset. Old={cycle_key}, New={new_key}")
    return new_key
