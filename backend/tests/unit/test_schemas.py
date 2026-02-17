"""
Unit tests for Pydantic schemas.

Tests valid construction and validation errors for schemas
in extraction, publishing, batches, search, and sync modules.

Version: 1.0.0
"""
import pytest
from datetime import datetime

from pydantic import ValidationError


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Extraction schemas
# ---------------------------------------------------------------------------

class TestExtractionSchemas:
    """Tests for app.schemas.extraction."""

    def test_extraction_search_response_valid(self):
        from app.schemas.extraction import ExtractionSearchResponse
        resp = ExtractionSearchResponse(products=[])
        assert resp.products == []

    def test_extraction_search_response_with_product(self):
        from app.schemas.extraction import ExtractionSearchResponse
        from app.schemas.products import NormalizedProduct
        product = NormalizedProduct(sku="WF338109", title="Test")
        resp = ExtractionSearchResponse(products=[product])
        assert len(resp.products) == 1
        assert resp.products[0].sku == "WF338109"

    def test_extraction_search_response_invalid_products_type(self):
        from app.schemas.extraction import ExtractionSearchResponse
        with pytest.raises(ValidationError):
            ExtractionSearchResponse(products="not-a-list")

    def test_backward_compat_alias(self):
        from app.schemas.extraction import BoeingSearchResponse, ExtractionSearchResponse
        assert BoeingSearchResponse is ExtractionSearchResponse


# ---------------------------------------------------------------------------
# Publishing schemas
# ---------------------------------------------------------------------------

class TestPublishingSchemas:
    """Tests for app.schemas.publishing."""

    def test_publish_request_valid(self):
        from app.schemas.publishing import PublishRequest
        req = PublishRequest(part_number="WF338109")
        assert req.part_number == "WF338109"
        assert req.batch_id is None

    def test_publish_request_with_batch_id(self):
        from app.schemas.publishing import PublishRequest
        req = PublishRequest(part_number="WF338109", batch_id="batch-001")
        assert req.batch_id == "batch-001"

    def test_publish_request_missing_part_number(self):
        from app.schemas.publishing import PublishRequest
        with pytest.raises(ValidationError):
            PublishRequest()

    def test_publish_response_valid(self):
        from app.schemas.publishing import PublishResponse
        resp = PublishResponse(success=True, shopifyProductId="99001")
        assert resp.success is True
        assert resp.shopifyProductId == "99001"

    def test_check_response_valid(self):
        from app.schemas.publishing import CheckResponse
        resp = CheckResponse(shopifyProductId="99001")
        assert resp.shopifyProductId == "99001"

    def test_check_response_none_id(self):
        from app.schemas.publishing import CheckResponse
        resp = CheckResponse()
        assert resp.shopifyProductId is None

    def test_backward_compat_aliases(self):
        from app.schemas.publishing import (
            ShopifyPublishRequest, PublishRequest,
            ShopifyPublishResponse, PublishResponse,
        )
        assert ShopifyPublishRequest is PublishRequest
        assert ShopifyPublishResponse is PublishResponse


# ---------------------------------------------------------------------------
# Batch schemas
# ---------------------------------------------------------------------------

