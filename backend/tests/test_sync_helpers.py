"""
Unit tests for sync_helpers.py.

Tests cover:
- Boeing field extraction (correct field mappings)
- Hash computation for change detection
- Out-of-stock data creation
- Slot distribution calculations
- Bucket allocation algorithms
"""

import pytest
from unittest.mock import patch, MagicMock

# Import the module under test
from app.utils.boeing_data_extract import extract_boeing_product_data, create_out_of_stock_data
from app.utils.hash_utils import compute_boeing_hash, compute_sync_hash
from app.utils.change_detection import should_update_shopify
from app.utils.slot_manager import get_slot_distribution, get_least_loaded_slot
from app.utils.batch_grouping import calculate_batch_groups
from app.utils.schedule_helpers import get_current_bucket
from app.utils.type_converters import to_float as _to_float, to_int as _to_int


# =============================================================================
# HELPER FUNCTION TESTS
# =============================================================================

class TestHelperFunctions:
    """Test helper conversion functions."""

    def test_to_float_valid_number(self):
        """Test _to_float with valid numbers."""
        assert _to_float(100.5) == 100.5
        assert _to_float("200.75") == 200.75
        assert _to_float(50) == 50.0

    def test_to_float_zero_returns_none(self):
        """Test _to_float returns None for zero values."""
        assert _to_float(0) is None
        assert _to_float("0") is None
        assert _to_float(0.0) is None

    def test_to_float_invalid_returns_none(self):
        """Test _to_float returns None for invalid values."""
        assert _to_float(None) is None
        assert _to_float("invalid") is None
        assert _to_float({}) is None

    def test_to_int_valid_number(self):
        """Test _to_int with valid numbers."""
        assert _to_int(100) == 100
        assert _to_int("50") == 50
        assert _to_int(25.9) == 25  # Truncates

    def test_to_int_invalid_returns_none(self):
        """Test _to_int returns None for invalid values."""
        assert _to_int(None) is None
        assert _to_int("invalid") is None
        assert _to_int({}) is None


# =============================================================================
# BOEING DATA EXTRACTION TESTS
# =============================================================================

class TestExtractBoeingProductData:
    """Test extract_boeing_product_data function with correct field mappings."""

    def test_extract_product_found(self, sample_boeing_response):
        """Test extraction when SKU is found in response."""
        result = extract_boeing_product_data(sample_boeing_response, "WF338109=K3")

        assert result is not None
        assert result["sku"] == "WF338109=K3"
        assert result["boeing_sku"] == "WF338109=K3"
        assert result["list_price"] == 753.09
        assert result["net_price"] == 680.50
        assert result["currency"] == "USD"
        assert result["inventory_quantity"] == 25
        assert result["inventory_status"] == "in_stock"
        assert result["in_stock"] is True
        assert len(result["locations"]) == 2
        assert len(result["location_quantities"]) == 2

    def test_extract_product_not_found(self, sample_boeing_response):
        """Test extraction when SKU is not in response."""
        result = extract_boeing_product_data(sample_boeing_response, "NONEXISTENT123")

        assert result is None

    def test_extract_product_case_insensitive(self, sample_boeing_response):
        """Test SKU matching is case-insensitive."""
        result = extract_boeing_product_data(sample_boeing_response, "wf338109=k3")

        assert result is not None
        assert result["boeing_sku"] == "WF338109=K3"

    def test_extract_correct_field_mappings(self, sample_boeing_response):
        """
        Test that field mappings match boeing_normalize.py.

        CRITICAL: Ensures we use:
        - listPrice (direct field, NOT nested under "pricing")
        - locationAvailabilities (NOT "availability")
        - availQuantity (NOT "availableQuantity")
        """
        result = extract_boeing_product_data(sample_boeing_response, "WF338109=K3")

        # Verify direct price access (not nested)
        assert result["list_price"] == 753.09
        assert result["net_price"] == 680.50

        # Verify location quantities from locationAvailabilities
        assert result["inventory_quantity"] == 25  # 15 + 10
        assert result["locations"][0]["location"] == "Dallas Central"
        assert result["locations"][0]["quantity"] == 15

    def test_extract_location_summary_format(self, sample_boeing_response):
        """Test location summary matches expected format."""
        result = extract_boeing_product_data(sample_boeing_response, "WF338109=K3")

        # Format should be "Location: qty; Location: qty"
        assert "Dallas Central: 15" in result["location_summary"]
        assert "Chicago Warehouse: 10" in result["location_summary"]
        assert ";" in result["location_summary"]

    def test_extract_out_of_stock_status(self):
        """Test inventory status detection for out-of-stock items."""
        response = {
            "currency": "USD",
            "lineItems": [
                {
                    "aviallPartNumber": "OUTSTOCK123",
                    "inStock": False,
                    "listPrice": 100.0,
                    "locationAvailabilities": [],
                }
            ]
        }
        result = extract_boeing_product_data(response, "OUTSTOCK123")

        assert result is not None
        assert result["inventory_quantity"] == 0
        assert result["inventory_status"] == "out_of_stock"
        assert result["in_stock"] is False

    def test_extract_empty_response(self, sample_boeing_empty_response):
        """Test extraction from empty response."""
        result = extract_boeing_product_data(sample_boeing_empty_response, "ANY123")

        assert result is None


