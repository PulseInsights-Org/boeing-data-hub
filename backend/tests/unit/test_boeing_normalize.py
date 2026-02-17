"""
Unit tests for boeing_normalize — transforms raw Boeing API data to staging format.

Tests cover:
- _to_float converts various types and returns None for zero/invalid
- _to_int converts various types and returns None for invalid
- _strip_variant_suffix strips "=K3" style suffixes
- normalize_boeing_payload full normalization of a sample payload
- Price markup (1.1x) applied correctly
- Inventory status derived from inStock and quantity
- Location summary formatting
- PMA detection from faaApprovalCode
- shopify sub-dict uses stripped SKU

Version: 1.0.0
"""
import pytest

from app.utils.boeing_normalize import (
    _to_float,
    _to_int,
    _strip_variant_suffix,
    normalize_boeing_payload,
)


# --------------------------------------------------------------------------
# _to_float
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestToFloat:

    def test_converts_string_to_float(self):
        assert _to_float("25.50") == 25.50

    def test_converts_int_to_float(self):
        assert _to_float(10) == 10.0

    def test_returns_none_for_zero(self):
        assert _to_float(0) is None
        assert _to_float("0") is None
        assert _to_float(0.0) is None

    def test_returns_none_for_none(self):
        assert _to_float(None) is None

    def test_returns_none_for_invalid_string(self):
        assert _to_float("abc") is None
        assert _to_float("") is None

    def test_converts_negative_number(self):
        assert _to_float("-5.5") == -5.5

    def test_converts_float_value(self):
        assert _to_float(3.14) == 3.14


# --------------------------------------------------------------------------
# _to_int
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestToInt:

    def test_converts_string_to_int(self):
        assert _to_int("42") == 42

    def test_converts_float_to_int(self):
        assert _to_int(3.7) == 3

    def test_returns_none_for_none(self):
        assert _to_int(None) is None

    def test_returns_none_for_invalid_string(self):
        assert _to_int("abc") is None

    def test_zero_is_valid_int(self):
        assert _to_int(0) == 0
        assert _to_int("0") == 0


# --------------------------------------------------------------------------
# _strip_variant_suffix
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestStripVariantSuffix:

    def test_strips_suffix_after_equals(self):
        assert _strip_variant_suffix("WF338109=K3") == "WF338109"

    def test_no_suffix_returns_same(self):
        assert _strip_variant_suffix("WF338109") == "WF338109"

    def test_empty_string_returns_empty(self):
        assert _strip_variant_suffix("") == ""

    def test_multiple_equals_strips_at_first(self):
        assert _strip_variant_suffix("A=B=C") == "A"

    def test_none_returns_empty(self):
        assert _strip_variant_suffix(None) == ""


# --------------------------------------------------------------------------
# normalize_boeing_payload — full payload
# --------------------------------------------------------------------------

@pytest.fixture
def sample_boeing_api_response():
    """Sample raw Boeing API response with one line item."""
    return {
        "currency": "USD",
        "lineItems": [
            {
                "aviallPartNumber": "WF338109=K3",
                "name": "GASKET, O-RING",
                "description": "O-Ring Gasket for hydraulic system",
                "listPrice": "25.50",
                "netPrice": "23.00",
                "quantity": "150",
                "inStock": True,
                "baseUOM": "EA",
                "countryOfOrigin": "US",
                "dim": "2.5 x 1.0 x 0.5",
                "dimUOM": "IN",
                "weight": "0.1",
                "weightUOM": "LB",
                "supplierName": "Aviall",
                "faaApprovalCode": "PMA",
                "hazmatCode": "N",
                "eccn": "EAR99",
                "scheduleBCode": "8484.10",
                "productImage": "https://boeing.com/img/WF338109.jpg",
                "thumbnailImage": "https://boeing.com/thumb/WF338109.jpg",
                "locationAvailabilities": [
                    {"location": "Dallas Central", "availQuantity": "100"},
                    {"location": "Chicago Warehouse", "availQuantity": "50"},
                ],
            }
        ],
    }


