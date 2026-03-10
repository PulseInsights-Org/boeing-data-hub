"""
Unit tests for dispatch_lock.py — deferred bucket tracking and catch-up lock.

Tests cover:
- record_deferred_bucket: stores bucket in Redis set with TTL
- get_deferred_buckets: retrieves deferred buckets as Set[int]
- clear_deferred_buckets: deletes the deferred set
- acquire_catchup_lock: NX lock for catch-up coordination
- release_catchup_lock: deletes the catch-up lock

Version: 1.0.0
"""
import pytest
from unittest.mock import patch, MagicMock

from app.utils.dispatch_lock import (
    record_deferred_bucket,
    get_deferred_buckets,
    clear_deferred_buckets,
    acquire_catchup_lock,
    release_catchup_lock,
    _DEFERRED_SET_TTL,
    _CATCHUP_LOCK_TTL,
)


pytestmark = pytest.mark.unit

MODULE = "app.utils.dispatch_lock"


@pytest.fixture
def mock_redis():
    """Provide a fresh MagicMock Redis client for each test."""
    r = MagicMock()
    with patch(f"{MODULE}._get_redis", return_value=r):
        yield r


@pytest.fixture
def fixed_today():
    """Pin _today() to a fixed date for deterministic key names."""
    with patch(f"{MODULE}._today", return_value="2026-02-28"):
        yield "2026-02-28"


# --------------------------------------------------------------------------
# record_deferred_bucket
# --------------------------------------------------------------------------

class TestRecordDeferredBucket:

    def test_adds_bucket_to_redis_set(self, mock_redis, fixed_today):
        record_deferred_bucket(5)

        mock_redis.sadd.assert_called_once_with(
            f"deferred_sync_buckets:{fixed_today}", 5
        )

    def test_sets_ttl_on_deferred_set(self, mock_redis, fixed_today):
        record_deferred_bucket(5)

        mock_redis.expire.assert_called_once_with(
            f"deferred_sync_buckets:{fixed_today}", _DEFERRED_SET_TTL
        )

    def test_records_multiple_buckets(self, mock_redis, fixed_today):
        record_deferred_bucket(5)
        record_deferred_bucket(6)
        record_deferred_bucket(7)

        assert mock_redis.sadd.call_count == 3
        bucket_args = [call[0][1] for call in mock_redis.sadd.call_args_list]
        assert bucket_args == [5, 6, 7]


# --------------------------------------------------------------------------
# get_deferred_buckets
# --------------------------------------------------------------------------

class TestGetDeferredBuckets:

    def test_returns_set_of_ints(self, mock_redis, fixed_today):
        mock_redis.smembers.return_value = {"5", "6", "7"}

        result = get_deferred_buckets()

        assert result == {5, 6, 7}
        mock_redis.smembers.assert_called_once_with(
            f"deferred_sync_buckets:{fixed_today}"
        )

    def test_returns_empty_set_when_no_deferred(self, mock_redis, fixed_today):
        mock_redis.smembers.return_value = set()

        result = get_deferred_buckets()

        assert result == set()

    def test_returns_empty_set_when_key_missing(self, mock_redis, fixed_today):
        mock_redis.smembers.return_value = None

        result = get_deferred_buckets()

        assert result == set()

    def test_handles_single_bucket(self, mock_redis, fixed_today):
        mock_redis.smembers.return_value = {"12"}

        result = get_deferred_buckets()

        assert result == {12}


# --------------------------------------------------------------------------
# clear_deferred_buckets
# --------------------------------------------------------------------------

class TestClearDeferredBuckets:

    def test_deletes_deferred_set(self, mock_redis, fixed_today):
        clear_deferred_buckets()

        mock_redis.delete.assert_called_once_with(
            f"deferred_sync_buckets:{fixed_today}"
        )


# --------------------------------------------------------------------------
# acquire_catchup_lock
# --------------------------------------------------------------------------

class TestAcquireCatchupLock:

    def test_acquires_lock_when_free(self, mock_redis, fixed_today):
        mock_redis.set.return_value = True

        result = acquire_catchup_lock("task-abc")

        assert result is True
        mock_redis.set.assert_called_once_with(
            f"deferred_catchup_lock:{fixed_today}",
            "task-abc",
            nx=True,
            ex=_CATCHUP_LOCK_TTL,
        )

    def test_fails_when_lock_held(self, mock_redis, fixed_today):
        mock_redis.set.return_value = None
        mock_redis.get.return_value = "other-task"

        result = acquire_catchup_lock("task-abc")

        assert result is False

    def test_default_task_id_is_unknown(self, mock_redis, fixed_today):
        mock_redis.set.return_value = True

        acquire_catchup_lock()

        call_args = mock_redis.set.call_args
        assert call_args[0][1] == "unknown"


# --------------------------------------------------------------------------
# release_catchup_lock
# --------------------------------------------------------------------------

class TestReleaseCatchupLock:

    def test_deletes_catchup_lock(self, mock_redis, fixed_today):
        release_catchup_lock()

        mock_redis.delete.assert_called_once_with(
            f"deferred_catchup_lock:{fixed_today}"
        )
