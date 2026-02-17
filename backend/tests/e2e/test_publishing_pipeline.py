"""
End-to-end tests for the publishing pipeline.

Tests the full flow: staging record -> publish -> Shopify product.
Mocks the Shopify orchestrator and verifies the pipeline from staging
to published product with image upload, location mapping, and sync schedule.
Version: 1.0.0
"""
import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

os.environ.setdefault("AUTO_START_CELERY", "false")


def _make_staging_record():
    """Create a sample staging record for publishing tests."""
    return {
        "sku": "WF338109",
        "title": "WF338109",
        "boeing_name": "GASKET, O-RING",
        "boeing_description": "O-Ring Gasket for hydraulic system",
        "vendor": "BDI",
        "supplier_name": "Aviall",
        "list_price": 25.50,
        "net_price": 23.00,
        "currency": "USD",
        "base_uom": "EA",
        "inventory_quantity": 150,
        "country_of_origin": "US",
        "dim_length": 2.5,
        "dim_width": 1.0,
        "dim_height": 0.5,
        "dim_uom": "IN",
        "weight": 0.1,
        "weight_unit": "lb",
        "condition": "NE",
        "location_summary": "Dallas Central: 100; Chicago Warehouse: 50",
        "location_availabilities": [
            {"location": "Dallas Central", "quantity": 100},
            {"location": "Chicago Warehouse", "quantity": 50},
        ],
        "boeing_image_url": "https://boeing.com/images/WF338109.jpg",
        "boeing_thumbnail_url": "https://boeing.com/thumbs/WF338109.jpg",
        "shopify_product_id": None,
        "image_url": None,
        "image_path": None,
        "body_html": "<p>O-Ring Gasket for hydraulic system</p>",
        "user_id": "test-user-id",
    }


def _make_mock_settings():
    """Create mock settings with location mapping."""
    settings = MagicMock()
    settings.shopify_location_map = {"Dallas Central": "Dallas Central"}
    settings.shopify_inventory_location_codes = {"Dallas Central": "1D1"}
    return settings


@pytest.mark.e2e
class TestPublishingPipelineCreate:
    """E2E tests for creating a new Shopify product from staging."""

    @pytest.mark.asyncio
    async def test_publish_new_product_full_flow(self):
        """Full publish flow: staging -> image upload -> Shopify create -> DB save."""
        from app.services.publishing_service import PublishingService

        record = _make_staging_record()

        mock_shopify = MagicMock()
        mock_shopify.find_product_by_sku = AsyncMock(return_value=None)
        mock_shopify.publish_product = AsyncMock(return_value={
            "product": {"id": 99001, "handle": "wf338109"},
        })
        mock_shopify.update_product = AsyncMock()
        mock_shopify.delete_product = AsyncMock()

        mock_staging = MagicMock()
        mock_staging.get_product_staging_by_part_number = AsyncMock(return_value=record)
        mock_staging.update_product_staging_shopify_id = AsyncMock()
        mock_staging.update_product_staging_image = AsyncMock()

        mock_products = MagicMock()
        mock_products.upsert_product = AsyncMock()

        mock_images = MagicMock()
        mock_images.upload_image_from_url = AsyncMock(return_value=(
            "https://test.supabase.co/storage/WF338109.jpg",
            "products/WF338109.jpg",
        ))

        mock_sync = MagicMock()
        mock_sync.upsert_sync_schedule = MagicMock()

        service = PublishingService(
            shopify=mock_shopify,
            staging_store=mock_staging,
            product_store=mock_products,
            image_store=mock_images,
            sync_store=mock_sync,
            settings=_make_mock_settings(),
        )

        result = await service.publish_product_for_batch(
            record, "WF338109", user_id="test-user-id"
        )

        assert result["success"] is True
        assert result["shopify_product_id"] == "99001"
        assert result["action"] == "created"
        assert result["is_new_product"] is True

        # Verify image was uploaded
        mock_images.upload_image_from_url.assert_called_once()

        # Verify product was saved to DB
        mock_products.upsert_product.assert_called_once()

        # Verify staging was updated with Shopify ID
        mock_staging.update_product_staging_shopify_id.assert_called_once()

        # Verify sync schedule was created
        mock_sync.upsert_sync_schedule.assert_called_once()

    @pytest.mark.asyncio
    async def test_publish_existing_product_updates_shopify(self):
        """Publish a product that already has a Shopify ID should UPDATE."""
        from app.services.publishing_service import PublishingService

        record = _make_staging_record()
        record["shopify_product_id"] = "existing-99001"

        mock_shopify = MagicMock()
        mock_shopify.update_product = AsyncMock(return_value={
            "product": {"id": "existing-99001"},
        })

        mock_staging = MagicMock()
        mock_staging.update_product_staging_shopify_id = AsyncMock()
        mock_staging.update_product_staging_image = AsyncMock()

        mock_products = MagicMock()
        mock_products.upsert_product = AsyncMock()

        mock_images = MagicMock()
        mock_images.upload_image_from_url = AsyncMock(return_value=(
            "https://test.supabase.co/storage/WF338109.jpg",
            "products/WF338109.jpg",
        ))

        mock_sync = MagicMock()
        mock_sync.upsert_sync_schedule = MagicMock()

        service = PublishingService(
            shopify=mock_shopify,
            staging_store=mock_staging,
            product_store=mock_products,
            image_store=mock_images,
            sync_store=mock_sync,
            settings=_make_mock_settings(),
        )

        result = await service.publish_product_for_batch(
            record, "WF338109", user_id="test-user-id"
        )

        assert result["success"] is True
        assert result["action"] == "updated"
        assert result["is_new_product"] is False

        # Should call update, not create
        mock_shopify.update_product.assert_called_once()
        mock_shopify.publish_product.assert_not_called()


