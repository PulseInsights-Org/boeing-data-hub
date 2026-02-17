"""
Unit tests for ShopifyUpdateService — sync-time Shopify product updates.

Tests cover:
- update_product raises NonRetryableError when product not found in DB
- update_product raises NonRetryableError when shopify_product_id is missing
- update_product calls update_product_pricing for simple (no location) updates
- update_product calls update_inventory_by_location for location-based updates
- update_product applies MARKUP_FACTOR to price
- update_product records sync success with computed hash
- update_product raises RetryableError when sync DB record fails
- update_product updates product pricing in product store
- update_product returns success dict with correct fields
- update_product builds metafields when location_summary is present
- update_product handles out-of-stock products (is_missing_sku)

Version: 1.0.0
"""
import sys
from unittest.mock import MagicMock as _MagicMock

for _mod in (
    "supabase", "storage3", "storage3.utils",
    "storage3._async", "storage3._async.client", "storage3._async.analytics",
    "postgrest", "postgrest.exceptions",
    "pyiceberg", "pyiceberg.catalog", "pyiceberg.catalog.rest", "pyroaring",
):
    sys.modules.setdefault(_mod, _MagicMock())

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from app.core.exceptions import RetryableError, NonRetryableError
from app.core.constants.pricing import MARKUP_FACTOR
from app.services.shopify_update_service import ShopifyUpdateService


def _make_service():
    """Create a ShopifyUpdateService with mocked dependencies."""
    mock_shopify = MagicMock()
    mock_shopify.update_product_pricing = AsyncMock(return_value={"product": {"id": 99001}})
    mock_shopify.update_inventory_by_location = AsyncMock()

    mock_sync_store = MagicMock()
    mock_sync_store.update_sync_success = MagicMock()
    mock_sync_store.update_sync_failure = MagicMock()

    mock_product_store = MagicMock()
    mock_product_store.get_product_by_sku = AsyncMock(return_value=None)
    mock_product_store.update_product_pricing = AsyncMock()

    service = ShopifyUpdateService(
        shopify=mock_shopify,
        sync_store=mock_sync_store,
        product_store=mock_product_store,
    )
    return service, mock_shopify, mock_sync_store, mock_product_store


def _sample_boeing_data(**overrides):
    """Build a sample boeing_data dict with defaults."""
    data = {
        "list_price": 25.50,
        "net_price": 23.00,
        "inventory_quantity": 100,
        "inventory_status": "in_stock",
        "location_quantities": [
            {"location": "Dallas Central", "quantity": 100},
        ],
        "location_summary": "Dallas Central: 100",
        "is_missing_sku": False,
    }
    data.update(overrides)
    return data


@pytest.mark.unit
class TestUpdateProductNotFound:
    """Verify update_product raises NonRetryableError when product is missing."""

    @pytest.mark.asyncio
    async def test_raises_when_product_not_in_db(self):
        svc, _, mock_sync, mock_products = _make_service()
        mock_products.get_product_by_sku = AsyncMock(return_value=None)

        with pytest.raises(NonRetryableError, match="not found"):
            await svc.update_product("WF338109", "user-1", _sample_boeing_data())

        mock_sync.update_sync_failure.assert_called_once_with(
            "WF338109", "Product not found in database"
        )

    @pytest.mark.asyncio
    async def test_raises_when_no_shopify_product_id(self):
        svc, _, mock_sync, mock_products = _make_service()
        mock_products.get_product_by_sku = AsyncMock(return_value={
            "sku": "WF338109",
            "shopify_product_id": None,
        })

        with pytest.raises(NonRetryableError, match="no Shopify ID"):
            await svc.update_product("WF338109", "user-1", _sample_boeing_data())

        mock_sync.update_sync_failure.assert_called_once_with(
            "WF338109", "No Shopify product ID"
        )


