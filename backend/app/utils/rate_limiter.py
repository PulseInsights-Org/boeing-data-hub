"""
Boeing API Rate Limiter using Token Bucket Algorithm.

Provides a global, Redis-based rate limiter that ensures Boeing API
rate limits are never exceeded, regardless of the number of workers.

Token Bucket Configuration:
- Capacity: 2 tokens (max burst)
- Refill Rate: 2 tokens per minute (1 token every 30 seconds)
- Storage: Redis with atomic Lua script operations

Usage:
    from app.utils.rate_limiter import get_boeing_rate_limiter

    limiter = get_boeing_rate_limiter()
    limiter.wait_for_token()  # Blocks until token available
    # Now safe to call Boeing API
"""

import logging
import time
from typing import Optional, Tuple

import redis

logger = logging.getLogger("rate_limiter")

# Redis key prefix for Boeing rate limiter
REDIS_KEY_PREFIX = "boeing:rate_limiter"

# Token bucket configuration
DEFAULT_CAPACITY = 2           # Max tokens (burst capacity)
DEFAULT_REFILL_RATE = 2        # Tokens per minute
DEFAULT_REFILL_INTERVAL = 60   # Seconds per refill cycle

# Lua script for atomic token acquisition
# This ensures thread-safety across multiple workers
ACQUIRE_TOKEN_SCRIPT = """
local tokens_key = KEYS[1]
local last_refill_key = KEYS[2]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local refill_interval = tonumber(ARGV[3])
local now = tonumber(ARGV[4])

-- Get current state (default to full bucket)
local tokens = tonumber(redis.call('GET', tokens_key) or capacity)
local last_refill = tonumber(redis.call('GET', last_refill_key) or now)

-- Calculate tokens to add based on elapsed time
local elapsed = now - last_refill
local tokens_per_second = refill_rate / refill_interval
local tokens_to_add = elapsed * tokens_per_second
tokens = math.min(capacity, tokens + tokens_to_add)

-- Update last refill time
redis.call('SET', last_refill_key, now)

-- Try to acquire token
if tokens >= 1 then
    tokens = tokens - 1
    redis.call('SET', tokens_key, tokens)
    return {1, 0, tokens}  -- success=1, wait_time=0, remaining_tokens
else
    -- Calculate wait time until next token
    local tokens_needed = 1 - tokens
    local wait_time = tokens_needed / tokens_per_second
    redis.call('SET', tokens_key, tokens)
    return {0, wait_time, tokens}  -- success=0, wait_time, remaining_tokens
end
"""


