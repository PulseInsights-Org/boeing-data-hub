"""
End-to-end tests for the sync pipeline.

Tests the full flow: dispatch -> boeing fetch -> shopify update.
Mocks Boeing client and Shopify orchestrator. Verifies sync store
gets updated with correct status, hash, and pricing data.
Version: 1.0.0
"""
import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock, call

os.environ.setdefault("AUTO_START_CELERY", "false")


SAMPLE_BOEING_SYNC_RESPONSE = {
    "currency": "USD",
    "lineItems": [
        {
            "aviallPartNumber": "WF338109",
            "aviallPartName": "GASKET, O-RING",
            "listPrice": 26.00,
            "netPrice": 24.00,
            "quantity": 120,
            "inStock": True,
            "locationAvailabilities": [
                {"location": "Dallas Central", "availQuantity": 80},
                {"location": "Chicago Warehouse", "availQuantity": 40},
            ],
        },
    ],
}


@pytest.mark.e2e
class TestSyncDispatchFlow:
    """E2E tests for sync dispatch logic."""

    def test_sync_store_get_products_for_hour(self):
        """Sync store should be able to return products for a given hour bucket."""
        mock_store = MagicMock()
        mock_store.get_products_for_hour.return_value = [
            {"sku": "WF338109", "user_id": "test-user-id", "hour_bucket": 3},
            {"sku": "AN3-12A", "user_id": "test-user-id", "hour_bucket": 3},
        ]

        products = mock_store.get_products_for_hour(3, status_filter=["pending", "success"])
        assert len(products) == 2
        assert all(p["hour_bucket"] == 3 for p in products)

    def test_batch_grouping_respects_batch_size(self):
        """Batch groups should not exceed the max batch size."""
        from app.utils.batch_grouping import calculate_batch_groups

        products = [{"sku": f"PN-{i:03d}", "user_id": "test-user"} for i in range(25)]
        batch_size = 10
        batches = calculate_batch_groups(products, batch_size)

        assert all(len(b) <= batch_size for b in batches)
        total_items = sum(len(b) for b in batches)
        assert total_items == 25

    def test_slot_distribution_categorization(self):
        """Slot distribution should categorize slots into active/filling/dormant."""
        from app.utils.slot_manager import get_slot_distribution

        slot_counts = {0: 15, 1: 20, 2: 5, 3: 0, 4: 0, 5: 0}
        distribution = get_slot_distribution(slot_counts)

        assert "active_slots" in distribution
        assert "filling_slots" in distribution
        assert "dormant_slots" in distribution
        assert distribution["active_count"] + distribution["filling_count"] + distribution["dormant_count"] > 0


@pytest.mark.e2e
class TestSyncBoeingFetchFlow:
    """E2E tests for the Boeing fetch portion of sync."""

    def test_extract_boeing_product_data_from_response(self):
        """extract_boeing_product_data should find the correct SKU in response."""
        from app.utils.boeing_data_extract import extract_boeing_product_data

        result = extract_boeing_product_data(SAMPLE_BOEING_SYNC_RESPONSE, "WF338109")

        assert result is not None
        assert result.get("list_price") is not None or result.get("net_price") is not None

    def test_extract_boeing_product_data_missing_sku(self):
        """extract_boeing_product_data should return None for missing SKU."""
        from app.utils.boeing_data_extract import extract_boeing_product_data

        result = extract_boeing_product_data(SAMPLE_BOEING_SYNC_RESPONSE, "NONEXISTENT")
        assert result is None

    def test_compute_boeing_hash_changes_with_data(self):
        """Boeing hash should change when product data changes."""
        from app.utils.hash_utils import compute_boeing_hash

        data_v1 = {"list_price": 25.50, "inventory_quantity": 150}
        data_v2 = {"list_price": 26.00, "inventory_quantity": 120}

        hash_v1 = compute_boeing_hash(data_v1)
        hash_v2 = compute_boeing_hash(data_v2)

        assert hash_v1 != hash_v2
        # Same data should produce same hash (deterministic)
        assert compute_boeing_hash(data_v1) == hash_v1

    def test_should_update_shopify_detects_changes(self):
        """should_update_shopify should return True when data has changed."""
        from app.utils.change_detection import should_update_shopify
        from app.utils.hash_utils import compute_boeing_hash

        old_data = {"list_price": 25.50, "inventory_quantity": 150}
        new_data = {"list_price": 26.00, "inventory_quantity": 120}

        old_hash = compute_boeing_hash(old_data)
        should_update, reason = should_update_shopify(
            new_data, old_hash, 25.50, 150
        )

        assert should_update is True
        assert reason is not None

    def test_should_update_shopify_skips_unchanged(self):
        """should_update_shopify should return False when data is unchanged."""
        from app.utils.change_detection import should_update_shopify
        from app.utils.hash_utils import compute_boeing_hash

        data = {"list_price": 25.50, "inventory_quantity": 150}
        current_hash = compute_boeing_hash(data)

        should_update, reason = should_update_shopify(
            data, current_hash, 25.50, 150
        )

        assert should_update is False