class TestBatchSchemas:
    """Tests for app.schemas.batches."""

    def test_bulk_search_request_with_list(self):
        from app.schemas.batches import BulkSearchRequest
        req = BulkSearchRequest(part_numbers=["WF338109", "AN3-12A"])
        assert len(req.part_numbers) == 2
        # Validator uppercases part numbers
        assert req.part_numbers[0] == "WF338109"

    def test_bulk_search_request_with_text(self):
        from app.schemas.batches import BulkSearchRequest
        req = BulkSearchRequest(part_numbers_text="WF338109,AN3-12A")
        assert len(req.part_numbers) == 2

    def test_bulk_search_request_both_fields_raises(self):
        from app.schemas.batches import BulkSearchRequest
        with pytest.raises(ValidationError):
            BulkSearchRequest(
                part_numbers=["WF338109"],
                part_numbers_text="AN3-12A"
            )

    def test_bulk_search_request_empty_raises(self):
        from app.schemas.batches import BulkSearchRequest
        with pytest.raises(ValidationError):
            BulkSearchRequest()

    def test_bulk_publish_request_valid(self):
        from app.schemas.batches import BulkPublishRequest
        req = BulkPublishRequest(part_numbers=["WF338109"])
        assert len(req.part_numbers) == 1

    def test_bulk_operation_response_valid(self):
        from app.schemas.batches import BulkOperationResponse
        resp = BulkOperationResponse(
            batch_id="batch-001",
            total_items=5,
            status="processing",
            message="Batch started",
        )
        assert resp.batch_id == "batch-001"
        assert resp.total_items == 5

    def test_batch_status_response_valid(self):
        from app.schemas.batches import BatchStatusResponse
        now = datetime.utcnow()
        resp = BatchStatusResponse(
            id="batch-001",
            batch_type="extract",
            status="processing",
            total_items=5,
            extracted_count=3,
            normalized_count=2,
            published_count=1,
            failed_count=0,
            progress_percent=60.0,
            created_at=now,
            updated_at=now,
        )
        assert resp.id == "batch-001"
        assert resp.progress_percent == 60.0

    def test_failed_item_valid(self):
        from app.schemas.batches import FailedItem
        item = FailedItem(part_number="WF338109", error="timeout")
        assert item.part_number == "WF338109"
        assert item.timestamp is None


# ---------------------------------------------------------------------------
# Search schemas
# ---------------------------------------------------------------------------

class TestSearchSchemas:
    """Tests for app.schemas.search."""

    def test_multi_part_search_request_valid(self):
        from app.schemas.search import MultiPartSearchRequest
        req = MultiPartSearchRequest(part_numbers=["WF338109", "AN3-12A"])
        assert len(req.part_numbers) == 2

    def test_multi_part_search_request_empty_raises(self):
        from app.schemas.search import MultiPartSearchRequest
        with pytest.raises(ValidationError):
            MultiPartSearchRequest(part_numbers=[])

    def test_multi_part_search_request_too_many_raises(self):
        from app.schemas.search import MultiPartSearchRequest, MAX_SKUS_ALLOWED
        with pytest.raises(ValidationError):
            MultiPartSearchRequest(part_numbers=["sku"] * (MAX_SKUS_ALLOWED + 1))

    def test_found_product_valid(self):
        from app.schemas.search import FoundProduct
        product = FoundProduct(
            sku="WF338109",
            shopify_product_id="99001",
            shopify_variant_id="55001",
            title="WF338109",
            handle="wf338109",
            status="active",
        )
        assert product.sku == "WF338109"
        assert product.tags == []
        assert product.images == []

    def test_search_summary_valid(self):
        from app.schemas.search import SearchSummary
        summary = SearchSummary(
            total_requested=10,
            unique_searched=8,
            found=5,
            not_found=3,
            duplicates_removed=2,
        )
        assert summary.found == 5

    def test_multi_part_search_response_valid(self):
        from app.schemas.search import MultiPartSearchResponse, SearchSummary
        resp = MultiPartSearchResponse(
            success=True,
            found_products=[],
            not_found_skus=["MISSING"],
            summary=SearchSummary(
                total_requested=1,
                unique_searched=1,
                found=0,
                not_found=1,
                duplicates_removed=0,
            ),
        )
        assert resp.success is True
        assert len(resp.not_found_skus) == 1


# ---------------------------------------------------------------------------
# Sync schemas
# ---------------------------------------------------------------------------

