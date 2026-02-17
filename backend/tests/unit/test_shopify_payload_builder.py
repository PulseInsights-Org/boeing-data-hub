"""
Unit tests for Shopify payload builder.

Tests build_product_payload, build_metafields, and mapping helpers:
map_unit_of_measure, map_cert, validate_trace_url, map_inventory_location.

Version: 1.0.0
"""
import pytest
from unittest.mock import patch

from app.utils.shopify_payload_builder import (
    build_product_payload,
    build_metafields,
    map_unit_of_measure,
    map_cert,
    validate_trace_url,
    map_inventory_location,
)
from app.core.constants.pricing import MARKUP_FACTOR, FALLBACK_IMAGE_URL
from app.core.constants.publishing import PRODUCT_TAGS


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# map_unit_of_measure
# ---------------------------------------------------------------------------

class TestMapUnitOfMeasure:
    """Tests for UOM mapping."""

    def test_ea_maps_to_ea(self):
        assert map_unit_of_measure("EA") == "EA"

    def test_each_maps_to_ea(self):
        assert map_unit_of_measure("EACH") == "EA"

    def test_in_maps_to_inches(self):
        assert map_unit_of_measure("IN") == "Inches"

    def test_lb_maps_to_pound(self):
        assert map_unit_of_measure("LB") == "Pound"

    def test_case_insensitive(self):
        assert map_unit_of_measure("ea") == "EA"
        assert map_unit_of_measure("Lb") == "Pound"

    def test_whitespace_stripped(self):
        assert map_unit_of_measure("  EA  ") == "EA"

    def test_empty_returns_empty(self):
        assert map_unit_of_measure("") == ""

    def test_none_returns_empty(self):
        assert map_unit_of_measure(None) == ""

    def test_unknown_uom_returns_empty(self):
        assert map_unit_of_measure("GALLONS") == ""


# ---------------------------------------------------------------------------
# map_cert
# ---------------------------------------------------------------------------

class TestMapCert:
    """Tests for certificate mapping."""

    def test_faa_8130(self):
        assert map_cert("FAA 8130-3") == "FAA 8130-3"

    def test_easa_form(self):
        assert map_cert("EASA Form 1") == "EASA Form 1"

    def test_oem_cert(self):
        assert map_cert("OEM Certificate") == "OEM Cert"

    def test_default_to_faa_for_unknown(self):
        # For unknown cert strings, defaults to FAA 8130-3
        assert map_cert("Some Random Certificate") == "FAA 8130-3"

    def test_empty_returns_empty(self):
        assert map_cert("") == ""

    def test_none_returns_empty(self):
        assert map_cert(None) == ""

    def test_case_insensitive_matching(self):
        assert map_cert("easa form") == "EASA Form 1"

    def test_caa_mapping(self):
        assert map_cert("CAA United Kingdom") == "CAA UK"


# ---------------------------------------------------------------------------
# validate_trace_url
# ---------------------------------------------------------------------------

class TestValidateTraceUrl:
    """Tests for trace URL validation."""

    def test_valid_shopify_cdn(self):
        url = "https://cdn.shopify.com/files/trace123.pdf"
        assert validate_trace_url(url) == url

    def test_valid_smartcert(self):
        url = "https://www.getsmartcert.com/cert/abc"
        assert validate_trace_url(url) == url

    def test_disallowed_domain_returns_empty(self):
        url = "https://malicious.com/evil.pdf"
        assert validate_trace_url(url) == ""

    def test_empty_returns_empty(self):
        assert validate_trace_url("") == ""

    def test_none_returns_empty(self):
        assert validate_trace_url(None) == ""

    def test_whitespace_stripped(self):
        url = "  https://cdn.shopify.com/files/trace.pdf  "
        assert validate_trace_url(url) == url.strip()


# ---------------------------------------------------------------------------
# map_inventory_location
# ---------------------------------------------------------------------------

