"""
Dispatch deduplication — Redis-based locks and SKU tracking for sync dispatch.

Three mechanisms to prevent re-syncing and task flooding:

1. Dispatch Idempotency Lock: Ensures only ONE dispatch_hourly per bucket window.
2. SKU Dispatch Dedup Set: Tracks which SKUs were already dispatched in the window.
3. Batch Idempotency Lock: Prevents two workers processing the same Boeing batch.

Redis connection follows the same pattern as cycle_tracker.py.
Version: 1.0.0
"""
import hashlib
import logging
from datetime import datetime, timezone
from typing import List, Optional, Set

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)

# TTLs by sync mode
_DISPATCH_LOCK_TTL = {"testing": 600, "production": 3600}   # 10 min / 1 hour
_SKU_SET_TTL = {"testing": 900, "production": 7200}          # 15 min / 2 hours
_BATCH_LOCK_TTL = 300                                         # 5 min (both modes)

SYNC_MODE = settings.sync_mode


def _get_redis() -> redis.Redis:
    """Create a Redis client from the configured URL."""
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _today() -> str:
    """Current UTC date as YYYY-MM-DD."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── 1. Dispatch Idempotency Lock ────────────────────────────────────────────

def acquire_dispatch_lock(bucket: int, task_id: str = "unknown") -> bool:
    """Acquire a per-bucket dispatch lock (SET NX EX).

    Returns True if lock was acquired (this task should proceed).
    Returns False if lock is already held (another dispatch is running).
    """
    r = _get_redis()
    key = f"dispatch_lock:{_today()}:{bucket}"
    ttl = _DISPATCH_LOCK_TTL.get(SYNC_MODE, 3600)

    acquired = r.set(key, task_id, nx=True, ex=ttl)

    if acquired:
        logger.info(f"Dispatch lock ACQUIRED: bucket={bucket}, task={task_id}, ttl={ttl}s")
    else:
        holder = r.get(key)
        logger.info(f"Dispatch lock HELD: bucket={bucket}, holder={holder}, skipping")

    return bool(acquired)


def release_dispatch_lock(bucket: int) -> None:
    """Release the dispatch lock for a bucket."""
    r = _get_redis()
    key = f"dispatch_lock:{_today()}:{bucket}"
    r.delete(key)
    logger.debug(f"Dispatch lock released: bucket={bucket}")


# ── 2. SKU Dispatch Dedup Set ───────────────────────────────────────────────

def record_dispatched_skus(bucket: int, skus: List[str]) -> int:
    """Record SKUs as dispatched for the current bucket window.

    Returns the number of NEW SKUs added (not already in the set).
    """
    if not skus:
        return 0

    r = _get_redis()
    key = f"dispatched_skus:{_today()}:{bucket}"
    ttl = _SKU_SET_TTL.get(SYNC_MODE, 7200)

    added = r.sadd(key, *skus)
    r.expire(key, ttl)

    logger.debug(f"Recorded {added} new dispatched SKUs for bucket {bucket} (total in set: {r.scard(key)})")
    return added


def get_already_dispatched_skus(bucket: int) -> Set[str]:
    """Get the set of SKUs already dispatched in the current bucket window."""
    r = _get_redis()
    key = f"dispatched_skus:{_today()}:{bucket}"
    members = r.smembers(key)
    return set(members) if members else set()


# ── 3. Batch Idempotency Lock ──────────────────────────────────────────────

def compute_batch_hash(skus: List[str]) -> str:
    """Compute a deterministic hash for a batch of SKUs."""
    normalized = ",".join(sorted(s.strip().upper() for s in skus))
    return hashlib.md5(normalized.encode()).hexdigest()


def acquire_batch_lock(sku_hash: str, worker_id: str = "unknown") -> bool:
    """Acquire a per-batch lock to prevent duplicate processing.

    Returns True if lock was acquired (this worker should process the batch).
    Returns False if lock is already held (another worker is processing).
    """
    r = _get_redis()
    key = f"batch_lock:{sku_hash}"

    acquired = r.set(key, worker_id, nx=True, ex=_BATCH_LOCK_TTL)

    if acquired:
        logger.debug(f"Batch lock ACQUIRED: hash={sku_hash[:12]}..., worker={worker_id}")
    else:
        holder = r.get(key)
        logger.info(f"Batch lock HELD: hash={sku_hash[:12]}..., holder={holder}, skipping")

    return bool(acquired)


def release_batch_lock(sku_hash: str) -> None:
    """Release the batch lock after processing completes."""
    r = _get_redis()
    key = f"batch_lock:{sku_hash}"
    r.delete(key)
    logger.debug(f"Batch lock released: hash={sku_hash[:12]}...")


def compute_window_start(sync_mode: Optional[str] = None) -> datetime:
    """Compute the start of the current bucket window.

    Testing mode (10-min buckets): floor to nearest 10-min boundary.
    Production mode (hour buckets): floor to start of current hour.
    """
    mode = sync_mode or SYNC_MODE
    now = datetime.now(timezone.utc)

    if mode == "testing":
        floored_minute = (now.minute // 10) * 10
        return now.replace(minute=floored_minute, second=0, microsecond=0)
    else:
        return now.replace(minute=0, second=0, microsecond=0)
