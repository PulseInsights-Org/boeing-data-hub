"""
Unit tests for PublishingService.

Tests publish_product_by_part_number, publish_product_for_batch,
and helper functions strip_variant_suffix, prepare_shopify_record,
_parse_location_summary.

Version: 1.0.0
"""
import sys
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Mock the supabase import chain that causes ImportError (missing pyiceberg).
# This must happen before any app.db.* or app.services.publishing_service import.
# ---------------------------------------------------------------------------
_supabase_mock = MagicMock()
_modules_to_mock = [
    "supabase",
    "storage3",
    "storage3.utils",
    "storage3._async",
    "storage3._async.client",
    "storage3._async.analytics",
    "postgrest",
    "postgrest.exceptions",
]
for mod in _modules_to_mock:
    if mod not in sys.modules:
        sys.modules[mod] = _supabase_mock

import pytest
from unittest.mock import AsyncMock

from fastapi import HTTPException

from app.core.exceptions import NonRetryableError
from app.services.publishing_service import (
    PublishingService,
    strip_variant_suffix,
    prepare_shopify_record,
    _parse_location_summary,
)


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(
    mock_shopify_orchestrator,
    mock_staging_store,
    mock_product_store,
    mock_image_store,
    mock_sync_store=None,
    mock_settings=None,
):
    """Create a PublishingService with mocked dependencies."""
    return PublishingService(
        shopify=mock_shopify_orchestrator,
        staging_store=mock_staging_store,
        product_store=mock_product_store,
        image_store=mock_image_store,
        sync_store=mock_sync_store,
        settings=mock_settings,
    )


# ---------------------------------------------------------------------------
# strip_variant_suffix
# ---------------------------------------------------------------------------

class TestStripVariantSuffix:
    """Tests for the strip_variant_suffix helper."""

    def test_strips_suffix(self):
        assert strip_variant_suffix("WF338109=K3") == "WF338109"

    def test_no_suffix_unchanged(self):
        assert strip_variant_suffix("WF338109") == "WF338109"

    def test_empty_returns_empty(self):
        assert strip_variant_suffix("") == ""

    def test_none_returns_empty(self):
        assert strip_variant_suffix(None) == ""

    def test_multiple_equals_strips_first(self):
        assert strip_variant_suffix("WF338109=K3=X") == "WF338109"


# ---------------------------------------------------------------------------
# prepare_shopify_record
# ---------------------------------------------------------------------------

class TestPrepareShopifyRecord:
    """Tests for the prepare_shopify_record helper."""

    def test_populates_shopify_dict(self, sample_boeing_record):
        result = prepare_shopify_record(dict(sample_boeing_record))
        assert "shopify" in result
        assert result["shopify"]["sku"] == "WF338109"
        assert result["shopify"]["title"] == "WF338109"

    def test_calculates_price_from_list_price(self, sample_boeing_record):
        record = dict(sample_boeing_record)
        result = prepare_shopify_record(record)
        from app.core.constants.pricing import MARKUP_FACTOR
        expected = record["list_price"] * MARKUP_FACTOR
        assert abs(result["shopify"]["price"] - expected) < 0.01

    def test_falls_back_to_net_price(self, sample_boeing_record):
        record = dict(sample_boeing_record)
        record["list_price"] = None
        result = prepare_shopify_record(record)
        from app.core.constants.pricing import MARKUP_FACTOR
        expected = record["net_price"] * MARKUP_FACTOR
        assert abs(result["shopify"]["price"] - expected) < 0.01

    def test_sets_name_from_boeing_name(self, sample_boeing_record):
        record = dict(sample_boeing_record)
        result = prepare_shopify_record(record)
        assert result["name"] == "GASKET, O-RING"

    def test_strips_variant_suffix_from_sku(self):
        record = {
            "sku": "WF338109=K3",
            "title": "WF338109=K3",
            "boeing_name": "Test Part",
        }
        result = prepare_shopify_record(record)
        assert result["shopify"]["sku"] == "WF338109"
        assert result["shopify"]["title"] == "WF338109"


# ---------------------------------------------------------------------------
# _parse_location_summary
# ---------------------------------------------------------------------------

class TestParseLocationSummary:
    """Tests for the _parse_location_summary helper."""

    def test_single_location(self):
        result = _parse_location_summary("Dallas Central: 243")
        assert result == [{"location": "Dallas Central", "quantity": 243}]

    def test_multiple_locations(self):
        result = _parse_location_summary("Dallas Central: 243; Miami, FL: 10")
        assert len(result) == 2
        assert result[0] == {"location": "Dallas Central", "quantity": 243}
        assert result[1] == {"location": "Miami, FL", "quantity": 10}

    def test_empty_returns_empty_list(self):
        assert _parse_location_summary("") == []
        assert _parse_location_summary(None) == []

    def test_invalid_quantity_defaults_to_zero(self):
        result = _parse_location_summary("Dallas Central: abc")
        assert result == [{"location": "Dallas Central", "quantity": 0}]


# ---------------------------------------------------------------------------
# publish_product_by_part_number
# ---------------------------------------------------------------------------

