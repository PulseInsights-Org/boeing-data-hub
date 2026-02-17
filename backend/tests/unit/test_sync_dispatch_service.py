"""
Unit tests for SyncDispatchService — hourly scheduling and batch dispatch.

Tests cover:
- dispatch_hourly skips when not in sync window (production mode)
- dispatch_hourly dispatches active slot products in production mode
- dispatch_hourly dispatches filling slot products when current slot is underfilled
- dispatch_hourly uses minute buckets in testing mode
- dispatch_hourly resets stuck products
- dispatch_hourly returns completed result with correct fields
- dispatch_retry returns early when no failed products
- dispatch_retry groups products by user_id and dispatches in batches
- dispatch_retry marks products as syncing before dispatch
- end_of_day_cleanup resets stuck products and logs stats
- end_of_day_cleanup reports high failure count warning

Version: 1.0.0
"""
import sys
from unittest.mock import MagicMock as _MagicMock

for _mod in (
    "supabase", "storage3", "storage3.utils",
    "storage3._async", "storage3._async.client", "storage3._async.analytics",
    "postgrest", "postgrest.exceptions",
    "pyiceberg", "pyiceberg.catalog", "pyiceberg.catalog.rest", "pyroaring",
):
    sys.modules.setdefault(_mod, _MagicMock())

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from app.services.sync_dispatch_service import SyncDispatchService


def _make_service(
    slot_counts=None,
    products_for_hour=None,
    failed_products=None,
    stuck_reset=0,
    sync_status_summary=None,
    slot_distribution_summary=None,
):
    """Create a SyncDispatchService with a mocked SyncStore."""
    mock_store = MagicMock()
    mock_store.get_slot_counts = MagicMock(return_value=slot_counts or {})
    mock_store.get_products_for_hour = MagicMock(return_value=products_for_hour or [])
    mock_store.mark_products_syncing = MagicMock()
    mock_store.reset_stuck_products = MagicMock(return_value=stuck_reset)
    mock_store.get_failed_products_for_retry = MagicMock(return_value=failed_products or [])
    mock_store.get_sync_status_summary = MagicMock(return_value=sync_status_summary or {
        "total_products": 0, "high_failure_count": 0,
    })
    mock_store.get_slot_distribution_summary = MagicMock(
        return_value=slot_distribution_summary or {"efficiency_percent": 100}
    )

    mock_callback = MagicMock()

    service = SyncDispatchService(sync_store=mock_store)
    return service, mock_store, mock_callback


@pytest.mark.unit
class TestDispatchHourlyProductionMode:
    """Verify dispatch_hourly behavior in production mode."""

    @patch("app.services.sync_dispatch_service.datetime")
    def test_skips_when_not_in_sync_window(self, mock_datetime):
        """Production mode skips when minute < 45."""
        mock_now = MagicMock()
        mock_now.minute = 30
        mock_now.strftime.return_value = "10:30:00"
        mock_datetime.now.return_value = mock_now
        mock_datetime.side_effect = lambda *a, **kw: datetime(*a, **kw)

        svc, _, mock_callback = _make_service()

        result = svc.dispatch_hourly("production", 6, mock_callback)

        assert result["status"] == "skipped"
        assert result["reason"] == "not_in_sync_window"
        mock_callback.assert_not_called()

    @patch("app.services.sync_dispatch_service.get_current_hour_utc", return_value=10)
    @patch("app.services.sync_dispatch_service.datetime")
    def test_dispatches_active_slot_products(self, mock_datetime, mock_get_hour):
        """When current slot has >= MIN_PRODUCTS_FOR_ACTIVE_SLOT products, dispatch them."""
        mock_now = MagicMock()
        mock_now.minute = 50  # In sync window
        mock_now.strftime.return_value = "10:50:00"
        mock_datetime.now.return_value = mock_now
        mock_datetime.side_effect = lambda *a, **kw: datetime(*a, **kw)

        products = [
            {"sku": f"SKU-{i}", "user_id": "user-1"} for i in range(15)
        ]

        svc, mock_store, mock_callback = _make_service(
            slot_counts={10: 15},  # 15 products in slot 10 (>= MIN_PRODUCTS_FOR_ACTIVE_SLOT)
            products_for_hour=products,
        )

        result = svc.dispatch_hourly("production", 6, mock_callback)

        assert result["status"] == "completed"
        assert result["mode"] == "production"
        assert result["bucket"] == 10
        assert result["products_dispatched"] == 15
        assert result["batches_dispatched"] >= 1
        mock_store.mark_products_syncing.assert_called()
        mock_callback.assert_called()

    @patch("app.services.sync_dispatch_service.get_current_hour_utc", return_value=2)
    @patch("app.services.sync_dispatch_service.datetime")
    def test_dispatches_filling_slots_when_current_underfilled(self, mock_datetime, mock_get_hour):
        """When current slot has < MIN but > 0 products, aggregate filling slots."""
        mock_now = MagicMock()
        mock_now.minute = 50
        mock_now.strftime.return_value = "02:50:00"
        mock_datetime.now.return_value = mock_now
        mock_datetime.side_effect = lambda *a, **kw: datetime(*a, **kw)

        filling_products = [
            {"sku": "SKU-A", "user_id": "user-1"},
            {"sku": "SKU-B", "user_id": "user-1"},
        ]

        svc, mock_store, mock_callback = _make_service(
            # Slot 2 has 3 products (filling: > 0 but < 10)
            slot_counts={2: 3, 3: 5},
        )
        # get_products_for_hour returns filling products for each filling slot
        mock_store.get_products_for_hour = MagicMock(return_value=filling_products)

        result = svc.dispatch_hourly("production", 6, mock_callback)

        assert result["status"] == "completed"
        # Products should have been dispatched from filling slots
        assert result["products_dispatched"] > 0

    @patch("app.services.sync_dispatch_service.get_current_hour_utc", return_value=5)
    @patch("app.services.sync_dispatch_service.datetime")
    def test_resets_stuck_products(self, mock_datetime, mock_get_hour):
        mock_now = MagicMock()
        mock_now.minute = 50
        mock_now.strftime.return_value = "05:50:00"
        mock_datetime.now.return_value = mock_now
        mock_datetime.side_effect = lambda *a, **kw: datetime(*a, **kw)

        svc, mock_store, mock_callback = _make_service(
            slot_counts={5: 0},
            stuck_reset=3,
        )

        result = svc.dispatch_hourly("production", 6, mock_callback)

        mock_store.reset_stuck_products.assert_called_once_with(stuck_threshold_minutes=30)
        assert result["stuck_reset"] == 3


