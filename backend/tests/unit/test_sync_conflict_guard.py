"""
Unit tests for sync conflict guard and deferred catch-up mechanisms.

Tests cover:
- _extraction_session_active: checks batch_store for active batches
- _dispatch_bucket: reusable dispatch logic with 3-layer dedup
- dispatch_hourly: conflict guard + passive catch-up path
- dispatch_deferred_catchup: active catch-up after extraction completes
- dispatch_retry: conflict guard

Version: 1.0.0
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, call

TASK_MODULE = "app.celery_app.tasks.sync_dispatch"


pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------
# _extraction_session_active
# --------------------------------------------------------------------------

class TestExtractionSessionActive:

    @patch(f"{TASK_MODULE}.get_batch_store")
    def test_returns_true_when_batches_active(self, mock_get_bs):
        from app.celery_app.tasks.sync_dispatch import _extraction_session_active

        mock_bs = MagicMock()
        mock_bs.get_active_batches.return_value = [
            {"id": "batch-001", "status": "processing"},
        ]
        mock_get_bs.return_value = mock_bs

        assert _extraction_session_active() is True

    @patch(f"{TASK_MODULE}.get_batch_store")
    def test_returns_false_when_no_active_batches(self, mock_get_bs):
        from app.celery_app.tasks.sync_dispatch import _extraction_session_active

        mock_bs = MagicMock()
        mock_bs.get_active_batches.return_value = []
        mock_get_bs.return_value = mock_bs

        assert _extraction_session_active() is False

    @patch(f"{TASK_MODULE}.get_batch_store")
    def test_fail_open_on_exception(self, mock_get_bs):
        """If DB query fails, sync should proceed (fail-open)."""
        from app.celery_app.tasks.sync_dispatch import _extraction_session_active

        mock_get_bs.side_effect = Exception("DB connection failed")

        assert _extraction_session_active() is False


# --------------------------------------------------------------------------
# _dispatch_bucket
# --------------------------------------------------------------------------

class TestDispatchBucket:

    def _make_sync_store(self, slot_counts=None, products=None):
        store = MagicMock()
        store.get_slot_counts.return_value = slot_counts or {}
        store.get_products_for_hour.return_value = products or []
        store.mark_products_syncing = MagicMock()
        return store

    @patch(f"{TASK_MODULE}.record_dispatched_skus")
    @patch(f"{TASK_MODULE}.get_already_dispatched_skus", return_value=set())
    @patch(f"{TASK_MODULE}.process_boeing_batch")
    @patch(f"{TASK_MODULE}.get_slot_distribution")
    def test_dispatches_active_slot_products(
        self, mock_dist, mock_task, mock_dedup, mock_record
    ):
        from app.celery_app.tasks.sync_dispatch import _dispatch_bucket

        mock_dist.return_value = {
            "active_slots": [5], "active_count": 1,
            "filling_slots": [], "filling_count": 0,
            "dormant_slots": [], "dormant_count": 0,
        }
        products = [
            {"sku": f"SKU-{i}", "user_id": "test-user"} for i in range(15)
        ]
        sync_store = self._make_sync_store(
            slot_counts={5: 15}, products=products
        )
        window_start = datetime(2026, 2, 28, 5, 0, tzinfo=timezone.utc)

        stats = _dispatch_bucket(sync_store, 5, window_start)

        assert stats["bucket"] == 5
        assert stats["products_dispatched"] == 15
        assert stats["batches_dispatched"] >= 1
        mock_task.delay.assert_called()

    @patch(f"{TASK_MODULE}.record_dispatched_skus")
    @patch(f"{TASK_MODULE}.get_already_dispatched_skus")
    @patch(f"{TASK_MODULE}.process_boeing_batch")
    @patch(f"{TASK_MODULE}.get_slot_distribution")
    def test_deduplicates_already_dispatched_skus(
        self, mock_dist, mock_task, mock_dedup, mock_record
    ):
        from app.celery_app.tasks.sync_dispatch import _dispatch_bucket

        mock_dist.return_value = {
            "active_slots": [5], "active_count": 1,
            "filling_slots": [], "filling_count": 0,
            "dormant_slots": [], "dormant_count": 0,
        }
        # 3 products, but 1 already dispatched
        mock_dedup.return_value = {"SKU-0"}
        products = [
            {"sku": "SKU-0", "user_id": "test-user"},
            {"sku": "SKU-1", "user_id": "test-user"},
            {"sku": "SKU-2", "user_id": "test-user"},
        ]
        sync_store = self._make_sync_store(slot_counts={5: 15}, products=products)
        window_start = datetime(2026, 2, 28, 5, 0, tzinfo=timezone.utc)

        stats = _dispatch_bucket(sync_store, 5, window_start)

        assert stats["products_dispatched"] == 2
        assert stats["skus_deduped"] == 1

    @patch(f"{TASK_MODULE}.get_already_dispatched_skus", return_value=set())
    @patch(f"{TASK_MODULE}.process_boeing_batch")
    @patch(f"{TASK_MODULE}.get_slot_distribution")
    def test_dormant_slot_dispatches_nothing(self, mock_dist, mock_task, mock_dedup):
        from app.celery_app.tasks.sync_dispatch import _dispatch_bucket

        mock_dist.return_value = {
            "active_slots": [], "active_count": 0,
            "filling_slots": [], "filling_count": 0,
            "dormant_slots": [5], "dormant_count": 1,
        }
        sync_store = self._make_sync_store(slot_counts={5: 0})
        window_start = datetime(2026, 2, 28, 5, 0, tzinfo=timezone.utc)

        stats = _dispatch_bucket(sync_store, 5, window_start)

        assert stats["products_dispatched"] == 0
        assert stats["batches_dispatched"] == 0
        mock_task.delay.assert_not_called()


# --------------------------------------------------------------------------
# dispatch_hourly — conflict guard
# --------------------------------------------------------------------------

class TestDispatchHourlyConflictGuard:

    @patch(f"{TASK_MODULE}.SYNC_ENABLED", True)
    @patch(f"{TASK_MODULE}.SYNC_MODE", "production")
    @patch(f"{TASK_MODULE}.get_current_hour_utc", return_value=5)
    @patch(f"{TASK_MODULE}.record_deferred_bucket")
    @patch(f"{TASK_MODULE}._extraction_session_active", return_value=True)
    def test_defers_bucket_when_extraction_active(
        self, mock_active, mock_defer, mock_hour
    ):
        from app.celery_app.tasks.sync_dispatch import dispatch_hourly

        now = datetime(2026, 2, 28, 5, 45, tzinfo=timezone.utc)
        with patch(f"{TASK_MODULE}.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            result = dispatch_hourly()

        assert result["status"] == "deferred"
        assert result["reason"] == "extraction_in_progress"
        assert result["deferred_bucket"] == 5
        mock_defer.assert_called_once_with(5)

    @patch(f"{TASK_MODULE}.SYNC_ENABLED", True)
    @patch(f"{TASK_MODULE}.SYNC_MODE", "production")
    @patch(f"{TASK_MODULE}.get_current_hour_utc", return_value=6)
    @patch(f"{TASK_MODULE}.wait_for_cycle_completion")
    @patch(f"{TASK_MODULE}.record_bucket_dispatched", return_value=False)
    @patch(f"{TASK_MODULE}._dispatch_bucket", return_value={
        "bucket": 6, "batches_dispatched": 2, "products_dispatched": 20, "skus_deduped": 0
    })
    @patch(f"{TASK_MODULE}.get_slot_distribution", return_value={
        "active_count": 1, "filling_count": 0, "dormant_count": 0
    })
    @patch(f"{TASK_MODULE}.get_deferred_buckets", return_value=set())
    @patch(f"{TASK_MODULE}.release_dispatch_lock")
    @patch(f"{TASK_MODULE}.compute_window_start")
    @patch(f"{TASK_MODULE}.acquire_dispatch_lock", return_value=True)
    @patch(f"{TASK_MODULE}._extraction_session_active", return_value=False)
    @patch(f"{TASK_MODULE}.get_sync_store")
    def test_proceeds_when_no_extraction_active(
        self, mock_store, mock_active, mock_acq, mock_ws, mock_rel,
        mock_deferred, mock_dist, mock_dispatch, mock_cycle, mock_wait, mock_hour
    ):
        from app.celery_app.tasks.sync_dispatch import dispatch_hourly

        mock_store.return_value = MagicMock()
        mock_store.return_value.get_slot_counts.return_value = {6: 20}
        mock_store.return_value.reset_stuck_products.return_value = 0

        now = datetime(2026, 2, 28, 6, 45, tzinfo=timezone.utc)
        with patch(f"{TASK_MODULE}.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            result = dispatch_hourly()

        assert result["status"] == "completed"
        assert result["batches_dispatched"] == 2
        assert result["products_dispatched"] == 20


# --------------------------------------------------------------------------
# dispatch_hourly — passive catch-up path
# --------------------------------------------------------------------------

class TestDispatchHourlyPassiveCatchup:

    @patch(f"{TASK_MODULE}.SYNC_ENABLED", True)
    @patch(f"{TASK_MODULE}.SYNC_MODE", "production")
    @patch(f"{TASK_MODULE}.get_current_hour_utc", return_value=6)
    @patch(f"{TASK_MODULE}.wait_for_cycle_completion")
    @patch(f"{TASK_MODULE}.record_bucket_dispatched", return_value=False)
    @patch(f"{TASK_MODULE}._dispatch_bucket")
    @patch(f"{TASK_MODULE}.get_slot_distribution", return_value={
        "active_count": 1, "filling_count": 0, "dormant_count": 0
    })
    @patch(f"{TASK_MODULE}.clear_deferred_buckets")
    @patch(f"{TASK_MODULE}.release_catchup_lock")
    @patch(f"{TASK_MODULE}.acquire_catchup_lock", return_value=True)
    @patch(f"{TASK_MODULE}.get_deferred_buckets", return_value={5})
    @patch(f"{TASK_MODULE}.release_dispatch_lock")
    @patch(f"{TASK_MODULE}.compute_window_start")
    @patch(f"{TASK_MODULE}.acquire_dispatch_lock", return_value=True)
    @patch(f"{TASK_MODULE}._extraction_session_active", return_value=False)
    @patch(f"{TASK_MODULE}.get_sync_store")
    def test_catches_up_deferred_buckets(
        self, mock_store, mock_active, mock_acq, mock_ws, mock_rel,
        mock_deferred, mock_catchup_acq, mock_catchup_rel, mock_clear,
        mock_dist, mock_dispatch, mock_cycle, mock_wait, mock_hour
    ):
        from app.celery_app.tasks.sync_dispatch import dispatch_hourly

        mock_store.return_value = MagicMock()
        mock_store.return_value.get_slot_counts.return_value = {6: 20}
        mock_store.return_value.reset_stuck_products.return_value = 0

        mock_dispatch.return_value = {
            "bucket": 0, "batches_dispatched": 1,
            "products_dispatched": 10, "skus_deduped": 0,
        }

        now = datetime(2026, 2, 28, 6, 45, tzinfo=timezone.utc)
        with patch(f"{TASK_MODULE}.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            result = dispatch_hourly()

        # Should have dispatched both deferred bucket 5 and current bucket 6
        assert mock_dispatch.call_count == 2
        mock_clear.assert_called_once()
        mock_catchup_rel.assert_called_once()

    @patch(f"{TASK_MODULE}.SYNC_ENABLED", True)
    @patch(f"{TASK_MODULE}.SYNC_MODE", "production")
    @patch(f"{TASK_MODULE}.get_current_hour_utc", return_value=6)
    @patch(f"{TASK_MODULE}.wait_for_cycle_completion")
    @patch(f"{TASK_MODULE}.record_bucket_dispatched", return_value=False)
    @patch(f"{TASK_MODULE}._dispatch_bucket")
    @patch(f"{TASK_MODULE}.get_slot_distribution", return_value={
        "active_count": 1, "filling_count": 0, "dormant_count": 0
    })
    @patch(f"{TASK_MODULE}.clear_deferred_buckets")
    @patch(f"{TASK_MODULE}.release_catchup_lock")
    @patch(f"{TASK_MODULE}.acquire_catchup_lock", return_value=False)
    @patch(f"{TASK_MODULE}.get_deferred_buckets", return_value={5})
    @patch(f"{TASK_MODULE}.release_dispatch_lock")
    @patch(f"{TASK_MODULE}.compute_window_start")
    @patch(f"{TASK_MODULE}.acquire_dispatch_lock", return_value=True)
    @patch(f"{TASK_MODULE}._extraction_session_active", return_value=False)
    @patch(f"{TASK_MODULE}.get_sync_store")
    def test_skips_deferred_when_catchup_lock_held(
        self, mock_store, mock_active, mock_acq, mock_ws, mock_rel,
        mock_deferred, mock_catchup_acq, mock_catchup_rel, mock_clear,
        mock_dist, mock_dispatch, mock_cycle, mock_wait, mock_hour
    ):
        """When active path holds the lock, passive path skips deferred buckets."""
        from app.celery_app.tasks.sync_dispatch import dispatch_hourly

        mock_store.return_value = MagicMock()
        mock_store.return_value.get_slot_counts.return_value = {6: 20}
        mock_store.return_value.reset_stuck_products.return_value = 0

        mock_dispatch.return_value = {
            "bucket": 6, "batches_dispatched": 1,
            "products_dispatched": 10, "skus_deduped": 0,
        }

        now = datetime(2026, 2, 28, 6, 45, tzinfo=timezone.utc)
        with patch(f"{TASK_MODULE}.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            result = dispatch_hourly()

        # Only current bucket dispatched (deferred skipped because lock held)
        assert mock_dispatch.call_count == 1
        mock_clear.assert_not_called()


# --------------------------------------------------------------------------
# dispatch_deferred_catchup (active path)
# --------------------------------------------------------------------------

class TestDispatchDeferredCatchup:

    @patch(f"{TASK_MODULE}.SYNC_ENABLED", True)
    @patch(f"{TASK_MODULE}.release_catchup_lock")
    @patch(f"{TASK_MODULE}.clear_deferred_buckets")
    @patch(f"{TASK_MODULE}.record_bucket_dispatched", return_value=False)
    @patch(f"{TASK_MODULE}._dispatch_bucket")
    @patch(f"{TASK_MODULE}.compute_window_start")
    @patch(f"{TASK_MODULE}.get_sync_store")
    @patch(f"{TASK_MODULE}.acquire_catchup_lock", return_value=True)
    @patch(f"{TASK_MODULE}.get_deferred_buckets", return_value={5, 6})
    @patch(f"{TASK_MODULE}._extraction_session_active", return_value=False)
    def test_processes_all_deferred_buckets(
        self, mock_active, mock_deferred, mock_catchup_acq,
        mock_store, mock_ws, mock_dispatch, mock_cycle, mock_clear, mock_rel
    ):
        from app.celery_app.tasks.sync_dispatch import dispatch_deferred_catchup

        mock_dispatch.return_value = {
            "bucket": 0, "batches_dispatched": 3,
            "products_dispatched": 30, "skus_deduped": 0,
        }

        result = dispatch_deferred_catchup()

        assert result["status"] == "completed"
        assert result["total_batches"] == 6  # 3 per bucket * 2 buckets
        assert result["total_products"] == 60
        assert result["deferred_buckets"] == [5, 6]
        mock_clear.assert_called_once()
        mock_rel.assert_called_once()

    @patch(f"{TASK_MODULE}.SYNC_ENABLED", True)
    @patch(f"{TASK_MODULE}._extraction_session_active", return_value=True)
    def test_skips_when_new_extraction_active(self, mock_active):
        from app.celery_app.tasks.sync_dispatch import dispatch_deferred_catchup

        result = dispatch_deferred_catchup()

        assert result["status"] == "deferred"
        assert result["reason"] == "new_extraction_in_progress"

    @patch(f"{TASK_MODULE}.SYNC_ENABLED", True)
    @patch(f"{TASK_MODULE}.get_deferred_buckets", return_value=set())
    @patch(f"{TASK_MODULE}._extraction_session_active", return_value=False)
    def test_skips_when_no_deferred_buckets(self, mock_active, mock_deferred):
        from app.celery_app.tasks.sync_dispatch import dispatch_deferred_catchup

        result = dispatch_deferred_catchup()

        assert result["status"] == "skipped"
        assert result["reason"] == "no_deferred_buckets"

    @patch(f"{TASK_MODULE}.SYNC_ENABLED", True)
    @patch(f"{TASK_MODULE}.acquire_catchup_lock", return_value=False)
    @patch(f"{TASK_MODULE}.get_deferred_buckets", return_value={5})
    @patch(f"{TASK_MODULE}._extraction_session_active", return_value=False)
    def test_skips_when_catchup_lock_held(self, mock_active, mock_deferred, mock_lock):
        from app.celery_app.tasks.sync_dispatch import dispatch_deferred_catchup

        result = dispatch_deferred_catchup()

        assert result["status"] == "skipped"
        assert result["reason"] == "catchup_lock_held"

    @patch(f"{TASK_MODULE}.SYNC_ENABLED", False)
    def test_skips_when_sync_disabled(self):
        from app.celery_app.tasks.sync_dispatch import dispatch_deferred_catchup

        result = dispatch_deferred_catchup()

        assert result["status"] == "skipped"
        assert result["reason"] == "sync_disabled"

    @patch(f"{TASK_MODULE}.SYNC_ENABLED", True)
    @patch(f"{TASK_MODULE}.wait_for_cycle_completion")
    @patch(f"{TASK_MODULE}.release_catchup_lock")
    @patch(f"{TASK_MODULE}.clear_deferred_buckets")
    @patch(f"{TASK_MODULE}.record_bucket_dispatched", return_value=True)
    @patch(f"{TASK_MODULE}._dispatch_bucket")
    @patch(f"{TASK_MODULE}.compute_window_start")
    @patch(f"{TASK_MODULE}.get_sync_store")
    @patch(f"{TASK_MODULE}.acquire_catchup_lock", return_value=True)
    @patch(f"{TASK_MODULE}.get_deferred_buckets", return_value={23})
    @patch(f"{TASK_MODULE}._extraction_session_active", return_value=False)
    def test_triggers_cycle_report_when_cycle_complete(
        self, mock_active, mock_deferred, mock_catchup_acq,
        mock_store, mock_ws, mock_dispatch, mock_cycle, mock_clear,
        mock_rel, mock_wait
    ):
        from app.celery_app.tasks.sync_dispatch import dispatch_deferred_catchup

        mock_dispatch.return_value = {
            "bucket": 23, "batches_dispatched": 1,
            "products_dispatched": 5, "skus_deduped": 0,
        }

        result = dispatch_deferred_catchup()

        assert result["cycle_complete"] is True
        mock_wait.delay.assert_called_once()


# --------------------------------------------------------------------------
# dispatch_retry — conflict guard
# --------------------------------------------------------------------------

class TestDispatchRetryConflictGuard:

    @patch(f"{TASK_MODULE}.SYNC_ENABLED", True)
    @patch(f"{TASK_MODULE}._extraction_session_active", return_value=True)
    def test_defers_when_extraction_active(self, mock_active):
        from app.celery_app.tasks.sync_dispatch import dispatch_retry

        result = dispatch_retry()

        assert result["status"] == "deferred"
        assert result["reason"] == "extraction_in_progress"

    @patch(f"{TASK_MODULE}.SYNC_ENABLED", True)
    @patch(f"{TASK_MODULE}.get_sync_store")
    @patch(f"{TASK_MODULE}._extraction_session_active", return_value=False)
    def test_proceeds_when_no_extraction(self, mock_active, mock_store):
        from app.celery_app.tasks.sync_dispatch import dispatch_retry

        mock_store.return_value.get_failed_products_for_retry.return_value = []

        result = dispatch_retry()

        assert result["status"] == "completed"