@pytest.mark.e2e
class TestPublishingPipelineValidation:
    """E2E tests for publishing validation logic."""

    @pytest.mark.asyncio
    async def test_publish_rejects_zero_price(self):
        """Publishing a product with zero price should raise NonRetryableError."""
        from app.services.publishing_service import PublishingService
        from app.core.exceptions import NonRetryableError

        record = _make_staging_record()
        record["list_price"] = 0
        record["net_price"] = 0
        record["price"] = 0
        record["cost_per_item"] = 0

        service = PublishingService(
            shopify=MagicMock(),
            staging_store=MagicMock(),
            product_store=MagicMock(),
            image_store=MagicMock(),
            sync_store=MagicMock(),
            settings=_make_mock_settings(),
        )

        with pytest.raises(NonRetryableError, match="no valid price"):
            await service.publish_product_for_batch(
                record, "WF338109", user_id="test-user-id"
            )

    @pytest.mark.asyncio
    async def test_publish_rejects_zero_inventory(self):
        """Publishing a product with zero inventory should raise NonRetryableError."""
        from app.services.publishing_service import PublishingService
        from app.core.exceptions import NonRetryableError

        record = _make_staging_record()
        record["inventory_quantity"] = 0

        service = PublishingService(
            shopify=MagicMock(),
            staging_store=MagicMock(),
            product_store=MagicMock(),
            image_store=MagicMock(),
            sync_store=MagicMock(),
            settings=_make_mock_settings(),
        )

        with pytest.raises(NonRetryableError, match="no inventory"):
            await service.publish_product_for_batch(
                record, "WF338109", user_id="test-user-id"
            )

    @pytest.mark.asyncio
    async def test_publish_rejects_unmapped_locations_only(self):
        """Publishing a product only at non-mapped locations should raise NonRetryableError."""
        from app.services.publishing_service import PublishingService
        from app.core.exceptions import NonRetryableError

        record = _make_staging_record()
        record["location_availabilities"] = [
            {"location": "Tokyo Warehouse", "quantity": 100},
        ]
        record["location_summary"] = "Tokyo Warehouse: 100"

        service = PublishingService(
            shopify=MagicMock(),
            staging_store=MagicMock(),
            product_store=MagicMock(),
            image_store=MagicMock(),
            sync_store=MagicMock(),
            settings=_make_mock_settings(),
        )

        with pytest.raises(NonRetryableError, match="non-mapped locations"):
            await service.publish_product_for_batch(
                record, "WF338109", user_id="test-user-id"
            )


@pytest.mark.e2e
class TestPublishingPipelineSagaCompensation:
    """E2E tests for saga compensation on DB failure."""

    @pytest.mark.asyncio
    async def test_db_failure_on_new_product_triggers_shopify_delete(self):
        """If DB save fails after Shopify CREATE, should delete from Shopify."""
        from app.services.publishing_service import PublishingService

        record = _make_staging_record()

        mock_shopify = MagicMock()
        mock_shopify.find_product_by_sku = AsyncMock(return_value=None)
        mock_shopify.publish_product = AsyncMock(return_value={
            "product": {"id": 99001},
        })
        mock_shopify.delete_product = AsyncMock(return_value=True)

        mock_staging = MagicMock()
        mock_staging.update_product_staging_shopify_id = AsyncMock()
        mock_staging.update_product_staging_image = AsyncMock()

        mock_products = MagicMock()
        mock_products.upsert_product = AsyncMock(
            side_effect=RuntimeError("DB connection lost")
        )

        mock_images = MagicMock()
        mock_images.upload_image_from_url = AsyncMock(return_value=(
            "https://test.supabase.co/storage/img.jpg",
            "products/img.jpg",
        ))

        service = PublishingService(
            shopify=mock_shopify,
            staging_store=mock_staging,
            product_store=mock_products,
            image_store=mock_images,
            sync_store=MagicMock(),
            settings=_make_mock_settings(),
        )

        with pytest.raises(RuntimeError, match="DB connection lost"):
            await service.publish_product_for_batch(
                record, "WF338109", user_id="test-user-id"
            )

        # Verify compensation: Shopify product was deleted
        mock_shopify.delete_product.assert_called_once_with(99001)