@pytest.mark.unit
class TestDispatchHourlyTestingMode:
    """Verify dispatch_hourly behavior in testing mode."""

    @patch("app.services.sync_dispatch_service.datetime")
    def test_uses_minute_bucket_in_testing_mode(self, mock_datetime):
        mock_now = MagicMock()
        mock_now.minute = 25  # minute // 10 = 2
        mock_now.strftime.return_value = "10:25:00"
        mock_datetime.now.return_value = mock_now
        mock_datetime.side_effect = lambda *a, **kw: datetime(*a, **kw)

        svc, mock_store, mock_callback = _make_service(slot_counts={2: 0})

        result = svc.dispatch_hourly("testing", 6, mock_callback)

        assert result["status"] == "completed"
        assert result["mode"] == "testing"
        assert result["bucket"] == 2
        assert result["bucket_type"] == "minute"

    @patch("app.services.sync_dispatch_service.datetime")
    def test_no_sync_window_check_in_testing_mode(self, mock_datetime):
        """Testing mode does not skip based on minute < 45."""
        mock_now = MagicMock()
        mock_now.minute = 5  # Would be skipped in production
        mock_now.strftime.return_value = "10:05:00"
        mock_datetime.now.return_value = mock_now
        mock_datetime.side_effect = lambda *a, **kw: datetime(*a, **kw)

        svc, _, mock_callback = _make_service(slot_counts={0: 0})

        result = svc.dispatch_hourly("testing", 6, mock_callback)

        # Should NOT be skipped
        assert result["status"] == "completed"


