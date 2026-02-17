"""
Unit tests for core constants modules.

Verifies that all four constant modules (pricing, publishing, extraction, sync)
export expected values with correct types and non-empty content.

Version: 1.0.0
"""
import pytest


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Pricing constants
# ---------------------------------------------------------------------------

class TestPricingConstants:
    """Tests for app.core.constants.pricing."""

    def test_markup_factor_type_and_value(self):
        from app.core.constants.pricing import MARKUP_FACTOR
        assert isinstance(MARKUP_FACTOR, float)
        assert MARKUP_FACTOR > 0

    def test_fallback_image_url_is_nonempty_string(self):
        from app.core.constants.pricing import FALLBACK_IMAGE_URL
        assert isinstance(FALLBACK_IMAGE_URL, str)
        assert FALLBACK_IMAGE_URL.startswith("http")

    def test_default_certificate(self):
        from app.core.constants.pricing import DEFAULT_CERTIFICATE
        assert isinstance(DEFAULT_CERTIFICATE, str)
        assert len(DEFAULT_CERTIFICATE) > 0

    def test_default_condition(self):
        from app.core.constants.pricing import DEFAULT_CONDITION
        assert isinstance(DEFAULT_CONDITION, str)
        assert len(DEFAULT_CONDITION) > 0

    def test_default_currency(self):
        from app.core.constants.pricing import DEFAULT_CURRENCY
        assert isinstance(DEFAULT_CURRENCY, str)
        assert DEFAULT_CURRENCY == "USD"


# ---------------------------------------------------------------------------
# Publishing constants
# ---------------------------------------------------------------------------

class TestPublishingConstants:
    """Tests for app.core.constants.publishing."""

    def test_metafield_definitions_is_nonempty_list(self):
        from app.core.constants.publishing import METAFIELD_DEFINITIONS
        assert isinstance(METAFIELD_DEFINITIONS, list)
        assert len(METAFIELD_DEFINITIONS) > 0

    def test_metafield_definitions_structure(self):
        from app.core.constants.publishing import METAFIELD_DEFINITIONS
        for defn in METAFIELD_DEFINITIONS:
            assert "namespace" in defn
            assert "key" in defn
            assert "name" in defn
            assert "type" in defn

    def test_product_category_gid_format(self):
        from app.core.constants.publishing import PRODUCT_CATEGORY_GID
        assert isinstance(PRODUCT_CATEGORY_GID, str)
        assert PRODUCT_CATEGORY_GID.startswith("gid://shopify/")

    def test_product_tags_is_nonempty_list(self):
        from app.core.constants.publishing import PRODUCT_TAGS
        assert isinstance(PRODUCT_TAGS, list)
        assert len(PRODUCT_TAGS) > 0
        assert all(isinstance(tag, str) for tag in PRODUCT_TAGS)

    def test_uom_mapping_is_nonempty_dict(self):
        from app.core.constants.publishing import UOM_MAPPING
        assert isinstance(UOM_MAPPING, dict)
        assert len(UOM_MAPPING) > 0
        assert "EA" in UOM_MAPPING

    def test_cert_mapping_is_list_of_tuples(self):
        from app.core.constants.publishing import CERT_MAPPING
        assert isinstance(CERT_MAPPING, list)
        assert len(CERT_MAPPING) > 0
        for keywords, value in CERT_MAPPING:
            assert isinstance(keywords, list)
            assert isinstance(value, str)

    def test_trace_allowed_domains(self):
        from app.core.constants.publishing import TRACE_ALLOWED_DOMAINS
        assert isinstance(TRACE_ALLOWED_DOMAINS, list)
        assert all(d.startswith("https://") for d in TRACE_ALLOWED_DOMAINS)

    def test_metafield_namespaces(self):
        from app.core.constants.publishing import METAFIELD_NAMESPACE, METAFIELD_NAMESPACE_BOEING
        assert isinstance(METAFIELD_NAMESPACE, str)
        assert METAFIELD_NAMESPACE == "custom"
        assert isinstance(METAFIELD_NAMESPACE_BOEING, str)
        assert METAFIELD_NAMESPACE_BOEING == "boeing"


# ---------------------------------------------------------------------------
# Extraction constants
# ---------------------------------------------------------------------------

class TestExtractionConstants:
    """Tests for app.core.constants.extraction."""

    def test_default_supplier(self):
        from app.core.constants.extraction import DEFAULT_SUPPLIER
        assert isinstance(DEFAULT_SUPPLIER, str)
        assert len(DEFAULT_SUPPLIER) > 0

    def test_system_user_id(self):
        from app.core.constants.extraction import SYSTEM_USER_ID
        assert isinstance(SYSTEM_USER_ID, str)
        assert len(SYSTEM_USER_ID) > 0

    def test_default_vendor(self):
        from app.core.constants.extraction import DEFAULT_VENDOR
        assert isinstance(DEFAULT_VENDOR, str)
        assert len(DEFAULT_VENDOR) > 0

    def test_boeing_batch_size_is_positive_int(self):
        from app.core.constants.extraction import BOEING_BATCH_SIZE
        assert isinstance(BOEING_BATCH_SIZE, int)
        assert BOEING_BATCH_SIZE > 0


# ---------------------------------------------------------------------------
# Sync constants
# ---------------------------------------------------------------------------

class TestSyncConstants:
    """Tests for app.core.constants.sync."""

    def test_min_products_for_active_slot(self):
        from app.core.constants.sync import MIN_PRODUCTS_FOR_ACTIVE_SLOT
        assert isinstance(MIN_PRODUCTS_FOR_ACTIVE_SLOT, int)
        assert MIN_PRODUCTS_FOR_ACTIVE_SLOT > 0

    def test_max_skus_per_api_call(self):
        from app.core.constants.sync import MAX_SKUS_PER_API_CALL
        assert isinstance(MAX_SKUS_PER_API_CALL, int)
        assert MAX_SKUS_PER_API_CALL > 0

    def test_stuck_threshold_minutes(self):
        from app.core.constants.sync import STUCK_THRESHOLD_MINUTES
        assert isinstance(STUCK_THRESHOLD_MINUTES, int)
        assert STUCK_THRESHOLD_MINUTES > 0

    def test_eod_stuck_threshold_minutes(self):
        from app.core.constants.sync import EOD_STUCK_THRESHOLD_MINUTES, STUCK_THRESHOLD_MINUTES
        assert isinstance(EOD_STUCK_THRESHOLD_MINUTES, int)
        assert EOD_STUCK_THRESHOLD_MINUTES >= STUCK_THRESHOLD_MINUTES

    def test_default_currency(self):
        from app.core.constants.sync import DEFAULT_CURRENCY
        assert DEFAULT_CURRENCY == "USD"