class TestMapInventoryLocation:
    """Tests for inventory location code mapping."""

    def test_exact_3char_location_id(self):
        assert map_inventory_location("Dallas Central", location_id="1D1") == "1D1"

    def test_exact_3char_location_string(self):
        assert map_inventory_location("1D1") == "1D1"

    def test_lookup_from_codes_map(self):
        codes = {"Dallas Central": "1D1"}
        result = map_inventory_location("Dallas Central: 106", inventory_location_codes=codes)
        assert result == "1D1"

    def test_semicolon_separated_takes_first(self):
        codes = {"Dallas Central": "1D1"}
        result = map_inventory_location(
            "Dallas Central: 100; Chicago: 50",
            inventory_location_codes=codes
        )
        assert result == "1D1"

    def test_no_match_returns_empty(self):
        codes = {"Dallas Central": "1D1"}
        result = map_inventory_location("Unknown Location", inventory_location_codes=codes)
        assert result == ""

    def test_empty_location_returns_empty(self):
        assert map_inventory_location("") == ""


# ---------------------------------------------------------------------------
# build_metafields
# ---------------------------------------------------------------------------

class TestBuildMetafields:
    """Tests for metafield list generation."""

    def test_includes_part_number(self, sample_boeing_record):
        metafields = build_metafields(sample_boeing_record)
        part_numbers = [m for m in metafields if m["key"] == "part_number"]
        assert len(part_numbers) == 1
        assert part_numbers[0]["value"] == "WF338109"

    def test_includes_condition(self, sample_boeing_record):
        metafields = build_metafields(sample_boeing_record)
        conditions = [m for m in metafields if m["key"] == "condition"]
        assert len(conditions) == 1
        assert conditions[0]["value"] == "NE"

    def test_includes_uom(self, sample_boeing_record):
        metafields = build_metafields(sample_boeing_record)
        uoms = [m for m in metafields if m["key"] == "unit_of_measure"]
        assert len(uoms) == 1
        assert uoms[0]["value"] == "EA"

    def test_includes_cert(self, sample_boeing_record):
        metafields = build_metafields(sample_boeing_record)
        certs = [m for m in metafields if m["key"] == "trace"]
        assert len(certs) == 1

    def test_all_metafields_have_namespace(self, sample_boeing_record):
        metafields = build_metafields(sample_boeing_record)
        for mf in metafields:
            assert "namespace" in mf
            assert mf["namespace"] in ("custom", "boeing")


# ---------------------------------------------------------------------------
# build_product_payload
# ---------------------------------------------------------------------------

class TestBuildProductPayload:
    """Tests for full Shopify REST payload generation."""

    def test_payload_has_product_key(self, sample_boeing_record):
        payload = build_product_payload(sample_boeing_record)
        assert "product" in payload

    def test_payload_has_title(self, sample_boeing_record):
        payload = build_product_payload(sample_boeing_record)
        assert payload["product"]["title"] == "WF338109"

    def test_payload_has_variants_with_sku(self, sample_boeing_record):
        payload = build_product_payload(sample_boeing_record)
        variants = payload["product"]["variants"]
        assert len(variants) == 1
        assert variants[0]["sku"] == "WF338109"

    def test_payload_price_uses_markup(self, sample_boeing_record):
        payload = build_product_payload(sample_boeing_record)
        variant_price = float(payload["product"]["variants"][0]["price"])
        expected = sample_boeing_record["list_price"] * MARKUP_FACTOR
        assert abs(variant_price - expected) < 0.01

    def test_payload_has_images(self, sample_boeing_record):
        payload = build_product_payload(sample_boeing_record)
        images = payload["product"]["images"]
        assert len(images) >= 1

    def test_fallback_image_when_no_image(self):
        record = {"sku": "TEST123", "title": "TEST"}
        payload = build_product_payload(record)
        images = payload["product"]["images"]
        assert any(FALLBACK_IMAGE_URL in img["src"] for img in images)

    def test_payload_has_tags(self, sample_boeing_record):
        payload = build_product_payload(sample_boeing_record)
        tags = payload["product"]["tags"]
        for expected_tag in PRODUCT_TAGS:
            assert expected_tag in tags

    def test_payload_has_metafields(self, sample_boeing_record):
        payload = build_product_payload(sample_boeing_record)
        metafields = payload["product"]["metafields"]
        assert isinstance(metafields, list)
        assert len(metafields) > 0