class BoeingRateLimiter:
    """
    Global rate limiter for Boeing API using Token Bucket algorithm.

    Ensures that Boeing API rate limits (2 requests/minute) are never
    exceeded across all workers. Workers acquire tokens before making
    API calls - if no token available, they wait.

    This is superior to Celery's built-in rate limiting because:
    1. It's GLOBAL (not per-worker)
    2. Workers wait BEFORE calling API (proactive, not reactive)
    3. Uses atomic Redis operations (thread-safe)
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        capacity: int = DEFAULT_CAPACITY,
        refill_rate: int = DEFAULT_REFILL_RATE,
        refill_interval: int = DEFAULT_REFILL_INTERVAL,
        key_prefix: str = REDIS_KEY_PREFIX,
    ):
        """
        Initialize the rate limiter.

        Args:
            redis_client: Redis client instance
            capacity: Maximum tokens (burst capacity)
            refill_rate: Tokens added per refill_interval
            refill_interval: Seconds per refill cycle
            key_prefix: Redis key prefix for this limiter
        """
        self._redis = redis_client
        self._capacity = capacity
        self._refill_rate = refill_rate
        self._refill_interval = refill_interval
        self._tokens_key = f"{key_prefix}:tokens"
        self._last_refill_key = f"{key_prefix}:last_refill"

        # Register Lua script
        self._acquire_script = self._redis.register_script(ACQUIRE_TOKEN_SCRIPT)

        logger.info(
            f"BoeingRateLimiter initialized: capacity={capacity}, "
            f"refill_rate={refill_rate}/min"
        )

    def acquire_token(self) -> Tuple[bool, float, float]:
        """
        Try to acquire a token from the bucket.

        Returns:
            Tuple of (success, wait_time_seconds, remaining_tokens)
            - success: True if token was acquired
            - wait_time_seconds: How long to wait if failed (0 if success)
            - remaining_tokens: Current token count after operation
        """
        now = time.time()

        try:
            result = self._acquire_script(
                keys=[self._tokens_key, self._last_refill_key],
                args=[
                    self._capacity,
                    self._refill_rate,
                    self._refill_interval,
                    now
                ]
            )

            success = bool(result[0])
            wait_time = float(result[1])
            remaining = float(result[2])

            if success:
                logger.debug(f"Token acquired. Remaining: {remaining:.2f}")
            else:
                logger.debug(f"No token available. Wait time: {wait_time:.2f}s")

            return success, wait_time, remaining

        except redis.RedisError as e:
            logger.error(f"Redis error in acquire_token: {e}")
            # On Redis error, allow the request (fail-open)
            # This prevents total system failure if Redis is down
            return True, 0, 0

    def wait_for_token(self, timeout: float = 120.0) -> bool:
        """
        Block until a token is acquired or timeout is reached.

        This is the primary method for rate limiting. Workers call this
        before making Boeing API requests.

        Args:
            timeout: Maximum seconds to wait for a token

        Returns:
            True if token was acquired, False if timeout reached
        """
        start_time = time.time()

        while True:
            success, wait_time, remaining = self.acquire_token()

            if success:
                return True

            elapsed = time.time() - start_time
            if elapsed + wait_time > timeout:
                logger.warning(
                    f"Token acquisition timeout after {elapsed:.2f}s. "
                    f"Would need to wait {wait_time:.2f}s more."
                )
                return False

            # Wait for the calculated time (with small buffer)
            actual_wait = min(wait_time + 0.1, timeout - elapsed)
            logger.debug(f"Waiting {actual_wait:.2f}s for next token...")
            time.sleep(actual_wait)

    def get_available_tokens(self) -> float:
        """
        Get current available tokens (for monitoring).

        Note: This is a snapshot and may change immediately after.

        Returns:
            Current token count (0 to capacity)
        """
        try:
            tokens = self._redis.get(self._tokens_key)
            if tokens is None:
                return float(self._capacity)
            return float(tokens)
        except redis.RedisError as e:
            logger.error(f"Redis error in get_available_tokens: {e}")
            return 0.0

    def reset(self) -> None:
        """
        Reset the bucket to full capacity.

        Use with caution - typically only for testing or initialization.
        """
        try:
            now = time.time()
            self._redis.set(self._tokens_key, self._capacity)
            self._redis.set(self._last_refill_key, now)
            logger.info("Rate limiter reset to full capacity")
        except redis.RedisError as e:
            logger.error(f"Redis error in reset: {e}")

    def get_status(self) -> dict:
        """
        Get current rate limiter status (for monitoring dashboard).

        Returns:
            Dict with tokens, capacity, refill_rate, etc.
        """
        try:
            tokens = self.get_available_tokens()
            last_refill = self._redis.get(self._last_refill_key)
            last_refill_time = float(last_refill) if last_refill else None

            return {
                "available_tokens": tokens,
                "capacity": self._capacity,
                "refill_rate_per_minute": self._refill_rate,
                "last_refill_timestamp": last_refill_time,
                "tokens_key": self._tokens_key,
            }
        except redis.RedisError as e:
            logger.error(f"Redis error in get_status: {e}")
            return {"error": str(e)}


# Singleton instance
_rate_limiter: Optional[BoeingRateLimiter] = None


def get_boeing_rate_limiter() -> BoeingRateLimiter:
    """
    Get or create the singleton BoeingRateLimiter instance.

    Uses Redis connection from environment configuration.

    Returns:
        BoeingRateLimiter instance
    """
    global _rate_limiter

    if _rate_limiter is None:
        import os
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        redis_client = redis.from_url(redis_url)

        # Get config from environment or use defaults
        capacity = int(os.getenv("BOEING_RATE_LIMIT_CAPACITY", DEFAULT_CAPACITY))
        refill_rate = int(os.getenv("BOEING_RATE_LIMIT_REFILL", DEFAULT_REFILL_RATE))

        _rate_limiter = BoeingRateLimiter(
            redis_client=redis_client,
            capacity=capacity,
            refill_rate=refill_rate,
        )

    return _rate_limiter


def reset_rate_limiter() -> None:
    """Reset the singleton instance (for testing)."""
    global _rate_limiter
    _rate_limiter = None
