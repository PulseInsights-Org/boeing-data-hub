"""
Unit tests for hash utilities.

Tests compute_boeing_hash and compute_sync_hash for determinism,
uniqueness on different inputs, and graceful handling of None/missing keys.

Version: 1.0.0
"""
import pytest

from app.utils.hash_utils import compute_boeing_hash, compute_sync_hash


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# compute_boeing_hash
# ---------------------------------------------------------------------------

class TestComputeBoeingHash:
    """Tests for compute_boeing_hash."""

    def test_deterministic_same_input(self):
        record = {"list_price": 25.50, "inventory_quantity": 100, "inventory_status": "in_stock", "location_summary": "Dallas: 100"}
        hash1 = compute_boeing_hash(record)
        hash2 = compute_boeing_hash(record)
        assert hash1 == hash2

    def test_returns_16_char_hex_string(self):
        record = {"list_price": 25.50, "inventory_quantity": 100}
        h = compute_boeing_hash(record)
        assert isinstance(h, str)
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_prices_different_hashes(self):
        record1 = {"list_price": 25.50, "inventory_quantity": 100}
        record2 = {"list_price": 30.00, "inventory_quantity": 100}
        assert compute_boeing_hash(record1) != compute_boeing_hash(record2)

    def test_different_quantities_different_hashes(self):
        record1 = {"list_price": 25.50, "inventory_quantity": 100}
        record2 = {"list_price": 25.50, "inventory_quantity": 200}
        assert compute_boeing_hash(record1) != compute_boeing_hash(record2)

    def test_handles_missing_keys_gracefully(self):
        record = {}
        h = compute_boeing_hash(record)
        assert isinstance(h, str)
        assert len(h) == 16

    def test_handles_none_values(self):
        record = {"list_price": None, "inventory_quantity": None, "inventory_status": None, "location_summary": None}
        h = compute_boeing_hash(record)
        assert isinstance(h, str)
        assert len(h) == 16

    def test_net_price_fallback(self):
        """When list_price is None, net_price should be used."""
        record1 = {"net_price": 23.00, "inventory_quantity": 100}
        record2 = {"net_price": 23.00, "inventory_quantity": 100}
        assert compute_boeing_hash(record1) == compute_boeing_hash(record2)

    def test_location_summary_affects_hash(self):
        record1 = {"list_price": 25.50, "inventory_quantity": 100, "location_summary": "Dallas: 100"}
        record2 = {"list_price": 25.50, "inventory_quantity": 100, "location_summary": "Chicago: 100"}
        assert compute_boeing_hash(record1) != compute_boeing_hash(record2)


# ---------------------------------------------------------------------------
# compute_sync_hash
# ---------------------------------------------------------------------------

class TestComputeSyncHash:
    """Tests for compute_sync_hash."""

    def test_deterministic_same_input(self):
        h1 = compute_sync_hash(25.50, 100, "in_stock", "Dallas: 100")
        h2 = compute_sync_hash(25.50, 100, "in_stock", "Dallas: 100")
        assert h1 == h2

    def test_returns_16_char_hex(self):
        h = compute_sync_hash(25.50, 100, "in_stock", "Dallas: 100")
        assert isinstance(h, str)
        assert len(h) == 16

    def test_different_prices_different_hashes(self):
        h1 = compute_sync_hash(25.50, 100, "in_stock", "Dallas: 100")
        h2 = compute_sync_hash(30.00, 100, "in_stock", "Dallas: 100")
        assert h1 != h2

    def test_different_status_different_hashes(self):
        h1 = compute_sync_hash(25.50, 100, "in_stock", "Dallas: 100")
        h2 = compute_sync_hash(25.50, 100, "out_of_stock", "Dallas: 100")
        assert h1 != h2

    def test_none_price_handled(self):
        h = compute_sync_hash(None, 0, None, None)
        assert isinstance(h, str)
        assert len(h) == 16

    def test_consistency_with_boeing_hash(self):
        """compute_sync_hash with same fields should match compute_boeing_hash."""
        record = {
            "list_price": 25.50,
            "inventory_quantity": 100,
            "inventory_status": "in_stock",
            "location_summary": "Dallas: 100",
        }
        boeing_hash = compute_boeing_hash(record)
        sync_hash = compute_sync_hash(25.50, 100, "in_stock", "Dallas: 100")
        assert boeing_hash == sync_hash

    def test_zero_quantity(self):
        h = compute_sync_hash(10.0, 0, "out_of_stock", None)
        assert isinstance(h, str)
        assert len(h) == 16
