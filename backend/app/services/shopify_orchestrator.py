"""
Shopify orchestrator — product-level CRUD operations for Shopify.

Coordinates HTTP calls (via ShopifyClient) with inventory management
(via ShopifyInventoryService) for publish, update, find, and pricing flows.
Version: 1.0.0
"""
import logging
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app.clients.shopify_client import ShopifyClient
from app.services.shopify_inventory_service import ShopifyInventoryService
from app.utils.shopify_payload_builder import build_product_payload

logger = logging.getLogger("shopify_orchestrator")


class ShopifyOrchestrator:
    """High-level Shopify product operations."""

    def __init__(
        self,
        client: ShopifyClient,
        inventory: ShopifyInventoryService,
    ) -> None:
        self._client = client
        self._inventory = inventory

    async def publish_product(self, product: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new product in Shopify, set category, inventory, and cost."""
        body = build_product_payload(product)
        data = await self._client.call_shopify("POST", "/products.json", json=body)
        shopify_product = data.get("product") or {}
        product_id = shopify_product.get("id")
        if product_id:
            await self._inventory.set_product_category(product_id)

        variants = shopify_product.get("variants") or []
        location_quantities = (product.get("shopify") or {}).get("location_quantities") or []
        if variants and location_quantities:
            inventory_item_id = variants[0].get("inventory_item_id")
            if inventory_item_id is not None:
                await self._inventory.set_inventory_levels(int(inventory_item_id), location_quantities)

        cost_per_item = (product.get("shopify") or {}).get("cost_per_item")
        if variants and cost_per_item is not None:
            inventory_item_id = variants[0].get("inventory_item_id")
            if inventory_item_id is not None:
                await self._inventory.set_inventory_cost(int(inventory_item_id), cost_per_item)

        return {"product": {"id": product_id, "handle": shopify_product.get("handle")}}

    async def update_product(
        self, shopify_product_id: str, product: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update an existing Shopify product, set category, inventory, and cost."""
        body = build_product_payload(product)
        body["product"]["id"] = int(shopify_product_id)
        data = await self._client.call_shopify(
            "PUT", f"/products/{shopify_product_id}.json", json=body
        )
        shopify_product = data.get("product") or {}
        product_id = shopify_product.get("id")
        if product_id:
            await self._inventory.set_product_category(product_id)

        variants = shopify_product.get("variants") or []
        location_quantities = (product.get("shopify") or {}).get("location_quantities") or []
        if variants and location_quantities:
            inventory_item_id = variants[0].get("inventory_item_id")
            if inventory_item_id is not None:
                await self._inventory.set_inventory_levels(int(inventory_item_id), location_quantities)
        if variants:
            inventory_item_id = variants[0].get("inventory_item_id")
            if inventory_item_id is not None:
                await self._inventory.set_inventory_cost(
                    int(inventory_item_id),
                    (product.get("shopify") or {}).get("cost_per_item"),
                )
        return data

    async def find_product_by_sku(self, sku: str) -> Optional[str]:
        """Find a product ID by SKU (REST search, first 50 products)."""
        params = {"limit": 50, "fields": "id,variants"}
        data = await self._client.call_shopify("GET", "/products.json", params=params)
        products = data.get("products") or []
        for p in products:
            for v in p.get("variants", []):
                if v.get("sku") == sku:
                    return str(p.get("id"))
        return None

    async def get_variant_by_sku(self, sku: str) -> Optional[Dict[str, Any]]:
        """Fetch variant data by SKU via GraphQL."""
        query = (
            "query GetSkuData($skuQuery: String!) { "
            "productVariants(first: 5, query: $skuQuery) { "
            "edges { node { id sku title price compareAtPrice inventoryQuantity } } } }"
        )
        body = {"query": query, "variables": {"skuQuery": sku}}
        data = await self._client.call_shopify("POST", "/graphql.json", json=body)
        if not data:
            return None
        if data.get("errors"):
            raise HTTPException(status_code=502, detail=str(data.get("errors")))
        edges = (data.get("data") or {}).get("productVariants", {}).get("edges", [])
        if not edges:
            return None
        for edge in edges:
            node = edge.get("node") or {}
            if node.get("sku") == sku:
                return node
        return (edges[0].get("node") or {}) if edges else None

    async def update_product_pricing(
        self,
        shopify_product_id: str | int,
        price: float | None = None,
        quantity: int | None = None,
        metafields: list[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        """Update product pricing, optional inventory, and metafields."""
        data = await self._client.call_shopify("GET", f"/products/{shopify_product_id}.json")
        variants = (data.get("product") or {}).get("variants") or []
        if not variants:
            raise HTTPException(status_code=404, detail="Product has no variants")
        variant = variants[0]
        variant_update: Dict[str, Any] = {"id": variant.get("id")}
        if price is not None:
            variant_update["price"] = str(price)
        payload: Dict[str, Any] = {
            "product": {"id": int(shopify_product_id), "variants": [variant_update]}
        }
        if metafields:
            payload["product"]["metafields"] = metafields
        result = await self._client.call_shopify(
            "PUT", f"/products/{shopify_product_id}.json", json=payload
        )
        if quantity is not None and variant.get("inventory_item_id"):
            await self.update_inventory(
                shopify_product_id, quantity, variant["inventory_item_id"]
            )
        return result

    async def update_inventory(
        self,
        shopify_product_id: str | int,
        quantity: int,
        inventory_item_id: int | None = None,
    ) -> None:
        """Update total inventory for a product (single location fallback)."""
        if inventory_item_id is None:
            data = await self._client.call_shopify("GET", f"/products/{shopify_product_id}.json")
            variants = (data.get("product") or {}).get("variants") or []
            if not variants:
                return
            inventory_item_id = variants[0].get("inventory_item_id")
        if not inventory_item_id:
            return
        location_map = await self._inventory.get_location_map()
        if not location_map:
            return
        location_id = next(iter(location_map.values()))
        await self._client.call_shopify("POST", "/inventory_levels/set.json", json={
            "location_id": location_id,
            "inventory_item_id": int(inventory_item_id),
            "available": quantity,
        })

    async def update_inventory_by_location(
        self, shopify_product_id: str | int, location_quantities: list[dict]
    ) -> None:
        """Update per-location inventory for a product."""
        if not location_quantities:
            return
        data = await self._client.call_shopify("GET", f"/products/{shopify_product_id}.json")
        variants = (data.get("product") or {}).get("variants") or []
        if not variants:
            return
        inventory_item_id = variants[0].get("inventory_item_id")
        if not inventory_item_id:
            return
        await self._inventory.set_inventory_levels(int(inventory_item_id), location_quantities)

    async def delete_product(self, product_id: int | str) -> bool:
        """Delete a product from Shopify."""
        return await self._client.delete_product(product_id)

    async def create_metafield_definitions(self) -> None:
        """Create custom metafield definitions in Shopify."""
        await self._inventory.create_metafield_definitions()
