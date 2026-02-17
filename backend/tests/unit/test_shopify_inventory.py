"""
Unit tests for ShopifyInventoryService.

Tests location mapping, inventory level setting with location lookup
and fallback, inventory cost, product category, and metafield definitions.

Version: 1.0.0
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException

from app.services.shopify_inventory_service import ShopifyInventoryService


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(
    mock_shopify_client,
    location_map_setting: dict | None = None,
) -> ShopifyInventoryService:
    """Create a ShopifyInventoryService with a mocked client and settings."""
    settings = MagicMock()
    settings.shopify_location_map = location_map_setting or {"Dallas Central": "Dallas Central"}
    return ShopifyInventoryService(mock_shopify_client, settings)


# ---------------------------------------------------------------------------
# get_location_map
# ---------------------------------------------------------------------------

class TestGetLocationMap:
    """Tests for location map fetching and caching."""

    @pytest.mark.asyncio
    async def test_fetches_locations_from_shopify(self, mock_shopify_client):
        mock_shopify_client.call_shopify = AsyncMock(return_value={
            "locations": [
                {"name": "Dallas Central", "id": 1001},
                {"name": "Chicago Warehouse", "id": 1002},
            ]
        })
        svc = _make_service(mock_shopify_client)

        result = await svc.get_location_map()
        assert result == {"Dallas Central": 1001, "Chicago Warehouse": 1002}
        mock_shopify_client.call_shopify.assert_called_once_with("GET", "/locations.json")

    @pytest.mark.asyncio
    async def test_caches_location_map(self, mock_shopify_client):
        mock_shopify_client.call_shopify = AsyncMock(return_value={
            "locations": [{"name": "Dallas Central", "id": 1001}]
        })
        svc = _make_service(mock_shopify_client)

        await svc.get_location_map()
        await svc.get_location_map()
        # Should only call Shopify once due to caching
        assert mock_shopify_client.call_shopify.call_count == 1

    @pytest.mark.asyncio
    async def test_empty_locations_response(self, mock_shopify_client):
        mock_shopify_client.call_shopify = AsyncMock(return_value={"locations": []})
        svc = _make_service(mock_shopify_client)

        result = await svc.get_location_map()
        assert result == {}


# ---------------------------------------------------------------------------
# set_inventory_levels
# ---------------------------------------------------------------------------

class TestSetInventoryLevels:
    """Tests for REST-based inventory level setting."""

    @pytest.mark.asyncio
    async def test_sets_inventory_per_location(self, mock_shopify_client):
        mock_shopify_client.call_shopify = AsyncMock(return_value={
            "locations": [{"name": "Dallas Central", "id": 1001}]
        })
        svc = _make_service(mock_shopify_client)

        location_quantities = [{"location": "Dallas Central", "quantity": 50}]
        await svc.set_inventory_levels(77001, location_quantities)

        # call_shopify called twice: GET locations + POST set
        assert mock_shopify_client.call_shopify.call_count == 2
        post_call = mock_shopify_client.call_shopify.call_args_list[1]
        assert post_call[0][0] == "POST"
        assert post_call[0][1] == "/inventory_levels/set.json"
        payload = post_call[1]["json"]
        assert payload["location_id"] == 1001
        assert payload["available"] == 50

    @pytest.mark.asyncio
    async def test_fallback_when_no_location_matches(self, mock_shopify_client):
        mock_shopify_client.call_shopify = AsyncMock(return_value={
            "locations": [{"name": "Dallas Central", "id": 1001}]
        })
        svc = _make_service(mock_shopify_client)

        # Location "Unknown" is not in the location map
        location_quantities = [{"location": "Unknown Place", "quantity": 30}]
        await svc.set_inventory_levels(77001, location_quantities)

        # Should POST with fallback location (first in map)
        assert mock_shopify_client.call_shopify.call_count == 2
        post_call = mock_shopify_client.call_shopify.call_args_list[1]
        payload = post_call[1]["json"]
        assert payload["location_id"] == 1001
        assert payload["available"] == 30

    @pytest.mark.asyncio
    async def test_empty_quantities_returns_early(self, mock_shopify_client):
        svc = _make_service(mock_shopify_client)
        await svc.set_inventory_levels(77001, [])
        # Should not make any API calls
        mock_shopify_client.call_shopify.assert_not_called()


# ---------------------------------------------------------------------------
# set_inventory_cost
# ---------------------------------------------------------------------------

class TestSetInventoryCost:
    """Tests for inventory cost setting."""

    @pytest.mark.asyncio
    async def test_sets_cost(self, mock_shopify_client):
        mock_shopify_client.call_shopify = AsyncMock(return_value={})
        svc = _make_service(mock_shopify_client)

        await svc.set_inventory_cost(77001, 25.50)
        mock_shopify_client.call_shopify.assert_called_once_with(
            "PUT", "/inventory_items/77001.json",
            json={"inventory_item": {"id": 77001, "cost": 25.50}}
        )

    @pytest.mark.asyncio
    async def test_none_cost_skips(self, mock_shopify_client):
        svc = _make_service(mock_shopify_client)
        await svc.set_inventory_cost(77001, None)
        mock_shopify_client.call_shopify.assert_not_called()


# ---------------------------------------------------------------------------
# set_product_category
# ---------------------------------------------------------------------------

class TestSetProductCategory:
    """Tests for product category mutation."""

    @pytest.mark.asyncio
    async def test_calls_graphql_with_gid(self, mock_shopify_client):
        mock_shopify_client.call_shopify_graphql = AsyncMock(return_value={})
        svc = _make_service(mock_shopify_client)

        await svc.set_product_category(99001)
        mock_shopify_client.call_shopify_graphql.assert_called_once()
        call_args = mock_shopify_client.call_shopify_graphql.call_args
        variables = call_args[0][1]
        assert "Product" in str(variables["input"]["id"])

    @pytest.mark.asyncio
    async def test_graphql_exception_is_silenced(self, mock_shopify_client):
        mock_shopify_client.call_shopify_graphql = AsyncMock(
            side_effect=HTTPException(status_code=502, detail="Error")
        )
        svc = _make_service(mock_shopify_client)

        # Should not raise
        await svc.set_product_category(99001)


# ---------------------------------------------------------------------------
# create_metafield_definitions
# ---------------------------------------------------------------------------

class TestCreateMetafieldDefinitions:
    """Tests for metafield definition creation."""

    @pytest.mark.asyncio
    async def test_creates_all_definitions(self, mock_shopify_client):
        mock_shopify_client.call_shopify = AsyncMock(return_value={})
        svc = _make_service(mock_shopify_client)

        await svc.create_metafield_definitions()
        # Should call Shopify once per METAFIELD_DEFINITIONS entry
        from app.core.constants.publishing import METAFIELD_DEFINITIONS
        assert mock_shopify_client.call_shopify.call_count == len(METAFIELD_DEFINITIONS)

    @pytest.mark.asyncio
    async def test_skips_422_errors(self, mock_shopify_client):
        mock_shopify_client.call_shopify = AsyncMock(
            side_effect=HTTPException(status_code=422, detail="Already exists")
        )
        svc = _make_service(mock_shopify_client)

        # Should not raise, just skip
        await svc.create_metafield_definitions()
