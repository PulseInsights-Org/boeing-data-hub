"""
Unit tests for cycle start trigger in dispatch_hourly.

Tests cover:
- First bucket triggers cycle start notification
- Subsequent buckets do NOT trigger notification
- Cycle start failure is non-fatal (dispatch continues)

Version: 1.0.0
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

DISPATCH_MODULE = "app.celery_app.tasks.sync_dispatch"
REPORT_MODULE = "app.celery_app.tasks.report_generation"

pytestmark = pytest.mark.unit


class TestDispatchHourlyCycleStart:

    @patch(f"{DISPATCH_MODULE}.release_dispatch_lock")
    @patch(f"{DISPATCH_MODULE}.record_bucket_dispatched", return_value=False)
    @patch(f"{DISPATCH_MODULE}.compute_window_start")
    @patch(f"{DISPATCH_MODULE}.acquire_dispatch_lock", return_value=True)
    @patch(f"{DISPATCH_MODULE}.get_deferred_buckets", return_value=set())
    @patch(f"{DISPATCH_MODULE}.get_batch_store")
    @patch(f"{DISPATCH_MODULE}.get_sync_dispatch_service")
    @patch(f"{DISPATCH_MODULE}.record_cycle_start", return_value=True)
    def test_first_bucket_triggers_notification(
        self,
        mock_cycle_start,
        mock_get_svc,
        mock_get_bs,
        mock_deferred,
        mock_acquire,
        mock_window,
        mock_record_bucket,
        mock_release,
    ):
        mock_svc = MagicMock()
        mock_svc.is_extraction_session_active.return_value = False
        mock_svc.dispatch_bucket.return_value = {
            "bucket": 0, "batches_dispatched": 1,
            "products_dispatched": 10, "skus_deduped": 0,
        }
        mock_svc._store.reset_stuck_products.return_value = 0
        mock_get_svc.return_value = mock_svc
        mock_window.return_value = datetime(2026, 3, 23, 6, 0, tzinfo=timezone.utc)

        mock_notify = MagicMock()
        with patch.dict(
            "sys.modules",
            {"app.celery_app.tasks.report_generation": MagicMock(
                send_cycle_start_notification=mock_notify,
                wait_for_cycle_completion=MagicMock(),
            )},
        ):
            from app.celery_app.tasks.sync_dispatch import dispatch_hourly
            dispatch_hourly.push_request(id="test-task")
            try:
                result = dispatch_hourly()
            finally:
                dispatch_hourly.pop_request()

        mock_cycle_start.assert_called_once()
        mock_notify.delay.assert_called_once()

    @patch(f"{DISPATCH_MODULE}.release_dispatch_lock")
    @patch(f"{DISPATCH_MODULE}.record_bucket_dispatched", return_value=False)
    @patch(f"{DISPATCH_MODULE}.compute_window_start")
    @patch(f"{DISPATCH_MODULE}.acquire_dispatch_lock", return_value=True)
    @patch(f"{DISPATCH_MODULE}.get_deferred_buckets", return_value=set())
    @patch(f"{DISPATCH_MODULE}.get_batch_store")
    @patch(f"{DISPATCH_MODULE}.get_sync_dispatch_service")
    @patch(f"{DISPATCH_MODULE}.record_cycle_start", return_value=False)
    def test_subsequent_bucket_skips_notification(
        self,
        mock_cycle_start,
        mock_get_svc,
        mock_get_bs,
        mock_deferred,
        mock_acquire,
        mock_window,
        mock_record_bucket,
        mock_release,
    ):
        mock_svc = MagicMock()
        mock_svc.is_extraction_session_active.return_value = False
        mock_svc.dispatch_bucket.return_value = {
            "bucket": 5, "batches_dispatched": 1,
            "products_dispatched": 10, "skus_deduped": 0,
        }
        mock_svc._store.reset_stuck_products.return_value = 0
        mock_get_svc.return_value = mock_svc
        mock_window.return_value = datetime(2026, 3, 23, 6, 0, tzinfo=timezone.utc)

        from app.celery_app.tasks.sync_dispatch import dispatch_hourly
        dispatch_hourly.push_request(id="test-task")
        try:
            result = dispatch_hourly()
        finally:
            dispatch_hourly.pop_request()

        mock_cycle_start.assert_called_once()
        # record_cycle_start returned False, so the lazy import of
        # send_cycle_start_notification never executes — no notification sent.
        assert result["status"] == "completed"

    @patch(f"{DISPATCH_MODULE}.release_dispatch_lock")
    @patch(f"{DISPATCH_MODULE}.record_bucket_dispatched", return_value=False)
    @patch(f"{DISPATCH_MODULE}.compute_window_start")
    @patch(f"{DISPATCH_MODULE}.acquire_dispatch_lock", return_value=True)
    @patch(f"{DISPATCH_MODULE}.get_deferred_buckets", return_value=set())
    @patch(f"{DISPATCH_MODULE}.get_batch_store")
    @patch(f"{DISPATCH_MODULE}.get_sync_dispatch_service")
    @patch(f"{DISPATCH_MODULE}.record_cycle_start", side_effect=Exception("Redis down"))
    def test_cycle_start_failure_is_non_fatal(
        self,
        mock_cycle_start,
        mock_get_svc,
        mock_get_bs,
        mock_deferred,
        mock_acquire,
        mock_window,
        mock_record_bucket,
        mock_release,
    ):
        mock_svc = MagicMock()
        mock_svc.is_extraction_session_active.return_value = False
        mock_svc.dispatch_bucket.return_value = {
            "bucket": 0, "batches_dispatched": 1,
            "products_dispatched": 10, "skus_deduped": 0,
        }
        mock_svc._store.reset_stuck_products.return_value = 0
        mock_get_svc.return_value = mock_svc
        mock_window.return_value = datetime(2026, 3, 23, 6, 0, tzinfo=timezone.utc)

        from app.celery_app.tasks.sync_dispatch import dispatch_hourly
        dispatch_hourly.push_request(id="test-task")
        try:
            # Should NOT raise despite record_cycle_start failing
            result = dispatch_hourly()
        finally:
            dispatch_hourly.pop_request()

        assert result["status"] == "completed"
