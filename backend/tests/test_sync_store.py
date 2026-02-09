"""
Unit tests for sync_store.py.

Tests cover:
- CRUD operations for sync schedules
- Slot distribution queries
- Status tracking and updates
- Retry management
- New inventory status and location tracking fields
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

# We'll need to patch settings before importing sync_store
@pytest.fixture(autouse=True)
def mock_settings_module():
    """Mock the settings module before importing sync_store."""
    mock_settings = MagicMock()
    mock_settings.sync_mode = "testing"
    mock_settings.sync_test_bucket_count = 6
    mock_settings.sync_batch_size = 10
    mock_settings.sync_max_failures = 5
    mock_settings.sync_max_buckets = 6
    mock_settings.supabase_url = "https://test.supabase.co"
    mock_settings.supabase_key = "test-key"

    with patch.dict("sys.modules", {"app.core.config": MagicMock(settings=mock_settings)}):
        with patch("app.core.config.settings", mock_settings):
            yield mock_settings


class TestSyncStoreSlotCounts:
    """Test slot counting functionality."""

    def test_get_slot_counts_empty(self, mock_supabase_client):
        """Test slot counts with no products."""
        from app.db.sync_store import SyncStore

        mock_supabase_client.execute.return_value = MagicMock(data=[])

        with patch.object(SyncStore, "client", mock_supabase_client):
            store = SyncStore()
            store._supabase_client = MagicMock(client=mock_supabase_client)
            counts = store.get_slot_counts()

        assert counts == {}

    def test_get_slot_counts_multiple_buckets(self, mock_supabase_client):
        """Test slot counts across multiple buckets."""
        from app.db.sync_store import SyncStore

        mock_supabase_client.execute.return_value = MagicMock(data=[
            {"hour_bucket": 0},
            {"hour_bucket": 0},
            {"hour_bucket": 0},
            {"hour_bucket": 1},
            {"hour_bucket": 1},
            {"hour_bucket": 2},
        ])

        store = SyncStore()
        # Override client property
        store._supabase_client = MagicMock()
        store._supabase_client.client = mock_supabase_client

        with patch.object(type(store), "client", property(lambda self: mock_supabase_client)):
            counts = store.get_slot_counts()

        assert counts == {0: 3, 1: 2, 2: 1}


class TestSyncStoreLeastLoadedSlot:
    """Test least-loaded slot allocation."""

    def test_least_loaded_slot_empty(self, mock_supabase_client):
        """Test allocation with no existing products."""
        from app.db.sync_store import SyncStore

        mock_supabase_client.execute.return_value = MagicMock(data=[])

        store = SyncStore()
        store._supabase_client = MagicMock()
        store._supabase_client.client = mock_supabase_client

        with patch.object(type(store), "client", property(lambda self: mock_supabase_client)):
            slot = store.get_least_loaded_slot()

        assert slot == 0

    def test_least_loaded_slot_balances(self, mock_supabase_client):
        """Test that allocation balances across slots."""
        from app.db.sync_store import SyncStore

        # Slot 0 has 10, slot 1 has 5
        mock_supabase_client.execute.return_value = MagicMock(data=[
            *[{"hour_bucket": 0} for _ in range(10)],
            *[{"hour_bucket": 1} for _ in range(5)],
        ])

        store = SyncStore()
        store._supabase_client = MagicMock()
        store._supabase_client.client = mock_supabase_client

        with patch.object(type(store), "client", property(lambda self: mock_supabase_client)):
            slot = store.get_least_loaded_slot()

        # Should pick slot 1 (5 products) over slot 0 (10 products)
        assert slot == 1


class TestSyncStoreCreateSchedule:
    """Test sync schedule creation."""

    def test_create_sync_schedule_auto_slot(self, mock_supabase_client):
        """Test creating schedule with auto slot allocation."""
        from app.db.sync_store import SyncStore

        mock_supabase_client.execute.return_value = MagicMock(data=[{
            "sku": "TEST123",
            "user_id": "user-1",
            "hour_bucket": 0,
            "sync_status": "pending",
        }])

        store = SyncStore()
        store._supabase_client = MagicMock()
        store._supabase_client.client = mock_supabase_client

        with patch.object(type(store), "client", property(lambda self: mock_supabase_client)):
            result = store.create_sync_schedule(
                sku="TEST123",
                user_id="user-1",
                initial_price=100.0,
                initial_quantity=10,
            )

        assert result["sku"] == "TEST123"
        mock_supabase_client.table.assert_called()

    def test_create_sync_schedule_explicit_slot(self, mock_supabase_client):
        """Test creating schedule with explicit slot."""
        from app.db.sync_store import SyncStore

        mock_supabase_client.execute.return_value = MagicMock(data=[{
            "sku": "TEST123",
            "hour_bucket": 5,
        }])

        store = SyncStore()
        store._supabase_client = MagicMock()
        store._supabase_client.client = mock_supabase_client

        with patch.object(type(store), "client", property(lambda self: mock_supabase_client)):
            result = store.create_sync_schedule(
                sku="TEST123",
                user_id="user-1",
                hour_bucket=5,  # Explicit slot
            )

        assert result["hour_bucket"] == 5


class TestSyncStoreUpdateSuccess:
    """Test sync success updates."""

    def test_update_sync_success_basic(self, mock_supabase_client):
        """Test basic success update."""
        from app.db.sync_store import SyncStore

        mock_supabase_client.execute.return_value = MagicMock(data=[{"sku": "TEST123"}])

        store = SyncStore()
        store._supabase_client = MagicMock()
        store._supabase_client.client = mock_supabase_client

        with patch.object(type(store), "client", property(lambda self: mock_supabase_client)):
            result = store.update_sync_success(
                sku="TEST123",
                new_hash="abc123",
                new_price=100.0,
                new_quantity=10,
            )

        assert result is True
        mock_supabase_client.update.assert_called()

    def test_update_sync_success_with_status(self, mock_supabase_client):
        """Test success update with inventory status."""
        from app.db.sync_store import SyncStore

        mock_supabase_client.execute.return_value = MagicMock(data=[{"sku": "TEST123"}])

        store = SyncStore()
        store._supabase_client = MagicMock()
        store._supabase_client.client = mock_supabase_client

        with patch.object(type(store), "client", property(lambda self: mock_supabase_client)):
            result = store.update_sync_success(
                sku="TEST123",
                new_hash="abc123",
                new_price=100.0,
                new_quantity=10,
                inventory_status="in_stock",
                location_summary="Dallas: 10",
            )

        assert result is True

    def test_update_sync_success_out_of_stock(self, mock_supabase_client):
        """Test success update for out-of-stock product."""
        from app.db.sync_store import SyncStore

        mock_supabase_client.execute.return_value = MagicMock(data=[{"sku": "TEST123"}])

        store = SyncStore()
        store._supabase_client = MagicMock()
        store._supabase_client.client = mock_supabase_client

        with patch.object(type(store), "client", property(lambda self: mock_supabase_client)):
            result = store.update_sync_success(
                sku="TEST123",
                new_hash="abc123",
                new_price=None,  # No price for out-of-stock
                new_quantity=0,
                inventory_status="out_of_stock",
                location_summary=None,
            )

        assert result is True


class TestSyncStoreUpdateFailure:
    """Test sync failure updates."""

    def test_update_sync_failure_increments_count(self, mock_supabase_client):
        """Test that failure increments counter."""
        from app.db.sync_store import SyncStore

        # First call returns current failures
        mock_supabase_client.execute.return_value = MagicMock(data=[
            {"consecutive_failures": 2}
        ])

        store = SyncStore()
        store._supabase_client = MagicMock()
        store._supabase_client.client = mock_supabase_client

        with patch.object(type(store), "client", property(lambda self: mock_supabase_client)):
            result = store.update_sync_failure(
                sku="TEST123",
                error_message="API timeout",
            )

        assert result["consecutive_failures"] == 3
        assert result["is_active"] is True

    def test_update_sync_failure_deactivates_after_max(self, mock_supabase_client):
        """Test that product is deactivated after max failures."""
        from app.db.sync_store import SyncStore

        # Currently at 4 failures (max is 5)
        mock_supabase_client.execute.return_value = MagicMock(data=[
            {"consecutive_failures": 4}
        ])

        store = SyncStore()
        store._supabase_client = MagicMock()
        store._supabase_client.client = mock_supabase_client

        with patch.object(type(store), "client", property(lambda self: mock_supabase_client)):
            result = store.update_sync_failure(
                sku="TEST123",
                error_message="API timeout",
            )

        assert result["consecutive_failures"] == 5
        assert result["is_active"] is False


class TestSyncStoreGetProducts:
    """Test product retrieval methods."""

    def test_get_products_for_hour(self, mock_supabase_client):
        """Test getting products for a specific hour."""
        from app.db.sync_store import SyncStore

        mock_supabase_client.execute.return_value = MagicMock(data=[
            {"sku": "SKU1", "hour_bucket": 2},
            {"sku": "SKU2", "hour_bucket": 2},
        ])

        store = SyncStore()
        store._supabase_client = MagicMock()
        store._supabase_client.client = mock_supabase_client

        with patch.object(type(store), "client", property(lambda self: mock_supabase_client)):
            products = store.get_products_for_hour(hour_bucket=2)

        assert len(products) == 2
        mock_supabase_client.eq.assert_called()

    def test_get_products_for_hour_with_status_filter(self, mock_supabase_client):
        """Test getting products with status filter."""
        from app.db.sync_store import SyncStore

        mock_supabase_client.execute.return_value = MagicMock(data=[
            {"sku": "SKU1", "sync_status": "pending"},
        ])

        store = SyncStore()
        store._supabase_client = MagicMock()
        store._supabase_client.client = mock_supabase_client

        with patch.object(type(store), "client", property(lambda self: mock_supabase_client)):
            products = store.get_products_for_hour(
                hour_bucket=2,
                status_filter=["pending", "success"],
            )

        assert len(products) == 1
        mock_supabase_client.in_.assert_called()

    def test_get_products_by_skus(self, mock_supabase_client):
        """Test getting products by SKU list."""
        from app.db.sync_store import SyncStore

        mock_supabase_client.execute.return_value = MagicMock(data=[
            {"sku": "SKU1"},
            {"sku": "SKU2"},
        ])

        store = SyncStore()
        store._supabase_client = MagicMock()
        store._supabase_client.client = mock_supabase_client

        with patch.object(type(store), "client", property(lambda self: mock_supabase_client)):
            products = store.get_products_by_skus(["SKU1", "SKU2"])

        assert len(products) == 2

    def test_get_products_by_skus_empty(self, mock_supabase_client):
        """Test getting products with empty SKU list."""
        from app.db.sync_store import SyncStore

        store = SyncStore()
        products = store.get_products_by_skus([])

        assert products == []


class TestSyncStoreRetryManagement:
    """Test retry-related functionality."""

    def test_get_failed_products_for_retry(self, mock_supabase_client):
        """Test getting failed products eligible for retry."""
        from app.db.sync_store import SyncStore

        mock_supabase_client.execute.return_value = MagicMock(data=[
            {"sku": "FAILED1", "sync_status": "failed", "consecutive_failures": 2},
            {"sku": "FAILED2", "sync_status": "failed", "consecutive_failures": 1},
        ])

        store = SyncStore()
        store._supabase_client = MagicMock()
        store._supabase_client.client = mock_supabase_client

        with patch.object(type(store), "client", property(lambda self: mock_supabase_client)):
            products = store.get_failed_products_for_retry(limit=100)

        assert len(products) == 2

    def test_reactivate_product(self, mock_supabase_client):
        """Test reactivating an inactive product."""
        from app.db.sync_store import SyncStore

        mock_supabase_client.execute.return_value = MagicMock(data=[{"sku": "TEST123"}])

        store = SyncStore()
        store._supabase_client = MagicMock()
        store._supabase_client.client = mock_supabase_client

        with patch.object(type(store), "client", property(lambda self: mock_supabase_client)):
            result = store.reactivate_product("TEST123")

        assert result is True


class TestSyncStoreStatusSummary:
    """Test status summary functionality."""

    def test_get_sync_status_summary(self, mock_supabase_client):
        """Test getting overall sync status summary."""
        from app.db.sync_store import SyncStore

        mock_supabase_client.execute.return_value = MagicMock(data=[
            {"sync_status": "success", "is_active": True, "consecutive_failures": 0},
            {"sync_status": "success", "is_active": True, "consecutive_failures": 0},
            {"sync_status": "failed", "is_active": True, "consecutive_failures": 3},
            {"sync_status": "pending", "is_active": False, "consecutive_failures": 5},
        ])

        store = SyncStore()
        store._supabase_client = MagicMock()
        store._supabase_client.client = mock_supabase_client

        with patch.object(type(store), "client", property(lambda self: mock_supabase_client)):
            summary = store.get_sync_status_summary()

        assert summary["total_products"] == 4
        assert summary["active_products"] == 3
        assert summary["inactive_products"] == 1
        assert summary["high_failure_count"] == 2  # 3+ failures

    def test_get_slot_distribution_summary(self, mock_supabase_client):
        """Test getting slot distribution summary."""
        from app.db.sync_store import SyncStore

        mock_supabase_client.execute.return_value = MagicMock(data=[
            *[{"hour_bucket": 0} for _ in range(12)],  # Active
            *[{"hour_bucket": 1} for _ in range(5)],   # Filling
        ])

        store = SyncStore()
        store._supabase_client = MagicMock()
        store._supabase_client.client = mock_supabase_client

        with patch.object(type(store), "client", property(lambda self: mock_supabase_client)):
            summary = store.get_slot_distribution_summary()

        assert summary["total_products"] == 17
        assert summary["active_count"] == 1
        assert summary["filling_count"] == 1


class TestSyncStoreSingleton:
    """Test singleton pattern for SyncStore."""

    def test_get_sync_store_returns_same_instance(self):
        """Test that get_sync_store returns singleton."""
        from app.db.sync_store import get_sync_store, reset_sync_store

        # Reset first
        reset_sync_store()

        store1 = get_sync_store()
        store2 = get_sync_store()

        assert store1 is store2

    def test_reset_sync_store_clears_singleton(self):
        """Test that reset clears the singleton."""
        from app.db.sync_store import get_sync_store, reset_sync_store

        store1 = get_sync_store()
        reset_sync_store()
        store2 = get_sync_store()

        assert store1 is not store2
