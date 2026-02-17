"""
Unit tests for sync helper functions.
Version: 1.0.0
"""
import pytest
from unittest.mock import patch, MagicMock


@pytest.mark.unit
class TestGetSlotDistribution:
    """Tests for get_slot_distribution."""

    def test_empty_counts(self):
        """Empty slot counts returns all dormant."""
        from app.utils.slot_manager import get_slot_distribution
        result = get_slot_distribution({})
        assert result["total_products"] == 0
        assert len(result["dormant_slots"]) > 0

    def test_mixed_counts(self):
        """Slots with varying counts are categorized correctly."""
        from app.utils.slot_manager import get_slot_distribution
        # Simulate: slot 0 has many products, slot 1 has few, rest empty
        counts = {0: 100, 1: 3}
        result = get_slot_distribution(counts)
        assert result["total_products"] == 103
        assert 0 in result["active_slots"]
        assert 1 in result["filling_slots"]

    def test_all_slots_categorized(self):
        """Total of dormant + filling + active equals max_buckets."""
        from app.utils.slot_manager import get_slot_distribution
        result = get_slot_distribution({0: 50})
        total = result["dormant_count"] + result["filling_count"] + result["active_count"]
        assert total == result["max_buckets"]


@pytest.mark.unit
class TestBatchBuilding:
    """Tests for calculate_batch_groups."""

    def test_products_split_into_batches(self):
        """Product list is split into proper batches."""
        from app.utils.batch_grouping import calculate_batch_groups
        products = [{"sku": f"SKU-{i}"} for i in range(25)]
        batches = calculate_batch_groups(products, max_batch_size=10)
        assert isinstance(batches, list)
        assert len(batches) == 3  # 10 + 10 + 5
        # All products should be present across batches
        all_products = [p for batch in batches for p in batch]
        assert len(all_products) == 25

    def test_single_batch_when_under_limit(self):
        """Products under batch size produce a single batch."""
        from app.utils.batch_grouping import calculate_batch_groups
        products = [{"sku": f"SKU-{i}"} for i in range(5)]
        batches = calculate_batch_groups(products, max_batch_size=10)
        assert len(batches) == 1
        assert len(batches[0]) == 5

    def test_empty_list_returns_empty(self):
        """Empty product list returns empty batches."""
        from app.utils.batch_grouping import calculate_batch_groups
        batches = calculate_batch_groups([], max_batch_size=10)
        assert batches == []


@pytest.mark.unit
class TestGetLeastLoadedSlot:
    """Tests for get_least_loaded_slot."""

    def test_returns_empty_slot(self):
        """Should return an empty slot when one is available."""
        from app.utils.slot_manager import get_least_loaded_slot
        slot_counts = {0: 10, 1: 5}
        slot = get_least_loaded_slot(slot_counts, total_products=15)
        assert slot == 1  # least loaded

    def test_returns_zero_for_empty_counts(self):
        """Should return slot 0 when no products exist."""
        from app.utils.slot_manager import get_least_loaded_slot
        slot = get_least_loaded_slot({}, total_products=0)
        assert slot == 0


@pytest.mark.unit
class TestChangeDetection:
    """Tests for should_update_shopify."""

    def test_detects_price_change(self):
        """Detects when price has changed."""
        from app.utils.change_detection import should_update_shopify
        from app.utils.hash_utils import compute_boeing_hash
        old_data = {"list_price": 25.50, "inventory_quantity": 100}
        new_data = {"list_price": 30.00, "inventory_quantity": 100}
        old_hash = compute_boeing_hash(old_data)
        should_update, reason = should_update_shopify(new_data, old_hash, 25.50, 100)
        assert should_update is True
        assert "price" in reason

    def test_no_change_returns_false(self):
        """No data change returns False."""
        from app.utils.change_detection import should_update_shopify
        from app.utils.hash_utils import compute_boeing_hash
        data = {"list_price": 25.50, "inventory_quantity": 100}
        current_hash = compute_boeing_hash(data)
        should_update, reason = should_update_shopify(data, current_hash, 25.50, 100)
        assert should_update is False

    def test_first_sync_returns_true(self):
        """First sync (no last_hash) returns True."""
        from app.utils.change_detection import should_update_shopify
        data = {"list_price": 25.50, "inventory_quantity": 100}
        should_update, reason = should_update_shopify(data, None, None, None)
        assert should_update is True


@pytest.mark.unit
class TestTimeFunctions:
    """Tests for time/bucket utility functions."""

    def test_get_current_hour_utc_range(self):
        """Current hour should be 0-23."""
        from app.utils.schedule_helpers import get_current_hour_utc
        hour = get_current_hour_utc()
        assert 0 <= hour <= 23

    def test_get_current_minute_bucket_range(self):
        """Minute bucket should be 0-5."""
        from app.utils.schedule_helpers import get_current_minute_bucket
        bucket = get_current_minute_bucket()
        assert 0 <= bucket <= 5

    def test_calculate_next_retry_time_capped(self):
        """Retry time should be capped at 24 hours."""
        from app.utils.schedule_helpers import calculate_next_retry_time
        hours = calculate_next_retry_time(consecutive_failures=10)
        assert hours <= 24

    def test_calculate_next_retry_time_exponential(self):
        """Retry time should increase with failures."""
        from app.utils.schedule_helpers import calculate_next_retry_time
        h1 = calculate_next_retry_time(1)
        h2 = calculate_next_retry_time(2)
        assert h2 > h1


@pytest.mark.unit
class TestReExports:
    """Tests for backward-compatible re-exports."""

    def test_hash_functions_importable(self):
        """compute_boeing_hash and compute_sync_hash are importable from sync_helpers."""
        from app.utils.hash_utils import compute_boeing_hash, compute_sync_hash
        assert callable(compute_boeing_hash)
        assert callable(compute_sync_hash)


@pytest.mark.unit
class TestExtractBoeingProductData:
    """Tests for extract_boeing_product_data."""

    def test_extracts_matching_sku(self):
        """Extracts product data for matching SKU."""
        from app.utils.boeing_data_extract import extract_boeing_product_data
        response = {
            "currency": "USD",
            "lineItems": [{"aviallPartNumber": "WF338109", "listPrice": 25.50, "netPrice": 23.00,
                           "inStock": True, "locationAvailabilities": []}],
        }
        result = extract_boeing_product_data(response, "WF338109")
        assert result is not None
        assert result["sku"] == "WF338109"
        assert result["list_price"] == 25.50

    def test_returns_none_for_missing_sku(self):
        """Returns None when SKU not found."""
        from app.utils.boeing_data_extract import extract_boeing_product_data
        response = {"currency": "USD", "lineItems": [{"aviallPartNumber": "OTHER", "listPrice": 10.0}]}
        result = extract_boeing_product_data(response, "MISSING")
        assert result is None

    def test_creates_out_of_stock_data(self):
        """create_out_of_stock_data returns zero quantity."""
        from app.utils.boeing_data_extract import create_out_of_stock_data
        result = create_out_of_stock_data("WF338109")
        assert result["inventory_quantity"] == 0
        assert result["is_missing_sku"] is True