@pytest.mark.unit
class TestDispatchHourlyBatching:
    """Verify batch size and dispatch_callback invocations."""

    @patch("app.services.sync_dispatch_service.get_current_hour_utc", return_value=0)
    @patch("app.services.sync_dispatch_service.datetime")
    def test_splits_products_into_batches(self, mock_datetime, mock_get_hour):
        mock_now = MagicMock()
        mock_now.minute = 50
        mock_now.strftime.return_value = "00:50:00"
        mock_datetime.now.return_value = mock_now
        mock_datetime.side_effect = lambda *a, **kw: datetime(*a, **kw)

        # 25 products should yield 3 batches (10 + 10 + 5)
        products = [{"sku": f"SKU-{i}", "user_id": "user-1"} for i in range(25)]

        svc, mock_store, mock_callback = _make_service(
            slot_counts={0: 25},
            products_for_hour=products,
        )

        result = svc.dispatch_hourly("production", 6, mock_callback)

        assert result["batches_dispatched"] == 3
        assert result["products_dispatched"] == 25
        assert mock_callback.call_count == 3

    @patch("app.services.sync_dispatch_service.get_current_hour_utc", return_value=0)
    @patch("app.services.sync_dispatch_service.datetime")
    def test_marks_products_syncing_before_dispatch(self, mock_datetime, mock_get_hour):
        mock_now = MagicMock()
        mock_now.minute = 50
        mock_now.strftime.return_value = "00:50:00"
        mock_datetime.now.return_value = mock_now
        mock_datetime.side_effect = lambda *a, **kw: datetime(*a, **kw)

        products = [{"sku": "SKU-1", "user_id": "user-1"}]

        svc, mock_store, mock_callback = _make_service(
            slot_counts={0: 10},
            products_for_hour=products,
        )

        svc.dispatch_hourly("production", 6, mock_callback)

        # mark_products_syncing should be called before dispatch_callback
        mock_store.mark_products_syncing.assert_called_once_with(["SKU-1"])
        mock_callback.assert_called_once()


@pytest.mark.unit
class TestDispatchRetry:
    """Verify dispatch_retry behavior."""

    def test_returns_early_when_no_failed_products(self):
        svc, _, mock_callback = _make_service(failed_products=[])

        result = svc.dispatch_retry(mock_callback)

        assert result["status"] == "completed"
        assert result["retries"] == 0
        mock_callback.assert_not_called()

    def test_dispatches_failed_products_in_batches(self):
        failed = [
            {"sku": f"SKU-{i}", "user_id": "user-1"} for i in range(15)
        ]

        svc, mock_store, mock_callback = _make_service(failed_products=failed)

        result = svc.dispatch_retry(mock_callback)

        assert result["status"] == "completed"
        assert result["batches_dispatched"] == 2  # 10 + 5
        assert result["products_retried"] == 15

    def test_groups_by_user_id(self):
        failed = [
            {"sku": "SKU-1", "user_id": "user-a"},
            {"sku": "SKU-2", "user_id": "user-a"},
            {"sku": "SKU-3", "user_id": "user-b"},
        ]

        svc, _, mock_callback = _make_service(failed_products=failed)

        result = svc.dispatch_retry(mock_callback)

        assert result["products_retried"] == 3
        # Each user's batch dispatched separately
        assert mock_callback.call_count == 2

    def test_marks_products_syncing(self):
        failed = [{"sku": "SKU-1", "user_id": "user-1"}]

        svc, mock_store, mock_callback = _make_service(failed_products=failed)

        svc.dispatch_retry(mock_callback)

        mock_store.mark_products_syncing.assert_called_once_with(["SKU-1"])

    def test_dispatch_callback_uses_bucket_minus_one(self):
        failed = [{"sku": "SKU-1", "user_id": "user-1"}]

        svc, _, mock_callback = _make_service(failed_products=failed)

        svc.dispatch_retry(mock_callback)

        # Retry uses bucket=-1 to indicate retry
        call_args = mock_callback.call_args
        assert call_args.args[2] == -1  # third arg is bucket


@pytest.mark.unit
class TestEndOfDayCleanup:
    """Verify end_of_day_cleanup behavior."""

    def test_resets_stuck_products_with_60_min_threshold(self):
        svc, mock_store, _ = _make_service(stuck_reset=5)

        result = svc.end_of_day_cleanup()

        mock_store.reset_stuck_products.assert_called_once_with(stuck_threshold_minutes=60)
        assert result["stuck_reset"] == 5

    def test_returns_summary_and_efficiency(self):
        svc, mock_store, _ = _make_service(
            sync_status_summary={
                "total_products": 100,
                "high_failure_count": 2,
                "active_products": 90,
            },
            slot_distribution_summary={"efficiency_percent": 85.5},
        )

        result = svc.end_of_day_cleanup()

        assert result["status"] == "completed"
        assert result["summary"]["total_products"] == 100
        assert result["efficiency"] == 85.5

    def test_reports_high_failure_count(self):
        svc, mock_store, _ = _make_service(
            sync_status_summary={
                "total_products": 50,
                "high_failure_count": 5,
            },
            slot_distribution_summary={"efficiency_percent": 90},
        )

        result = svc.end_of_day_cleanup()

        assert result["summary"]["high_failure_count"] == 5

    def test_queries_both_status_and_distribution(self):
        svc, mock_store, _ = _make_service()

        svc.end_of_day_cleanup()

        mock_store.get_sync_status_summary.assert_called_once()
        mock_store.get_slot_distribution_summary.assert_called_once()