class TestPublishProductByPartNumber:
    """Tests for the route-level publish flow."""

    @pytest.mark.asyncio
    async def test_happy_path(
        self, mock_shopify_orchestrator, mock_staging_store,
        mock_product_store, mock_image_store
    ):
        mock_staging_store.get_product_staging_by_part_number = AsyncMock(return_value={
            "sku": "WF338109",
            "title": "WF338109",
            "boeing_name": "GASKET, O-RING",
            "list_price": 25.50,
            "net_price": 23.00,
            "base_uom": "EA",
            "inventory_quantity": 100,
            "condition": "NE",
            "shopify_product_id": None,
        })
        mock_shopify_orchestrator.find_product_by_sku = AsyncMock(return_value=None)
        mock_shopify_orchestrator.publish_product = AsyncMock(return_value={
            "product": {"id": 99001, "handle": "wf338109"}
        })
        mock_image_store.upload_image_from_url = AsyncMock(
            return_value=("https://cdn.test/img.png", "products/img.png")
        )

        svc = _make_service(
            mock_shopify_orchestrator, mock_staging_store,
            mock_product_store, mock_image_store,
        )

        result = await svc.publish_product_by_part_number("WF338109")
        assert result["success"] is True
        assert result["shopifyProductId"] == "99001"

    @pytest.mark.asyncio
    async def test_404_when_not_in_staging(
        self, mock_shopify_orchestrator, mock_staging_store,
        mock_product_store, mock_image_store
    ):
        mock_staging_store.get_product_staging_by_part_number = AsyncMock(return_value=None)

        svc = _make_service(
            mock_shopify_orchestrator, mock_staging_store,
            mock_product_store, mock_image_store,
        )

        with pytest.raises(HTTPException) as exc_info:
            await svc.publish_product_by_part_number("NONEXISTENT")
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# publish_product_for_batch
# ---------------------------------------------------------------------------

class TestPublishProductForBatch:
    """Tests for the batch-level publish flow with saga compensation."""

    @pytest.mark.asyncio
    async def test_happy_path_create(
        self, mock_shopify_orchestrator, mock_staging_store,
        mock_product_store, mock_image_store, mock_settings
    ):
        mock_shopify_orchestrator.find_product_by_sku = AsyncMock(return_value=None)
        mock_shopify_orchestrator.publish_product = AsyncMock(return_value={
            "product": {"id": 99001, "handle": "wf338109"}
        })
        mock_image_store.upload_image_from_url = AsyncMock(
            return_value=("https://cdn.test/img.png", "products/img.png")
        )

        svc = _make_service(
            mock_shopify_orchestrator, mock_staging_store,
            mock_product_store, mock_image_store,
            mock_settings=mock_settings,
        )

        record = {
            "sku": "WF338109",
            "title": "WF338109",
            "boeing_name": "GASKET, O-RING",
            "list_price": 25.50,
            "net_price": 23.00,
            "price": 25.50,
            "inventory_quantity": 100,
            "condition": "NE",
            "base_uom": "EA",
            "location_summary": "Dallas Central: 100",
            "location_availabilities": [
                {"location": "Dallas Central", "quantity": 100},
            ],
            "boeing_image_url": "https://boeing.com/img.jpg",
        }

        result = await svc.publish_product_for_batch(record, "WF338109")
        assert result["success"] is True
        assert result["action"] == "created"
        assert result["is_new_product"] is True

    @pytest.mark.asyncio
    async def test_non_retryable_error_on_no_price(
        self, mock_shopify_orchestrator, mock_staging_store,
        mock_product_store, mock_image_store
    ):
        svc = _make_service(
            mock_shopify_orchestrator, mock_staging_store,
            mock_product_store, mock_image_store,
        )

        record = {
            "sku": "WF338109",
            "price": None,
            "list_price": None,
            "net_price": None,
            "cost_per_item": None,
            "inventory_quantity": 100,
        }

        with pytest.raises(NonRetryableError, match="no valid price"):
            await svc.publish_product_for_batch(record, "WF338109")

    @pytest.mark.asyncio
    async def test_non_retryable_error_on_no_inventory(
        self, mock_shopify_orchestrator, mock_staging_store,
        mock_product_store, mock_image_store
    ):
        svc = _make_service(
            mock_shopify_orchestrator, mock_staging_store,
            mock_product_store, mock_image_store,
        )

        record = {
            "sku": "WF338109",
            "price": 25.50,
            "inventory_quantity": 0,
        }

        with pytest.raises(NonRetryableError, match="no inventory"):
            await svc.publish_product_for_batch(record, "WF338109")

    @pytest.mark.asyncio
    async def test_saga_compensation_on_db_failure(
        self, mock_shopify_orchestrator, mock_staging_store,
        mock_product_store, mock_image_store, mock_settings
    ):
        """When DB save fails after CREATE, should delete the Shopify product."""
        mock_shopify_orchestrator.find_product_by_sku = AsyncMock(return_value=None)
        mock_shopify_orchestrator.publish_product = AsyncMock(return_value={
            "product": {"id": 99001, "handle": "wf338109"}
        })
        mock_product_store.upsert_product = AsyncMock(
            side_effect=Exception("DB connection lost")
        )
        mock_shopify_orchestrator.delete_product = AsyncMock(return_value=True)
        mock_image_store.upload_image_from_url = AsyncMock(
            return_value=("https://cdn.test/img.png", "products/img.png")
        )

        svc = _make_service(
            mock_shopify_orchestrator, mock_staging_store,
            mock_product_store, mock_image_store,
            mock_settings=mock_settings,
        )

        record = {
            "sku": "WF338109",
            "title": "WF338109",
            "boeing_name": "GASKET",
            "list_price": 25.50,
            "price": 25.50,
            "inventory_quantity": 100,
            "condition": "NE",
            "base_uom": "EA",
            "location_summary": "Dallas Central: 100",
            "location_availabilities": [
                {"location": "Dallas Central", "quantity": 100},
            ],
        }

        with pytest.raises(Exception, match="DB connection lost"):
            await svc.publish_product_for_batch(record, "WF338109")

        # Verify saga compensation: delete_product was called
        mock_shopify_orchestrator.delete_product.assert_called_once_with(99001)