@pytest.mark.unit
class TestNormalizeBoeingPayload:

    def test_returns_one_record_per_line_item(self, sample_boeing_api_response):
        result = normalize_boeing_payload("WF338109", sample_boeing_api_response)

        assert len(result) == 1

    def test_sku_stores_full_part_number_with_suffix(self, sample_boeing_api_response):
        result = normalize_boeing_payload("WF338109", sample_boeing_api_response)
        record = result[0]

        assert record["sku"] == "WF338109=K3"
        assert record["aviall_part_number"] == "WF338109=K3"

    def test_shopify_sku_is_stripped(self, sample_boeing_api_response):
        result = normalize_boeing_payload("WF338109", sample_boeing_api_response)
        record = result[0]

        assert record["shopify"]["sku"] == "WF338109"
        assert record["shopify"]["title"] == "WF338109"

    def test_price_has_10_percent_markup(self, sample_boeing_api_response):
        result = normalize_boeing_payload("WF338109", sample_boeing_api_response)
        record = result[0]

        list_price = 25.50
        expected_price = list_price * 1.1
        assert record["price"] == pytest.approx(expected_price, rel=1e-6)
        assert record["cost_per_item"] == 25.50

    def test_inventory_status_in_stock(self, sample_boeing_api_response):
        result = normalize_boeing_payload("WF338109", sample_boeing_api_response)
        record = result[0]

        assert record["inventory_status"] == "in_stock"
        assert record["inventory_quantity"] == 150

    def test_dimensions_parsed(self, sample_boeing_api_response):
        result = normalize_boeing_payload("WF338109", sample_boeing_api_response)
        record = result[0]

        assert record["dim_length"] == 2.5
        assert record["dim_width"] == 1.0
        assert record["dim_height"] == 0.5

    def test_pma_detected_from_faa_approval_code(self, sample_boeing_api_response):
        result = normalize_boeing_payload("WF338109", sample_boeing_api_response)
        record = result[0]

        assert record["pma"] is True

    def test_location_summary_formatted(self, sample_boeing_api_response):
        result = normalize_boeing_payload("WF338109", sample_boeing_api_response)
        record = result[0]

        assert "Dallas Central: 100" in record["location_summary"]
        assert "Chicago Warehouse: 50" in record["location_summary"]

    def test_currency_propagated(self, sample_boeing_api_response):
        result = normalize_boeing_payload("WF338109", sample_boeing_api_response)
        record = result[0]

        assert record["currency"] == "USD"
        assert record["shopify"]["currency"] == "USD"

    def test_raw_boeing_data_preserved(self, sample_boeing_api_response):
        result = normalize_boeing_payload("WF338109", sample_boeing_api_response)
        record = result[0]

        assert "raw_boeing_data" in record
        assert record["raw_boeing_data"]["aviallPartNumber"] == "WF338109=K3"
        assert record["raw_boeing_data"]["currency"] == "USD"

    def test_condition_is_always_ne(self, sample_boeing_api_response):
        result = normalize_boeing_payload("WF338109", sample_boeing_api_response)
        record = result[0]

        assert record["condition"] == "NE"

    def test_body_html_contains_part_info(self, sample_boeing_api_response):
        result = normalize_boeing_payload("WF338109", sample_boeing_api_response)
        record = result[0]
        body = record["shopify"]["body_html"]

        assert "WF338109" in body  # stripped SKU
        assert "GASKET, O-RING" in body
        assert "Aviall" in body
        assert "FAA 8130-3" in body
        assert "NE" in body


# --------------------------------------------------------------------------
# Edge cases
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestNormalizeEdgeCases:

    def test_empty_line_items_returns_empty(self):
        payload = {"currency": "USD", "lineItems": []}
        result = normalize_boeing_payload("X", payload)
        assert result == []

    def test_missing_line_items_returns_empty(self):
        payload = {"currency": "USD"}
        result = normalize_boeing_payload("X", payload)
        assert result == []

    def test_out_of_stock_status(self):
        payload = {
            "currency": "USD",
            "lineItems": [{
                "aviallPartNumber": "OOS-PART",
                "inStock": False,
                "quantity": "0",
                "locationAvailabilities": [],
            }],
        }
        result = normalize_boeing_payload("OOS-PART", payload)
        record = result[0]
        assert record["inventory_status"] == "out_of_stock"
        assert record["inventory_quantity"] == 0

    def test_no_price_data(self):
        payload = {
            "currency": None,
            "lineItems": [{
                "aviallPartNumber": "FREE-PART",
                "inStock": True,
                "quantity": "5",
                "locationAvailabilities": [],
            }],
        }
        result = normalize_boeing_payload("FREE-PART", payload)
        record = result[0]
        assert record["list_price"] is None
        assert record["net_price"] is None
        assert record["price"] is None
        assert record["cost_per_item"] is None

    def test_non_pma_faa_code(self):
        payload = {
            "currency": "USD",
            "lineItems": [{
                "aviallPartNumber": "STD-PART",
                "faaApprovalCode": "TSO",
                "locationAvailabilities": [],
            }],
        }
        result = normalize_boeing_payload("STD-PART", payload)
        assert result[0]["pma"] is False

    def test_invalid_dimensions_handled_gracefully(self):
        payload = {
            "currency": "USD",
            "lineItems": [{
                "aviallPartNumber": "BAD-DIM",
                "dim": "not-a-dimension",
                "locationAvailabilities": [],
            }],
        }
        result = normalize_boeing_payload("BAD-DIM", payload)
        record = result[0]
        # Should not crash, dimensions should be None
        assert record["dim_length"] is None
        assert record["dim_width"] is None
        assert record["dim_height"] is None

    def test_default_supplier_name_is_bdi(self):
        payload = {
            "currency": "USD",
            "lineItems": [{
                "aviallPartNumber": "NO-SUPPLIER",
                "locationAvailabilities": [],
            }],
        }
        result = normalize_boeing_payload("NO-SUPPLIER", payload)
        assert result[0]["supplier_name"] == "BDI"
        assert result[0]["manufacturer"] == "BDI"