@pytest.mark.unit
class TestUpdateProductPricing:
    """Verify pricing and inventory update paths."""

    @pytest.mark.asyncio
    async def test_uses_location_based_update_when_locations_present(self):
        svc, mock_shopify, _, mock_products = _make_service()
        mock_products.get_product_by_sku = AsyncMock(return_value={
            "sku": "WF338109",
            "shopify_product_id": "99001",
        })

        boeing_data = _sample_boeing_data()
        await svc.update_product("WF338109", "user-1", boeing_data)

        # Should call update_product_pricing WITHOUT quantity (location-based path)
        mock_shopify.update_product_pricing.assert_awaited_once()
        pricing_call = mock_shopify.update_product_pricing.call_args
        assert "quantity" not in pricing_call.kwargs

        # Should call update_inventory_by_location
        mock_shopify.update_inventory_by_location.assert_awaited_once_with(
            "99001", boeing_data["location_quantities"]
        )

    @pytest.mark.asyncio
    async def test_uses_simple_update_when_no_locations(self):
        svc, mock_shopify, _, mock_products = _make_service()
        mock_products.get_product_by_sku = AsyncMock(return_value={
            "sku": "WF338109",
            "shopify_product_id": "99001",
        })

        boeing_data = _sample_boeing_data(location_quantities=[], location_summary=None)
        await svc.update_product("WF338109", "user-1", boeing_data)

        # Should call update_product_pricing WITH quantity (simple path)
        mock_shopify.update_product_pricing.assert_awaited_once()
        pricing_call = mock_shopify.update_product_pricing.call_args
        assert pricing_call.kwargs["quantity"] == 100

        # Should NOT call update_inventory_by_location
        mock_shopify.update_inventory_by_location.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_uses_simple_update_when_out_of_stock(self):
        svc, mock_shopify, _, mock_products = _make_service()
        mock_products.get_product_by_sku = AsyncMock(return_value={
            "sku": "WF338109",
            "shopify_product_id": "99001",
        })

        boeing_data = _sample_boeing_data(
            inventory_status="out_of_stock",
            inventory_quantity=0,
        )
        await svc.update_product("WF338109", "user-1", boeing_data)

        # Out of stock should use simple path (not location-based)
        mock_shopify.update_product_pricing.assert_awaited_once()
        pricing_call = mock_shopify.update_product_pricing.call_args
        assert pricing_call.kwargs["quantity"] == 0
        mock_shopify.update_inventory_by_location.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_uses_simple_update_when_is_missing_sku(self):
        svc, mock_shopify, _, mock_products = _make_service()
        mock_products.get_product_by_sku = AsyncMock(return_value={
            "sku": "WF338109",
            "shopify_product_id": "99001",
        })

        boeing_data = _sample_boeing_data(is_missing_sku=True)
        await svc.update_product("WF338109", "user-1", boeing_data)

        mock_shopify.update_inventory_by_location.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_applies_markup_factor_to_price(self):
        svc, mock_shopify, _, mock_products = _make_service()
        mock_products.get_product_by_sku = AsyncMock(return_value={
            "sku": "WF338109",
            "shopify_product_id": "99001",
        })

        boeing_data = _sample_boeing_data(list_price=100.00)
        await svc.update_product("WF338109", "user-1", boeing_data)

        pricing_call = mock_shopify.update_product_pricing.call_args
        expected_price = round(100.00 * MARKUP_FACTOR, 2)
        assert pricing_call.kwargs["price"] == expected_price

    @pytest.mark.asyncio
    async def test_falls_back_to_net_price_when_no_list_price(self):
        svc, mock_shopify, _, mock_products = _make_service()
        mock_products.get_product_by_sku = AsyncMock(return_value={
            "sku": "WF338109",
            "shopify_product_id": "99001",
        })

        boeing_data = _sample_boeing_data(list_price=None, net_price=50.00, location_quantities=[])
        await svc.update_product("WF338109", "user-1", boeing_data)

        pricing_call = mock_shopify.update_product_pricing.call_args
        expected_price = round(50.00 * MARKUP_FACTOR, 2)
        assert pricing_call.kwargs["price"] == expected_price


