"""
Unit tests for cycle_tracker.py lifecycle functions.

Tests cover:
- record_cycle_start() idempotency (only first call per cycle returns True)
- get_cycle_start_time() returns stored timestamp
- get_cycle_start_time() returns None when no start recorded
- Atomic SET NX+EX ensures TTL is always set
- Multiple cycles in the same day get independent start times

Version: 1.0.0
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

MODULE = "app.utils.cycle_tracker"

pytestmark = pytest.mark.unit


class TestRecordCycleStart:

    @patch(f"{MODULE}._get_redis")
    @patch(f"{MODULE}._get_cycle_key", return_value="sync_cycle:2026-03-23:0")
    def test_first_call_returns_true(self, mock_key, mock_get_redis):
        mock_r = MagicMock()
        mock_r.set.return_value = True  # SETNX succeeded
        mock_get_redis.return_value = mock_r

        from app.utils.cycle_tracker import record_cycle_start
        result = record_cycle_start()

        assert result is True
        mock_r.set.assert_called_once()
        call_kwargs = mock_r.set.call_args
        assert call_kwargs[1]["nx"] is True
        assert call_kwargs[1]["ex"] == 86400

    @patch(f"{MODULE}._get_redis")
    @patch(f"{MODULE}._get_cycle_key", return_value="sync_cycle:2026-03-23:0")
    def test_second_call_returns_false(self, mock_key, mock_get_redis):
        mock_r = MagicMock()
        mock_r.set.return_value = False  # SETNX failed (key already exists)
        mock_get_redis.return_value = mock_r

        from app.utils.cycle_tracker import record_cycle_start
        result = record_cycle_start()

        assert result is False

    @patch(f"{MODULE}._get_redis")
    @patch(f"{MODULE}._get_cycle_key", return_value="sync_cycle:2026-03-23:0")
    def test_uses_atomic_set_nx_ex(self, mock_key, mock_get_redis):
        """Ensures we use r.set(nx=True, ex=TTL) instead of separate setnx+expire."""
        mock_r = MagicMock()
        mock_r.set.return_value = True
        mock_get_redis.return_value = mock_r

        from app.utils.cycle_tracker import record_cycle_start
        record_cycle_start()

        # Should NOT call setnx or expire separately
        mock_r.setnx.assert_not_called()
        mock_r.expire.assert_not_called()
        # Should use atomic set with nx and ex
        mock_r.set.assert_called_once()
        args, kwargs = mock_r.set.call_args
        assert args[0] == "sync_cycle:2026-03-23:0:started_at"
        assert kwargs["nx"] is True
        assert kwargs["ex"] == 86400

    @patch(f"{MODULE}._get_redis")
    @patch(f"{MODULE}._get_cycle_key", return_value="sync_cycle:2026-03-23:0")
    def test_stores_iso_timestamp(self, mock_key, mock_get_redis):
        mock_r = MagicMock()
        mock_r.set.return_value = True
        mock_get_redis.return_value = mock_r

        from app.utils.cycle_tracker import record_cycle_start
        record_cycle_start()

        stored_value = mock_r.set.call_args[0][1]
        # Should be a valid ISO timestamp
        dt = datetime.fromisoformat(stored_value)
        assert dt.tzinfo is not None  # timezone-aware


class TestGetCycleStartTime:

    @patch(f"{MODULE}._get_redis")
    @patch(f"{MODULE}._get_cycle_key", return_value="sync_cycle:2026-03-23:0")
    def test_returns_stored_timestamp(self, mock_key, mock_get_redis):
        mock_r = MagicMock()
        mock_r.get.return_value = "2026-03-23T10:00:00+00:00"
        mock_get_redis.return_value = mock_r

        from app.utils.cycle_tracker import get_cycle_start_time
        result = get_cycle_start_time()

        assert result == "2026-03-23T10:00:00+00:00"
        mock_r.get.assert_called_once_with("sync_cycle:2026-03-23:0:started_at")

    @patch(f"{MODULE}._get_redis")
    @patch(f"{MODULE}._get_cycle_key", return_value="sync_cycle:2026-03-23:0")
    def test_returns_none_when_not_started(self, mock_key, mock_get_redis):
        mock_r = MagicMock()
        mock_r.get.return_value = None
        mock_get_redis.return_value = mock_r

        from app.utils.cycle_tracker import get_cycle_start_time
        result = get_cycle_start_time()

        assert result is None

    @patch(f"{MODULE}._get_redis")
    def test_uses_explicit_cycle_id(self, mock_get_redis):
        mock_r = MagicMock()
        mock_r.get.return_value = "2026-03-22T08:00:00+00:00"
        mock_get_redis.return_value = mock_r

        from app.utils.cycle_tracker import get_cycle_start_time
        result = get_cycle_start_time(cycle_id="sync_cycle:2026-03-22:0")

        assert result == "2026-03-22T08:00:00+00:00"
        mock_r.get.assert_called_once_with("sync_cycle:2026-03-22:0:started_at")