class TestSyncSchemas:
    """Tests for app.schemas.sync."""

    def test_sync_status_counts_defaults(self):
        from app.schemas.sync import SyncStatusCounts
        counts = SyncStatusCounts()
        assert counts.pending == 0
        assert counts.syncing == 0
        assert counts.success == 0
        assert counts.failed == 0

    def test_slot_info_valid(self):
        from app.schemas.sync import SlotInfo
        slot = SlotInfo(hour=14, count=25, status="active")
        assert slot.hour == 14
        assert slot.status == "active"

    def test_slot_info_missing_status_raises(self):
        from app.schemas.sync import SlotInfo
        with pytest.raises(ValidationError):
            SlotInfo(hour=14, count=25)

    def test_sync_product_valid(self):
        from app.schemas.sync import SyncProduct
        product = SyncProduct(
            id="sp-001",
            sku="WF338109",
            user_id="test-user",
            hour_bucket=14,
            sync_status="pending",
            last_sync_at=None,
            consecutive_failures=0,
            last_error=None,
            last_price=25.50,
            last_quantity=100,
            last_inventory_status="in_stock",
            last_location_summary="Dallas: 100",
            is_active=True,
            created_at="2025-01-15T10:00:00Z",
            updated_at="2025-01-15T10:00:00Z",
        )
        assert product.sku == "WF338109"
        assert product.is_active is True

    def test_sync_dashboard_response_valid(self):
        from app.schemas.sync import SyncDashboardResponse, SyncStatusCounts
        resp = SyncDashboardResponse(
            total_products=100,
            active_products=80,
            inactive_products=20,
            success_rate_percent=95.0,
            high_failure_count=2,
            status_counts=SyncStatusCounts(pending=5, syncing=2, success=90, failed=3),
            current_hour=14,
            current_hour_products=10,
            sync_mode="production",
            max_buckets=24,
            slot_distribution=[],
            active_slots=10,
            filling_slots=5,
            dormant_slots=9,
            efficiency_percent=62.5,
            last_updated="2025-01-15T10:00:00Z",
        )
        assert resp.total_products == 100
        assert resp.efficiency_percent == 62.5

    def test_failed_product_valid(self):
        from app.schemas.sync import FailedProduct
        fp = FailedProduct(
            sku="WF338109",
            consecutive_failures=3,
            last_error="timeout",
            last_sync_at="2025-01-15T10:00:00Z",
            hour_bucket=14,
            is_active=True,
        )
        assert fp.consecutive_failures == 3

    def test_hourly_stats_valid(self):
        from app.schemas.sync import HourlyStats
        stats = HourlyStats(hour=14, total=50, pending=10, syncing=5, success=30, failed=5)
        assert stats.total == 50

    def test_sync_products_response_valid(self):
        from app.schemas.sync import SyncProductsResponse
        resp = SyncProductsResponse(products=[], total=0, limit=50, offset=0)
        assert resp.total == 0
        assert resp.products == []


# ---------------------------------------------------------------------------
# Product schemas (shared base)
# ---------------------------------------------------------------------------

class TestProductSchemas:
    """Tests for app.schemas.products base models."""

    def test_normalized_product_minimal(self):
        from app.schemas.products import NormalizedProduct
        product = NormalizedProduct()
        assert product.sku is None
        assert product.shopify is None

    def test_normalized_product_with_fields(self):
        from app.schemas.products import NormalizedProduct
        product = NormalizedProduct(
            sku="WF338109",
            title="WF338109",
            list_price=25.50,
            inventory_quantity=100,
            condition="NE",
        )
        assert product.sku == "WF338109"
        assert product.list_price == 25.50

    def test_location_availability_model(self):
        from app.schemas.products import LocationAvailability
        loc = LocationAvailability(location="Dallas Central", avail_quantity=100)
        assert loc.location == "Dallas Central"
        assert loc.avail_quantity == 100

    def test_shopify_product_model_all_optional(self):
        from app.schemas.products import ShopifyProductModel
        model = ShopifyProductModel()
        assert model.title is None
        assert model.sku is None
        assert model.price is None