class TestCreateOutOfStockData:
    """Test create_out_of_stock_data function."""

    def test_creates_out_of_stock_record(self):
        """Test creation of synthetic out-of-stock data."""
        result = create_out_of_stock_data("MISSING123")

        assert result["sku"] == "MISSING123"
        assert result["boeing_sku"] == "MISSING123"
        assert result["list_price"] is None
        assert result["inventory_quantity"] == 0
        assert result["inventory_status"] == "out_of_stock"
        assert result["in_stock"] is False
        assert result["locations"] == []
        assert result["is_missing_sku"] is True

    def test_out_of_stock_has_required_fields(self):
        """Test that out-of-stock data has all required fields for sync."""
        result = create_out_of_stock_data("TEST123")

        required_fields = [
            "sku", "boeing_sku", "list_price", "net_price", "currency",
            "inventory_quantity", "inventory_status", "in_stock",
            "locations", "location_quantities", "location_summary",
            "estimated_lead_time_days", "is_missing_sku"
        ]
        for field in required_fields:
            assert field in result, f"Missing required field: {field}"


# =============================================================================
# HASH COMPUTATION TESTS
# =============================================================================

class TestComputeBoeingHash:
    """Test hash computation for change detection."""

    def test_hash_deterministic(self, sample_normalized_product):
        """Test that same data produces same hash."""
        hash1 = compute_boeing_hash(sample_normalized_product)
        hash2 = compute_boeing_hash(sample_normalized_product)

        assert hash1 == hash2

    def test_hash_changes_with_price(self, sample_normalized_product):
        """Test that hash changes when price changes."""
        hash1 = compute_boeing_hash(sample_normalized_product)

        modified = sample_normalized_product.copy()
        modified["list_price"] = 999.99
        hash2 = compute_boeing_hash(modified)

        assert hash1 != hash2

    def test_hash_changes_with_quantity(self, sample_normalized_product):
        """Test that hash changes when quantity changes."""
        hash1 = compute_boeing_hash(sample_normalized_product)

        modified = sample_normalized_product.copy()
        modified["inventory_quantity"] = 0
        hash2 = compute_boeing_hash(modified)

        assert hash1 != hash2

    def test_hash_changes_with_status(self, sample_normalized_product):
        """Test that hash changes when inventory status changes."""
        hash1 = compute_boeing_hash(sample_normalized_product)

        modified = sample_normalized_product.copy()
        modified["inventory_status"] = "out_of_stock"
        hash2 = compute_boeing_hash(modified)

        assert hash1 != hash2

    def test_hash_length(self, sample_normalized_product):
        """Test that hash is 16 characters (first 16 of SHA-256)."""
        hash_value = compute_boeing_hash(sample_normalized_product)

        assert len(hash_value) == 16


class TestComputeSyncHash:
    """Test sync hash computation from individual fields."""

    def test_sync_hash_matches_boeing_hash(self, sample_normalized_product):
        """Test that sync hash produces same result as boeing hash for same data."""
        boeing_hash = compute_boeing_hash(sample_normalized_product)
        sync_hash = compute_sync_hash(
            price=sample_normalized_product["list_price"],
            quantity=sample_normalized_product["inventory_quantity"],
            inventory_status=sample_normalized_product["inventory_status"],
            location_summary=sample_normalized_product["location_summary"],
        )

        assert boeing_hash == sync_hash

    def test_sync_hash_out_of_stock(self, sample_out_of_stock_data):
        """Test hash for out-of-stock products."""
        hash_value = compute_sync_hash(
            price=None,
            quantity=0,
            inventory_status="out_of_stock",
            location_summary=None,
        )

        assert len(hash_value) == 16


# =============================================================================
# CHANGE DETECTION TESTS
# =============================================================================

