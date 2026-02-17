"""
Unit tests for SyncAnalytics — dashboard queries for slot distribution and status.

Tests cover:
- get_slot_distribution_summary classifies slots as active/filling/dormant
- get_slot_distribution_summary computes efficiency metrics
- get_slot_distribution_summary handles empty data
- get_slot_distribution_summary handles database errors
- get_sync_status_summary counts products by status
- get_sync_status_summary counts active/inactive products
- get_sync_status_summary tracks high failure products
- get_sync_status_summary computes success rate
- get_sync_status_summary handles empty data
- get_sync_status_summary handles database errors
- client property lazy-initializes SupabaseClient

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


def _make_analytics(table_data=None):
    """Create a SyncAnalytics with a mocked SupabaseClient.

    Args:
        table_data: list of dicts to return from .execute().data
    """
    from app.db.sync_analytics import SyncAnalytics

    mock_supabase_client = MagicMock()
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=table_data or [])
    mock_supabase_client.client.table.return_value = mock_table

    analytics = SyncAnalytics(supabase_client=mock_supabase_client)
    return analytics, mock_table


@pytest.mark.unit
class TestGetSlotDistributionSummary:
    """Verify get_slot_distribution_summary slot classification and metrics."""

    @patch("app.db.sync_analytics.MAX_SKUS_PER_SLOT", 10)
    @patch("app.db.sync_analytics.MAX_BUCKETS", 6)
    def test_classifies_active_filling_dormant_slots(self):
        # Slot 0: 15 products (active), Slot 1: 5 products (filling), Slots 2-5: 0 (dormant)
        rows = (
            [{"hour_bucket": 0}] * 15
            + [{"hour_bucket": 1}] * 5
        )
        analytics, _ = _make_analytics(rows)

        result = analytics.get_slot_distribution_summary()

        assert result["active_count"] == 1
        assert result["filling_count"] == 1
        assert result["dormant_count"] == 4
        assert 0 in result["active_slots"]
        assert 1 in result["filling_slots"]
        assert result["total_products"] == 20

    @patch("app.db.sync_analytics.MAX_SKUS_PER_SLOT", 10)
    @patch("app.db.sync_analytics.MAX_BUCKETS", 6)
    def test_all_dormant_when_no_products(self):
        analytics, _ = _make_analytics([])

        result = analytics.get_slot_distribution_summary()

        assert result["total_products"] == 0
        assert result["active_count"] == 0
        assert result["filling_count"] == 0
        assert result["dormant_count"] == 6
        assert result["optimal_slots_needed"] == 0
        assert result["efficiency_percent"] == 100

    @patch("app.db.sync_analytics.MAX_SKUS_PER_SLOT", 10)
    @patch("app.db.sync_analytics.MAX_BUCKETS", 6)
    def test_efficiency_is_100_when_optimal_equals_actual(self):
        # 10 products in 1 slot = optimal 1 slot, actual 1 slot => 100%
        rows = [{"hour_bucket": 0}] * 10
        analytics, _ = _make_analytics(rows)

        result = analytics.get_slot_distribution_summary()

        assert result["optimal_slots_needed"] == 1
        assert result["efficiency_percent"] == 100.0

    @patch("app.db.sync_analytics.MAX_SKUS_PER_SLOT", 10)
    @patch("app.db.sync_analytics.MAX_BUCKETS", 6)
    def test_efficiency_below_100_when_spread_across_more_slots(self):
        # 10 products spread across 2 filling slots => optimal 1, actual 2 => 50%
        rows = [{"hour_bucket": 0}] * 5 + [{"hour_bucket": 1}] * 5
        analytics, _ = _make_analytics(rows)

        result = analytics.get_slot_distribution_summary()

        assert result["optimal_slots_needed"] == 1
        # actual_slots = active (0) + filling (2) = 2
        assert result["efficiency_percent"] == 50.0

    @patch("app.db.sync_analytics.MAX_SKUS_PER_SLOT", 10)
    @patch("app.db.sync_analytics.MAX_BUCKETS", 6)
    def test_slot_counts_dict_is_populated(self):
        rows = [{"hour_bucket": 0}] * 3 + [{"hour_bucket": 2}] * 7
        analytics, _ = _make_analytics(rows)

        result = analytics.get_slot_distribution_summary()

        assert result["slot_counts"][0] == 3
        assert result["slot_counts"][2] == 7

    @patch("app.db.sync_analytics.MAX_SKUS_PER_SLOT", 10)
    @patch("app.db.sync_analytics.MAX_BUCKETS", 6)
    def test_handles_database_error_gracefully(self):
        analytics, mock_table = _make_analytics([])
        mock_table.execute.side_effect = Exception("DB connection failed")

        # Should not raise; returns empty slot_counts
        result = analytics.get_slot_distribution_summary()

        assert result["total_products"] == 0
        assert result["dormant_count"] == 6


@pytest.mark.unit
class TestGetSyncStatusSummary:
    """Verify get_sync_status_summary product counting and success rate."""

    def test_counts_products_by_status(self):
        rows = [
            {"sync_status": "pending", "is_active": True, "consecutive_failures": 0},
            {"sync_status": "pending", "is_active": True, "consecutive_failures": 0},
            {"sync_status": "success", "is_active": True, "consecutive_failures": 0},
            {"sync_status": "success", "is_active": True, "consecutive_failures": 0},
            {"sync_status": "success", "is_active": True, "consecutive_failures": 0},
            {"sync_status": "failed", "is_active": False, "consecutive_failures": 5},
        ]
        analytics, _ = _make_analytics(rows)

        result = analytics.get_sync_status_summary()

        assert result["status_counts"]["pending"] == 2
        assert result["status_counts"]["success"] == 3
        assert result["status_counts"]["failed"] == 1
        assert result["status_counts"]["syncing"] == 0
        assert result["total_products"] == 6

    def test_counts_active_and_inactive(self):
        rows = [
            {"sync_status": "success", "is_active": True, "consecutive_failures": 0},
            {"sync_status": "success", "is_active": True, "consecutive_failures": 0},
            {"sync_status": "failed", "is_active": False, "consecutive_failures": 3},
        ]
        analytics, _ = _make_analytics(rows)

        result = analytics.get_sync_status_summary()

        assert result["active_products"] == 2
        assert result["inactive_products"] == 1

    def test_tracks_high_failure_count(self):
        rows = [
            {"sync_status": "failed", "is_active": True, "consecutive_failures": 3},
            {"sync_status": "failed", "is_active": True, "consecutive_failures": 5},
            {"sync_status": "success", "is_active": True, "consecutive_failures": 1},
        ]
        analytics, _ = _make_analytics(rows)

        result = analytics.get_sync_status_summary()

        # Only products with consecutive_failures >= 3
        assert result["high_failure_count"] == 2

    def test_computes_success_rate(self):
        rows = [
            {"sync_status": "success", "is_active": True, "consecutive_failures": 0},
            {"sync_status": "success", "is_active": True, "consecutive_failures": 0},
            {"sync_status": "failed", "is_active": True, "consecutive_failures": 1},
            {"sync_status": "pending", "is_active": True, "consecutive_failures": 0},
        ]
        analytics, _ = _make_analytics(rows)

        result = analytics.get_sync_status_summary()

        # 2 success out of 4 total = 50%
        assert result["success_rate_percent"] == 50.0

    def test_empty_data_returns_zero_success_rate(self):
        analytics, _ = _make_analytics([])

        result = analytics.get_sync_status_summary()

        assert result["total_products"] == 0
        assert result["success_rate_percent"] == 0

    def test_handles_database_error_returns_error_dict(self):
        analytics, mock_table = _make_analytics([])
        mock_table.execute.side_effect = Exception("Connection timeout")

        result = analytics.get_sync_status_summary()

        assert "error" in result
        assert "Connection timeout" in result["error"]

    def test_missing_sync_status_defaults_to_pending(self):
        rows = [
            {"is_active": True, "consecutive_failures": 0},  # no sync_status
        ]
        analytics, _ = _make_analytics(rows)

        result = analytics.get_sync_status_summary()

        assert result["status_counts"]["pending"] == 1


@pytest.mark.unit
class TestClientPropertyLazyInit:
    """Verify the client property lazy-initializes SupabaseClient."""

    def test_uses_provided_client(self):
        from app.db.sync_analytics import SyncAnalytics

        mock_supabase_client = MagicMock()
        analytics = SyncAnalytics(supabase_client=mock_supabase_client)

        _ = analytics.client

        # Should access .client on the provided supabase_client
        mock_supabase_client.client  # property access
        assert analytics._supabase_client is mock_supabase_client

    @patch("app.db.sync_analytics.SupabaseClient")
    @patch("app.db.sync_analytics.settings")
    def test_lazy_creates_client_when_none_provided(self, mock_settings, MockSupabaseClient):
        from app.db.sync_analytics import SyncAnalytics

        mock_instance = MagicMock()
        MockSupabaseClient.return_value = mock_instance

        analytics = SyncAnalytics(supabase_client=None)
        _ = analytics.client

        MockSupabaseClient.assert_called_once_with(mock_settings)
