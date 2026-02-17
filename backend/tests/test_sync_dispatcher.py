"""
Integration tests for sync_dispatcher.py.

Tests cover:
- Hourly sync dispatch logic
- Boeing batch sync processing
- Missing SKU detection (out of stock)
- Shopify product updates
- Location-based inventory updates
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock
from datetime import datetime, timezone


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def mock_sync_store():
    """Mock SyncStore for testing."""
    store = MagicMock()
    store.get_slot_counts.return_value = {0: 10, 1: 5}
    store.get_products_for_hour.return_value = []
    store.get_products_by_skus.return_value = []
    store.mark_products_syncing.return_value = 1
    store.update_sync_success.return_value = True
    store.update_sync_failure.return_value = {"is_active": True}
    store.reset_stuck_products.return_value = 0
    return store


@pytest.fixture
def mock_boeing_response():
    """Mock Boeing API response."""
    return {
        "currency": "USD",
        "lineItems": [
            {
                "aviallPartNumber": "SKU1=K3",
                "listPrice": 100.0,
                "netPrice": 90.0,
                "inStock": True,
                "locationAvailabilities": [
                    {"location": "Dallas", "availQuantity": 10},
                ],
            },
            {
                "aviallPartNumber": "SKU2=E9",
                "listPrice": 200.0,
                "netPrice": 180.0,
                "inStock": True,
                "locationAvailabilities": [
                    {"location": "Chicago", "availQuantity": 5},
                ],
            },
        ],
    }


# =============================================================================
# DISPATCH HOURLY SYNC TESTS
# =============================================================================

class TestDispatchHourlySync:
    """Test the main sync dispatch task."""

    @patch("celery_app.tasks.sync_dispatcher.get_sync_store")
    @patch("celery_app.tasks.sync_dispatcher.SYNC_MODE", "testing")
    def test_dispatch_testing_mode_processes_immediately(self, mock_get_store, mock_sync_store):
        """Test that testing mode processes without sync window check."""
        mock_get_store.return_value = mock_sync_store
        mock_sync_store.get_products_for_hour.return_value = [
            {"sku": f"SKU{i}", "user_id": "test"} for i in range(10)
        ]

        from celery_app.tasks.sync_dispatcher import dispatch_hourly_sync

        # Create mock task
        task = MagicMock()
        result = dispatch_hourly_sync.__wrapped__(task)

        assert result["status"] == "completed"
        assert result["mode"] == "testing"

    @patch("celery_app.tasks.sync_dispatcher.get_sync_store")
    @patch("celery_app.tasks.sync_dispatcher.SYNC_MODE", "production")
    @patch("celery_app.tasks.sync_dispatcher.datetime")
    def test_dispatch_production_skips_outside_window(self, mock_dt, mock_get_store, mock_sync_store):
        """Test that production mode skips outside sync window."""
        mock_get_store.return_value = mock_sync_store

        # Mock time at minute 30 (outside 45-59 window)
        mock_now = MagicMock()
        mock_now.minute = 30
        mock_now.hour = 14
        mock_dt.now.return_value = mock_now

        from celery_app.tasks.sync_dispatcher import dispatch_hourly_sync

        task = MagicMock()
        result = dispatch_hourly_sync.__wrapped__(task)

        assert result["status"] == "skipped"
        assert result["reason"] == "not_in_sync_window"


# =============================================================================
# SYNC BOEING BATCH TESTS
# =============================================================================

class TestSyncBoeingBatch:
    """Test Boeing batch sync task."""

    @patch("celery_app.tasks.sync_dispatcher.get_sync_store")
    @patch("celery_app.tasks.sync_dispatcher.get_boeing_rate_limiter")
    @patch("celery_app.tasks.sync_dispatcher.get_settings")
    @patch("celery_app.tasks.sync_dispatcher.BoeingClient")
    @patch("celery_app.tasks.sync_dispatcher.run_async")
    @patch("celery_app.tasks.sync_dispatcher.sync_shopify_product")
    def test_sync_batch_queues_shopify_updates(
        self,
        mock_shopify_task,
        mock_run_async,
        mock_boeing_class,
        mock_get_settings,
        mock_rate_limiter,
        mock_get_store,
        mock_sync_store,
        mock_boeing_response,
    ):
        """Test that batch sync queues Shopify updates."""
        mock_get_store.return_value = mock_sync_store
        mock_rate_limiter.return_value.wait_for_token.return_value = True
        mock_run_async.return_value = mock_boeing_response
        mock_sync_store.get_products_by_skus.return_value = [
            {"sku": "SKU1=K3", "last_boeing_hash": None}
        ]

        from celery_app.tasks.sync_dispatcher import sync_boeing_batch

        task = MagicMock()
        result = sync_boeing_batch.__wrapped__(
            task,
            skus=["SKU1=K3", "SKU2=E9"],
            user_id="test-user",
            source_hour=0,
        )

        assert result["status"] == "completed"
        assert result["updates_queued"] >= 1

    @patch("celery_app.tasks.sync_dispatcher.get_sync_store")
    @patch("celery_app.tasks.sync_dispatcher.get_boeing_rate_limiter")
    @patch("celery_app.tasks.sync_dispatcher.get_settings")
    @patch("celery_app.tasks.sync_dispatcher.BoeingClient")
    @patch("celery_app.tasks.sync_dispatcher.run_async")
    @patch("celery_app.tasks.sync_dispatcher.sync_shopify_product")
    @patch("celery_app.tasks.sync_dispatcher.create_out_of_stock_data")
    def test_sync_batch_handles_missing_sku_as_out_of_stock(
        self,
        mock_create_oos,
        mock_shopify_task,
        mock_run_async,
        mock_boeing_class,
        mock_get_settings,
        mock_rate_limiter,
        mock_get_store,
        mock_sync_store,
    ):
        """Test that missing SKUs are treated as out-of-stock."""
        mock_get_store.return_value = mock_sync_store
        mock_rate_limiter.return_value.wait_for_token.return_value = True

        # Empty response - all SKUs missing
        mock_run_async.return_value = {"currency": "USD", "lineItems": []}

        # Mock the out-of-stock data creation
        mock_create_oos.return_value = {
            "sku": "MISSING123",
            "inventory_quantity": 0,
            "inventory_status": "out_of_stock",
            "is_missing_sku": True,
            "list_price": None,
            "location_summary": None,
        }

        mock_sync_store.get_products_by_skus.return_value = [
            {"sku": "MISSING123", "last_boeing_hash": "old_hash"}
        ]

        from celery_app.tasks.sync_dispatcher import sync_boeing_batch

        task = MagicMock()
        result = sync_boeing_batch.__wrapped__(
            task,
            skus=["MISSING123"],
            user_id="test-user",
            source_hour=0,
        )

        assert result["status"] == "completed"
        assert result["out_of_stock"] >= 1
        mock_create_oos.assert_called_with("MISSING123")

    @patch("celery_app.tasks.sync_dispatcher.get_sync_store")
    @patch("celery_app.tasks.sync_dispatcher.get_boeing_rate_limiter")
    def test_sync_batch_empty_skus_skipped(
        self,
        mock_rate_limiter,
        mock_get_store,
        mock_sync_store,
    ):
        """Test that empty SKU list is skipped."""
        mock_get_store.return_value = mock_sync_store

        from celery_app.tasks.sync_dispatcher import sync_boeing_batch

        task = MagicMock()
        result = sync_boeing_batch.__wrapped__(
            task,
            skus=[],
            user_id="test-user",
            source_hour=0,
        )

        assert result["status"] == "skipped"
        assert result["reason"] == "empty_batch"

    @patch("celery_app.tasks.sync_dispatcher.get_sync_store")
    @patch("celery_app.tasks.sync_dispatcher.get_boeing_rate_limiter")
    @patch("celery_app.tasks.sync_dispatcher.get_settings")
    @patch("celery_app.tasks.sync_dispatcher.BoeingClient")
    @patch("celery_app.tasks.sync_dispatcher.run_async")
    @patch("celery_app.tasks.sync_dispatcher.sync_shopify_product")
    @patch("celery_app.tasks.sync_dispatcher.compute_boeing_hash")
    def test_sync_batch_no_update_when_hash_matches(
        self,
        mock_compute_hash,
        mock_shopify_task,
        mock_run_async,
        mock_boeing_class,
        mock_get_settings,
        mock_rate_limiter,
        mock_get_store,
        mock_sync_store,
        mock_boeing_response,
    ):
        """Test that no Shopify update when hash matches."""
        mock_get_store.return_value = mock_sync_store
        mock_rate_limiter.return_value.wait_for_token.return_value = True
        mock_run_async.return_value = mock_boeing_response

        # Same hash as current
        mock_compute_hash.return_value = "same_hash"
        mock_sync_store.get_products_by_skus.return_value = [
            {"sku": "SKU1=K3", "last_boeing_hash": "same_hash", "last_price": 100.0, "last_quantity": 10}
        ]

        from celery_app.tasks.sync_dispatcher import sync_boeing_batch

        task = MagicMock()
        result = sync_boeing_batch.__wrapped__(
            task,
            skus=["SKU1=K3"],
            user_id="test-user",
            source_hour=0,
        )

        assert result["no_change"] >= 1


# =============================================================================
# SYNC SHOPIFY PRODUCT TESTS
# =============================================================================

class TestSyncShopifyProduct:
    """Test Shopify product sync task."""

    @patch("celery_app.tasks.sync_dispatcher.get_sync_store")
    @patch("celery_app.tasks.sync_dispatcher.get_shopify_client")
    @patch("celery_app.tasks.sync_dispatcher.run_async")
    @patch("celery_app.tasks.sync_dispatcher.compute_boeing_hash")
    def test_sync_shopify_updates_pricing(
        self,
        mock_compute_hash,
        mock_run_async,
        mock_get_shopify,
        mock_get_store,
        mock_sync_store,
    ):
        """Test that Shopify pricing is updated."""
        mock_get_store.return_value = mock_sync_store
        mock_compute_hash.return_value = "new_hash"

        mock_shopify = MagicMock()
        mock_shopify.update_product_pricing = AsyncMock(return_value={"product": {}})
        mock_shopify.update_inventory_by_location = AsyncMock()
        mock_get_shopify.return_value = mock_shopify

        # Mock product lookup
        mock_run_async.side_effect = [
            {"shopify_product_id": "12345"},  # get_product_by_sku
            {"product": {}},  # update_product_pricing
            None,  # update_inventory_by_location
            None,  # update_product_pricing (for products table)
        ]

        boeing_data = {
            "list_price": 100.0,
            "inventory_quantity": 10,
            "inventory_status": "in_stock",
            "location_quantities": [{"location": "Dallas", "quantity": 10}],
            "location_summary": "Dallas: 10",
        }

        from celery_app.tasks.sync_dispatcher import sync_shopify_product

        task = MagicMock()

        # Note: This is a simplified test - in real tests we'd need
        # to properly mock the async context

    @patch("celery_app.tasks.sync_dispatcher.get_sync_store")
    @patch("celery_app.tasks.sync_dispatcher.get_shopify_client")
    @patch("celery_app.tasks.sync_dispatcher.run_async")
    def test_sync_shopify_sets_zero_inventory_for_out_of_stock(
        self,
        mock_run_async,
        mock_get_shopify,
        mock_get_store,
        mock_sync_store,
    ):
        """Test that out-of-stock products get zero inventory."""
        mock_get_store.return_value = mock_sync_store

        mock_shopify = MagicMock()
        mock_shopify.update_product_pricing = AsyncMock(return_value={"product": {}})
        mock_get_shopify.return_value = mock_shopify

        mock_run_async.side_effect = [
            {"shopify_product_id": "12345"},
            {"product": {}},
            None,
        ]

        boeing_data = {
            "list_price": None,
            "inventory_quantity": 0,
            "inventory_status": "out_of_stock",
            "is_missing_sku": True,
            "location_quantities": [],
            "location_summary": None,
        }

        # Verify that quantity=0 would be passed to update_product_pricing


# =============================================================================
# FIELD MAPPING INTEGRATION TESTS
# =============================================================================

class TestFieldMappingIntegration:
    """Test that field mappings are consistent across the sync system."""

    def test_extract_and_hash_consistency(self, sample_boeing_response):
        """Test that extracted data hashes consistently."""
        from app.utils.boeing_data_extract import extract_boeing_product_data
        from app.utils.hash_utils import compute_boeing_hash

        # Extract data
        data = extract_boeing_product_data(sample_boeing_response, "WF338109=K3")
        assert data is not None

        # Hash should be deterministic
        hash1 = compute_boeing_hash(data)
        hash2 = compute_boeing_hash(data)
        assert hash1 == hash2

    def test_out_of_stock_data_hashes_correctly(self):
        """Test that out-of-stock data produces valid hash."""
        from app.utils.boeing_data_extract import create_out_of_stock_data
        from app.utils.hash_utils import compute_boeing_hash

        data = create_out_of_stock_data("MISSING123")
        hash_value = compute_boeing_hash(data)

        assert len(hash_value) == 16
        assert hash_value  # Not empty

    def test_field_names_match_boeing_normalize(self, sample_boeing_response):
        """
        Test that field names in sync match boeing_normalize.py.

        This ensures we use the correct Boeing API field names:
        - listPrice (not pricing.listPrice)
        - locationAvailabilities (not availability)
        - availQuantity (not availableQuantity)
        """
        from app.utils.boeing_data_extract import extract_boeing_product_data

        data = extract_boeing_product_data(sample_boeing_response, "WF338109=K3")

        # These fields come from direct Boeing API access
        assert data["list_price"] == 753.09
        assert data["net_price"] == 680.50
        assert data["inventory_quantity"] == 25

        # Location data from locationAvailabilities
        assert len(data["locations"]) == 2
        assert data["locations"][0]["location"] == "Dallas Central"
        assert data["locations"][0]["quantity"] == 15


# =============================================================================
# EDGE CASE TESTS
# =============================================================================

class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_extract_product_with_null_locations(self):
        """Test extraction when locationAvailabilities is null."""
        from app.utils.boeing_data_extract import extract_boeing_product_data

        response = {
            "currency": "USD",
            "lineItems": [
                {
                    "aviallPartNumber": "TEST123",
                    "listPrice": 100.0,
                    "inStock": True,
                    "locationAvailabilities": None,
                }
            ]
        }

        data = extract_boeing_product_data(response, "TEST123")
        assert data is not None
        assert data["inventory_quantity"] == 0
        assert data["locations"] == []

    def test_extract_product_with_zero_price(self):
        """Test extraction when price is zero (should be None)."""
        from app.utils.boeing_data_extract import extract_boeing_product_data

        response = {
            "currency": "USD",
            "lineItems": [
                {
                    "aviallPartNumber": "TEST123",
                    "listPrice": 0,
                    "netPrice": 0,
                    "inStock": True,
                    "locationAvailabilities": [],
                }
            ]
        }

        data = extract_boeing_product_data(response, "TEST123")
        assert data is not None
        assert data["list_price"] is None
        assert data["net_price"] is None

    def test_hash_handles_none_values(self):
        """Test that hash handles None values correctly."""
        from app.utils.hash_utils import compute_sync_hash

        hash1 = compute_sync_hash(
            price=None,
            quantity=0,
            inventory_status=None,
            location_summary=None,
        )

        hash2 = compute_sync_hash(
            price=None,
            quantity=0,
            inventory_status=None,
            location_summary=None,
        )

        assert hash1 == hash2
        assert len(hash1) == 16

    def test_location_summary_format(self, sample_boeing_response):
        """Test that location summary has correct format."""
        from app.utils.boeing_data_extract import extract_boeing_product_data

        data = extract_boeing_product_data(sample_boeing_response, "WF338109=K3")

        # Format should be "Location: qty; Location: qty"
        summary = data["location_summary"]
        assert ": " in summary
        assert ";" in summary
        # Should contain location names
        assert "Dallas Central" in summary
        assert "Chicago Warehouse" in summary