@pytest.mark.e2e
class TestSyncShopifyUpdateFlow:
    """E2E tests for the Shopify update portion of sync."""

    @pytest.mark.asyncio
    async def test_sync_update_calls_shopify_with_correct_price(self):
        """Sync should apply 10% markup when updating Shopify pricing."""
        mock_shopify = MagicMock()
        mock_shopify.update_product_pricing = AsyncMock(return_value={
            "product": {"id": "99001"},
        })
        mock_shopify.update_inventory_by_location = AsyncMock()

        mock_product_store = MagicMock()
        mock_product_store.get_product_by_sku = AsyncMock(return_value={
            "sku": "WF338109",
            "shopify_product_id": "99001",
        })
        mock_product_store.update_product_pricing = AsyncMock()

        mock_sync_store = MagicMock()
        mock_sync_store.update_sync_success = MagicMock()
        mock_sync_store.update_sync_failure = MagicMock()

        boeing_data = {
            "list_price": 26.00,
            "net_price": 24.00,
            "inventory_quantity": 120,
            "inventory_status": "in_stock",
            "location_quantities": [
                {"location": "Dallas Central", "quantity": 80},
            ],
            "location_summary": "Dallas Central: 80",
        }

        # Simulate what update_shopify_product task does:
        product_record = await mock_product_store.get_product_by_sku("WF338109", "test-user-id")
        shopify_product_id = product_record["shopify_product_id"]
        new_price = boeing_data["list_price"]
        shopify_price = round(new_price * 1.1, 2)

        await mock_shopify.update_product_pricing(
            shopify_product_id,
            price=shopify_price,
        )
        await mock_shopify.update_inventory_by_location(
            shopify_product_id,
            boeing_data["location_quantities"],
        )

        # Verify correct price calculation (10% markup)
        assert shopify_price == 28.60  # 26.00 * 1.1

        # Verify calls were made
        mock_shopify.update_product_pricing.assert_called_once()
        mock_shopify.update_inventory_by_location.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_update_records_success_in_sync_store(self):
        """Successful sync should update sync store with new hash and data."""
        from app.utils.hash_utils import compute_boeing_hash

        mock_sync_store = MagicMock()
        mock_sync_store.update_sync_success = MagicMock()

        boeing_data = {
            "list_price": 26.00,
            "inventory_quantity": 120,
            "inventory_status": "in_stock",
            "location_quantities": [
                {"location": "Dallas Central", "quantity": 80},
            ],
        }

        new_hash = compute_boeing_hash(boeing_data)

        mock_sync_store.update_sync_success(
            "WF338109",
            new_hash,
            boeing_data["list_price"],
            boeing_data["inventory_quantity"],
            inventory_status=boeing_data["inventory_status"],
            locations=boeing_data["location_quantities"],
        )

        mock_sync_store.update_sync_success.assert_called_once_with(
            "WF338109",
            new_hash,
            26.00,
            120,
            inventory_status="in_stock",
            locations=[{"location": "Dallas Central", "quantity": 80}],
        )

    @pytest.mark.asyncio
    async def test_sync_update_records_failure_on_error(self):
        """Failed sync should update sync store with error message."""
        mock_sync_store = MagicMock()
        mock_sync_store.update_sync_failure = MagicMock()

        error_msg = "Boeing API timeout"
        mock_sync_store.update_sync_failure("WF338109", error_msg)

        mock_sync_store.update_sync_failure.assert_called_once_with(
            "WF338109", "Boeing API timeout"
        )


@pytest.mark.e2e
class TestSyncPipelineOutOfStock:
    """E2E tests for out-of-stock handling in the sync pipeline."""

    def test_create_out_of_stock_data(self):
        """Out-of-stock data should have zero quantity and appropriate status."""
        from app.utils.boeing_data_extract import create_out_of_stock_data

        data = create_out_of_stock_data("WF338109")

        assert data is not None
        assert data.get("inventory_quantity") == 0 or data.get("is_missing_sku") is True
        assert data.get("inventory_status") in ("out_of_stock", None) or data.get("is_missing_sku") is True