@pytest.mark.unit
class TestUpdateProductSyncRecord:
    """Verify sync record updates after Shopify update."""

    @pytest.mark.asyncio
    async def test_records_sync_success(self):
        svc, _, mock_sync, mock_products = _make_service()
        mock_products.get_product_by_sku = AsyncMock(return_value={
            "sku": "WF338109",
            "shopify_product_id": "99001",
        })

        boeing_data = _sample_boeing_data()
        await svc.update_product("WF338109", "user-1", boeing_data)

        mock_sync.update_sync_success.assert_called_once()
        call_args = mock_sync.update_sync_success.call_args
        assert call_args.args[0] == "WF338109"
        # Second arg is the hash
        assert isinstance(call_args.args[1], str)
        assert len(call_args.args[1]) == 16  # SHA-256 truncated to 16 chars

    @pytest.mark.asyncio
    async def test_raises_retryable_when_sync_db_fails(self):
        svc, _, mock_sync, mock_products = _make_service()
        mock_products.get_product_by_sku = AsyncMock(return_value={
            "sku": "WF338109",
            "shopify_product_id": "99001",
        })
        mock_sync.update_sync_success.side_effect = Exception("DB timeout")

        with pytest.raises(RetryableError, match="DB sync record failed"):
            await svc.update_product("WF338109", "user-1", _sample_boeing_data())

    @pytest.mark.asyncio
    async def test_updates_product_pricing_in_product_store(self):
        svc, _, _, mock_products = _make_service()
        mock_products.get_product_by_sku = AsyncMock(return_value={
            "sku": "WF338109",
            "shopify_product_id": "99001",
        })

        boeing_data = _sample_boeing_data(list_price=25.50)
        await svc.update_product("WF338109", "user-1", boeing_data)

        mock_products.update_product_pricing.assert_awaited_once()
        call_kwargs = mock_products.update_product_pricing.call_args
        assert call_kwargs.args[0] == "WF338109"
        assert call_kwargs.args[1] == "user-1"


@pytest.mark.unit
class TestUpdateProductReturnValue:
    """Verify the return dict structure from update_product."""

    @pytest.mark.asyncio
    async def test_returns_success_dict(self):
        svc, _, _, mock_products = _make_service()
        mock_products.get_product_by_sku = AsyncMock(return_value={
            "sku": "WF338109",
            "shopify_product_id": "99001",
        })

        result = await svc.update_product("WF338109", "user-1", _sample_boeing_data())

        assert result["status"] == "success"
        assert result["sku"] == "WF338109"
        assert result["shopify_product_id"] == "99001"
        assert "new_price" in result
        assert "new_quantity" in result
        assert "hash" in result
        assert len(result["hash"]) == 16


@pytest.mark.unit
class TestMetafields:
    """Verify metafield construction for location summary."""

    @pytest.mark.asyncio
    async def test_includes_metafields_when_location_summary_present(self):
        svc, mock_shopify, _, mock_products = _make_service()
        mock_products.get_product_by_sku = AsyncMock(return_value={
            "sku": "WF338109",
            "shopify_product_id": "99001",
        })

        boeing_data = _sample_boeing_data(location_summary="Dallas Central: 100")
        await svc.update_product("WF338109", "user-1", boeing_data)

        pricing_call = mock_shopify.update_product_pricing.call_args
        metafields = pricing_call.kwargs["metafields"]
        assert metafields is not None
        assert len(metafields) == 1
        assert metafields[0]["namespace"] == "boeing"
        assert metafields[0]["key"] == "location_summary"
        assert metafields[0]["value"] == "Dallas Central: 100"

    @pytest.mark.asyncio
    async def test_no_metafields_when_location_summary_absent(self):
        svc, mock_shopify, _, mock_products = _make_service()
        mock_products.get_product_by_sku = AsyncMock(return_value={
            "sku": "WF338109",
            "shopify_product_id": "99001",
        })

        boeing_data = _sample_boeing_data(
            location_summary=None,
            location_quantities=[],
        )
        await svc.update_product("WF338109", "user-1", boeing_data)

        pricing_call = mock_shopify.update_product_pricing.call_args
        assert pricing_call.kwargs["metafields"] is None