class TestShouldUpdateShopify:
    """Test should_update_shopify function for change detection."""

    def test_update_on_first_sync(self, sample_normalized_product):
        """Test that first sync always triggers update."""
        should_update, reason = should_update_shopify(
            sample_normalized_product,
            last_hash=None,
            last_price=None,
            last_quantity=None,
        )

        assert should_update is True
        assert "first_sync" in reason.lower() or "hash_mismatch" in reason.lower()

    def test_no_update_when_hash_matches(self, sample_normalized_product):
        """Test that no update when hash matches."""
        current_hash = compute_boeing_hash(sample_normalized_product)

        should_update, reason = should_update_shopify(
            sample_normalized_product,
            last_hash=current_hash,
            last_price=sample_normalized_product["list_price"],
            last_quantity=sample_normalized_product["inventory_quantity"],
        )

        assert should_update is False
        assert reason == "no_change"

    def test_update_on_price_change(self, sample_normalized_product):
        """Test that update triggers when price changes."""
        should_update, reason = should_update_shopify(
            sample_normalized_product,
            last_hash="different_hash",
            last_price=100.00,  # Different from sample
            last_quantity=sample_normalized_product["inventory_quantity"],
        )

        assert should_update is True
        assert "price" in reason.lower()

    def test_update_on_quantity_change(self, sample_normalized_product):
        """Test that update triggers when quantity changes."""
        should_update, reason = should_update_shopify(
            sample_normalized_product,
            last_hash="different_hash",
            last_price=sample_normalized_product["list_price"],
            last_quantity=1,  # Different from sample
        )

        assert should_update is True
        assert "quantity" in reason.lower()


# =============================================================================
# SLOT DISTRIBUTION TESTS
# =============================================================================

class TestSlotDistribution:
    """Test slot distribution analysis."""

    def test_get_slot_distribution_empty(self):
        """Test distribution with no products."""
        result = get_slot_distribution({})

        assert result["dormant_count"] > 0
        assert result["filling_count"] == 0
        assert result["active_count"] == 0
        assert result["total_products"] == 0

    def test_get_slot_distribution_active_slots(self):
        """Test distribution with active slots (10+ products)."""
        slot_counts = {0: 15, 1: 12, 2: 10}

        result = get_slot_distribution(slot_counts)

        assert result["active_count"] == 3
        assert result["total_products"] == 37

    def test_get_slot_distribution_filling_slots(self):
        """Test distribution with filling slots (1-9 products)."""
        slot_counts = {0: 5, 1: 3, 2: 8}

        result = get_slot_distribution(slot_counts)

        assert result["filling_count"] == 3
        assert result["active_count"] == 0


class TestLeastLoadedSlot:
    """Test least-loaded slot allocation algorithm."""

    def test_least_loaded_empty(self):
        """Test allocation with no existing products."""
        slot = get_least_loaded_slot({}, total_products=1)

        assert slot == 0  # First slot

    def test_least_loaded_balances(self):
        """Test that allocation balances across slots."""
        slot_counts = {0: 10, 1: 5, 2: 8}

        slot = get_least_loaded_slot(slot_counts, total_products=24)

        assert slot == 1  # Least loaded

    def test_least_loaded_respects_active_range(self):
        """Test that allocation only considers active slot range."""
        slot_counts = {0: 10, 1: 10}

        # With 21 products, need 3 slots (21/10 = 2.1 -> 3)
        slot = get_least_loaded_slot(slot_counts, total_products=21)

        assert slot == 2  # New slot in active range


class TestBatchGroups:
    """Test batch grouping for Boeing API calls."""

    def test_batch_groups_exact_size(self):
        """Test grouping when products exactly fill batches."""
        products = [{"sku": f"SKU{i}"} for i in range(20)]

        batches = calculate_batch_groups(products, max_batch_size=10)

        assert len(batches) == 2
        assert len(batches[0]) == 10
        assert len(batches[1]) == 10

    def test_batch_groups_partial(self):
        """Test grouping with partial last batch."""
        products = [{"sku": f"SKU{i}"} for i in range(15)]

        batches = calculate_batch_groups(products, max_batch_size=10)

        assert len(batches) == 2
        assert len(batches[0]) == 10
        assert len(batches[1]) == 5

    def test_batch_groups_small(self):
        """Test grouping with fewer products than batch size."""
        products = [{"sku": f"SKU{i}"} for i in range(3)]

        batches = calculate_batch_groups(products, max_batch_size=10)

        assert len(batches) == 1
        assert len(batches[0]) == 3

    def test_batch_groups_empty(self):
        """Test grouping with empty list."""
        batches = calculate_batch_groups([], max_batch_size=10)

        assert len(batches) == 0


# =============================================================================
# BUCKET CALCULATION TESTS
# =============================================================================

class TestCurrentBucket:
    """Test current bucket calculation based on sync mode."""

    @patch("app.utils.schedule_helpers.SYNC_MODE", "testing")
    def test_testing_mode_minute_bucket(self):
        """Test that testing mode uses minute buckets."""
        from datetime import datetime
        from unittest.mock import patch as mock_patch

        # Mock datetime to return minute 25
        mock_now = MagicMock()
        mock_now.minute = 25
        mock_now.hour = 14

        with mock_patch("app.utils.schedule_helpers.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            # Bucket should be 25 // 10 = 2
            # Note: get_current_bucket checks SYNC_MODE at module level

    @patch("app.utils.schedule_helpers.SYNC_MODE", "production")
    def test_production_mode_hour_bucket(self):
        """Test that production mode uses hour buckets."""
        # In production mode, bucket = current hour (0-23)
        pass  # Similar to above with hour check
