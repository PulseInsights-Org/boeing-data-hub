"""
Shopify inventory service — location mapping, inventory levels, and cost management.

Extracted from shopify_client.py to separate inventory/location concerns
from HTTP transport. All methods are async and delegate HTTP calls to ShopifyClient.
Version: 1.0.0
"""
import logging
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app.clients.shopify_client import ShopifyClient
from app.core.config import Settings
from app.core.constants.publishing import METAFIELD_DEFINITIONS, PRODUCT_CATEGORY_GID

logger = logging.getLogger("shopify_inventory")


class ShopifyInventoryService:
    """Low-level Shopify inventory and location operations."""

    def __init__(self, client: ShopifyClient, settings: Settings) -> None:
        self._client = client
        self._location_map: Dict[str, int] = {}
        self._location_name_map = settings.shopify_location_map or {}
        self._default_location_name = settings.shopify_default_location_name

    async def get_location_map(self) -> Dict[str, int]:
        """Fetch and cache Shopify location name -> ID mapping."""
        if self._location_map:
            return self._location_map
        data = await self._client.call_shopify("GET", "/locations.json")
        locations = data.get("locations") or []
        self._location_map = {
            loc.get("name"): int(loc.get("id"))
            for loc in locations
            if loc.get("name") and loc.get("id") is not None
        }
        logger.info("shopify locations loaded=%s", list(self._location_map.keys()))
        return self._location_map

    async def set_inventory_levels(
        self, inventory_item_id: int, location_quantities: list[dict]
    ) -> None:
        """Set inventory levels per location via REST API."""
        if not location_quantities:
            return
        try:
            location_map = await self.get_location_map()
        except HTTPException:
            return
        matched, total_qty = 0, 0
        for loc in location_quantities:
            loc_name, qty = loc.get("location"), loc.get("quantity")
            if loc_name is None or qty is None:
                continue
            total_qty += int(qty)
            mapped_name = self._location_name_map.get(loc_name, loc_name)
            location_id = location_map.get(mapped_name)
            if location_id is None:
                continue
            await self._client.call_shopify("POST", "/inventory_levels/set.json", json={
                "location_id": location_id,
                "inventory_item_id": inventory_item_id,
                "available": int(qty),
            })
            matched += 1
        if matched == 0 and location_map:
            fallback_id = next(iter(location_map.values()))
            await self._client.call_shopify("POST", "/inventory_levels/set.json", json={
                "location_id": fallback_id,
                "inventory_item_id": inventory_item_id,
                "available": int(total_qty),
            })

        # Disconnect default location if it's not one of the mapped locations
        if matched > 0 and self._default_location_name:
            default_loc_id = location_map.get(self._default_location_name)
            if default_loc_id:
                mapped_loc_ids = set()
                for loc in location_quantities:
                    loc_name = loc.get("location")
                    if loc_name is None:
                        continue
                    mapped_name = self._location_name_map.get(loc_name, loc_name)
                    lid = location_map.get(mapped_name)
                    if lid:
                        mapped_loc_ids.add(lid)
                if default_loc_id not in mapped_loc_ids:
                    try:
                        await self._client.disconnect_inventory_level(inventory_item_id, default_loc_id)
                        logger.info(
                            "Disconnected default location '%s' from inventory item %s",
                            self._default_location_name, inventory_item_id,
                        )
                    except Exception as e:
                        logger.warning("Failed to disconnect default location: %s", e)

    async def set_inventory_levels_graphql(
        self, inventory_item_id: str | int, location_quantities: list[dict]
    ) -> None:
        """Set inventory levels per location via GraphQL (preferred for bulk)."""
        if not location_quantities:
            return
        try:
            location_map = await self.get_location_map()
        except HTTPException:
            return
        matched, total_qty, set_quantities = 0, 0, []
        for loc in location_quantities:
            loc_name, qty = loc.get("location"), loc.get("quantity")
            if loc_name is None or qty is None:
                continue
            total_qty += int(qty)
            mapped_name = self._location_name_map.get(loc_name, loc_name)
            location_id = location_map.get(mapped_name)
            if location_id is None:
                continue
            set_quantities.append({
                "inventoryItemId": self._client.to_gid("InventoryItem", inventory_item_id),
                "locationId": self._client.to_gid("Location", location_id),
                "quantity": int(qty),
            })
            matched += 1
        if matched == 0 and location_map:
            fallback_id = next(iter(location_map.values()))
            set_quantities.append({
                "inventoryItemId": self._client.to_gid("InventoryItem", inventory_item_id),
                "locationId": self._client.to_gid("Location", fallback_id),
                "quantity": int(total_qty),
            })
        if not set_quantities:
            return
        mutation = (
            "mutation InventorySetOnHand($input: InventorySetOnHandQuantitiesInput!) { "
            "inventorySetOnHandQuantities(input: $input) { userErrors { field message } } }"
        )
        data = await self._client.call_shopify_graphql(
            mutation, {"input": {"reason": "correction", "setQuantities": set_quantities}}
        )
        errors = (data.get("data") or {}).get("inventorySetOnHandQuantities", {}).get("userErrors") or []
        if errors:
            raise HTTPException(status_code=502, detail=str(errors))

    async def set_inventory_cost(
        self, inventory_item_id: int, cost_per_item: float | None
    ) -> None:
        """Set cost-per-item on an inventory item."""
        if cost_per_item is None:
            return
        await self._client.call_shopify("PUT", f"/inventory_items/{inventory_item_id}.json", json={
            "inventory_item": {"id": inventory_item_id, "cost": float(cost_per_item)}
        })

    async def set_product_category(self, product_id: int) -> None:
        """Set product category via GraphQL mutation."""
        mutation = """mutation productUpdate($input: ProductInput!) {
            productUpdate(input: $input) {
                product { id } userErrors { field message }
            }
        }"""
        variables = {
            "input": {
                "id": self._client.to_gid("Product", product_id),
                "category": PRODUCT_CATEGORY_GID,
            }
        }
        try:
            await self._client.call_shopify_graphql(mutation, variables)
        except HTTPException:
            pass

    async def create_metafield_definitions(self) -> None:
        """Create all custom metafield definitions in Shopify."""
        for definition in METAFIELD_DEFINITIONS:
            payload = {"metafield_definition": {**definition, "owner_type": "product"}}
            try:
                await self._client.call_shopify("POST", "/metafield_definitions.json", json=payload)
            except HTTPException as exc:
                if exc.status_code in (406, 422):
                    logger.info(
                        "shopify metafield definition skipped status=%s key=%s detail=%s",
                        exc.status_code, definition["key"], exc.detail,
                    )
                    continue
                raise
