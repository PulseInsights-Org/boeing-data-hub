"""
Unit tests for ShopifyOrchestrator.

Tests product-level CRUD coordination between ShopifyClient
and ShopifyInventoryService: publish, update, find, pricing, and delete.

Version: 1.0.0
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.services.shopify_orchestrator import ShopifyOrchestrator


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_orchestrator(mock_shopify_client, mock_shopify_inventory):
    """Create a ShopifyOrchestrator with mocked dependencies."""
    return ShopifyOrchestrator(mock_shopify_client, mock_shopify_inventory)


def _sample_product_input():
    """Minimal product input dict for orchestrator tests."""
    return {
        "sku": "WF338109",
        "title": "WF338109",
        "name": "GASKET, O-RING",
        "description": "O-Ring Gasket",
        "shopify": {
            "location_quantities": [
                {"location": "Dallas Central", "quantity": 100},
            ],
            "cost_per_item": 25.50,
        },
    }


# ---------------------------------------------------------------------------
# publish_product
# ---------------------------------------------------------------------------

class TestPublishProduct:
    """Tests for ShopifyOrchestrator.publish_product."""

    @pytest.mark.asyncio
    async def test_creates_product_and_sets_category(
        self, mock_shopify_client, mock_shopify_inventory
    ):
        mock_shopify_client.call_shopify = AsyncMock(return_value={
            "product": {
                "id": 99001,
                "handle": "wf338109",
                "variants": [{"id": 55001, "inventory_item_id": 77001}],
            }
        })
        orch = _make_orchestrator(mock_shopify_client, mock_shopify_inventory)

        result = await orch.publish_product(_sample_product_input())

        assert result["product"]["id"] == 99001
        mock_shopify_client.call_shopify.assert_called_once()
        mock_shopify_inventory.set_product_category.assert_called_once_with(99001)

    @pytest.mark.asyncio
    async def test_sets_inventory_levels_when_locations_present(
        self, mock_shopify_client, mock_shopify_inventory
    ):
        mock_shopify_client.call_shopify = AsyncMock(return_value={
            "product": {
                "id": 99001,
                "handle": "wf338109",
                "variants": [{"id": 55001, "inventory_item_id": 77001}],
            }
        })
        orch = _make_orchestrator(mock_shopify_client, mock_shopify_inventory)

        await orch.publish_product(_sample_product_input())
        mock_shopify_inventory.set_inventory_levels.assert_called_once()
        call_args = mock_shopify_inventory.set_inventory_levels.call_args
        assert call_args[0][0] == 77001  # inventory_item_id

    @pytest.mark.asyncio
    async def test_sets_inventory_cost(
        self, mock_shopify_client, mock_shopify_inventory
    ):
        mock_shopify_client.call_shopify = AsyncMock(return_value={
            "product": {
                "id": 99001,
                "handle": "wf338109",
                "variants": [{"id": 55001, "inventory_item_id": 77001}],
            }
        })
        orch = _make_orchestrator(mock_shopify_client, mock_shopify_inventory)

        await orch.publish_product(_sample_product_input())
        mock_shopify_inventory.set_inventory_cost.assert_called_once_with(77001, 25.50)

    @pytest.mark.asyncio
    async def test_no_category_when_no_product_id(
        self, mock_shopify_client, mock_shopify_inventory
    ):
        mock_shopify_client.call_shopify = AsyncMock(return_value={
            "product": {}
        })
        orch = _make_orchestrator(mock_shopify_client, mock_shopify_inventory)

        await orch.publish_product(_sample_product_input())
        mock_shopify_inventory.set_product_category.assert_not_called()


# ---------------------------------------------------------------------------
# update_product
# ---------------------------------------------------------------------------

class TestUpdateProduct:
    """Tests for ShopifyOrchestrator.update_product."""

    @pytest.mark.asyncio
    async def test_updates_existing_product(
        self, mock_shopify_client, mock_shopify_inventory
    ):
        mock_shopify_client.call_shopify = AsyncMock(return_value={
            "product": {
                "id": 99001,
                "variants": [{"id": 55001, "inventory_item_id": 77001}],
            }
        })
        orch = _make_orchestrator(mock_shopify_client, mock_shopify_inventory)

        result = await orch.update_product("99001", _sample_product_input())
        assert result["product"]["id"] == 99001
        # Should set category on update too
        mock_shopify_inventory.set_product_category.assert_called_once_with(99001)


# ---------------------------------------------------------------------------
# find_product_by_sku
# ---------------------------------------------------------------------------

class TestFindProductBySku:
    """Tests for ShopifyOrchestrator.find_product_by_sku."""

    @pytest.mark.asyncio
    async def test_finds_existing_product(
        self, mock_shopify_client, mock_shopify_inventory
    ):
        mock_shopify_client.call_shopify = AsyncMock(return_value={
            "products": [
                {"id": 99001, "variants": [{"sku": "WF338109"}]},
                {"id": 99002, "variants": [{"sku": "AN3-12A"}]},
            ]
        })
        orch = _make_orchestrator(mock_shopify_client, mock_shopify_inventory)

        result = await orch.find_product_by_sku("WF338109")
        assert result == "99001"

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(
        self, mock_shopify_client, mock_shopify_inventory
    ):
        mock_shopify_client.call_shopify = AsyncMock(return_value={
            "products": [
                {"id": 99002, "variants": [{"sku": "AN3-12A"}]},
            ]
        })
        orch = _make_orchestrator(mock_shopify_client, mock_shopify_inventory)

        result = await orch.find_product_by_sku("NONEXISTENT")
        assert result is None


# ---------------------------------------------------------------------------
# get_variant_by_sku
# ---------------------------------------------------------------------------

class TestGetVariantBySku:
    """Tests for ShopifyOrchestrator.get_variant_by_sku."""

    @pytest.mark.asyncio
    async def test_finds_exact_sku_match(
        self, mock_shopify_client, mock_shopify_inventory
    ):
        mock_shopify_client.call_shopify = AsyncMock(return_value={
            "data": {
                "productVariants": {
                    "edges": [
                        {"node": {"id": "gid://shopify/ProductVariant/55001", "sku": "WF338109", "price": "28.05"}},
                    ]
                }
            }
        })
        orch = _make_orchestrator(mock_shopify_client, mock_shopify_inventory)

        result = await orch.get_variant_by_sku("WF338109")
        assert result["sku"] == "WF338109"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_edges(
        self, mock_shopify_client, mock_shopify_inventory
    ):
        mock_shopify_client.call_shopify = AsyncMock(return_value={
            "data": {"productVariants": {"edges": []}}
        })
        orch = _make_orchestrator(mock_shopify_client, mock_shopify_inventory)

        result = await orch.get_variant_by_sku("NONEXISTENT")
        assert result is None


# ---------------------------------------------------------------------------
# update_product_pricing
# ---------------------------------------------------------------------------

class TestUpdateProductPricing:
    """Tests for ShopifyOrchestrator.update_product_pricing."""

    @pytest.mark.asyncio
    async def test_updates_price_on_first_variant(
        self, mock_shopify_client, mock_shopify_inventory
    ):
        mock_shopify_client.call_shopify = AsyncMock(side_effect=[
            # First call: GET product
            {"product": {"variants": [{"id": 55001, "inventory_item_id": 77001}]}},
            # Second call: PUT product
            {"product": {"id": 99001}},
        ])
        orch = _make_orchestrator(mock_shopify_client, mock_shopify_inventory)

        result = await orch.update_product_pricing("99001", price=30.0)
        assert result["product"]["id"] == 99001

    @pytest.mark.asyncio
    async def test_raises_404_when_no_variants(
        self, mock_shopify_client, mock_shopify_inventory
    ):
        mock_shopify_client.call_shopify = AsyncMock(return_value={
            "product": {"variants": []}
        })
        orch = _make_orchestrator(mock_shopify_client, mock_shopify_inventory)

        with pytest.raises(HTTPException) as exc_info:
            await orch.update_product_pricing("99001", price=30.0)
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# update_inventory
# ---------------------------------------------------------------------------

class TestUpdateInventory:
    """Tests for ShopifyOrchestrator.update_inventory."""

    @pytest.mark.asyncio
    async def test_sets_inventory_at_fallback_location(
        self, mock_shopify_client, mock_shopify_inventory
    ):
        mock_shopify_inventory.get_location_map = AsyncMock(
            return_value={"Dallas Central": 1001}
        )
        mock_shopify_client.call_shopify = AsyncMock(return_value={})
        orch = _make_orchestrator(mock_shopify_client, mock_shopify_inventory)

        await orch.update_inventory("99001", 200, inventory_item_id=77001)
        mock_shopify_client.call_shopify.assert_called_once_with(
            "POST", "/inventory_levels/set.json",
            json={"location_id": 1001, "inventory_item_id": 77001, "available": 200}
        )


# ---------------------------------------------------------------------------
# update_inventory_by_location
# ---------------------------------------------------------------------------

class TestUpdateInventoryByLocation:
    """Tests for ShopifyOrchestrator.update_inventory_by_location."""

    @pytest.mark.asyncio
    async def test_delegates_to_inventory_service(
        self, mock_shopify_client, mock_shopify_inventory
    ):
        mock_shopify_client.call_shopify = AsyncMock(return_value={
            "product": {"variants": [{"id": 55001, "inventory_item_id": 77001}]}
        })
        orch = _make_orchestrator(mock_shopify_client, mock_shopify_inventory)

        locations = [{"location": "Dallas Central", "quantity": 100}]
        await orch.update_inventory_by_location("99001", locations)
        mock_shopify_inventory.set_inventory_levels.assert_called_once_with(77001, locations)

    @pytest.mark.asyncio
    async def test_empty_locations_returns_early(
        self, mock_shopify_client, mock_shopify_inventory
    ):
        orch = _make_orchestrator(mock_shopify_client, mock_shopify_inventory)
        await orch.update_inventory_by_location("99001", [])
        mock_shopify_client.call_shopify.assert_not_called()


# ---------------------------------------------------------------------------
# delete_product
# ---------------------------------------------------------------------------

class TestDeleteProduct:
    """Tests for ShopifyOrchestrator.delete_product delegation."""

    @pytest.mark.asyncio
    async def test_delegates_to_client(
        self, mock_shopify_client, mock_shopify_inventory
    ):
        mock_shopify_client.delete_product = AsyncMock(return_value=True)
        orch = _make_orchestrator(mock_shopify_client, mock_shopify_inventory)

        result = await orch.delete_product(99001)
        assert result is True
        mock_shopify_client.delete_product.assert_called_once_with(99001)
