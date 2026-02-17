"""
Shopify HTTP client — REST and GraphQL transport layer.

Handles authentication, domain normalization, and raw HTTP calls
to the Shopify Admin API. No business logic — all orchestration
lives in services/utils/shopify_orchestrator.py.
Version: 1.0.0
"""
import logging
import httpx
from typing import Any, Dict, Optional
from fastapi import HTTPException
from app.core.config import Settings

logger = logging.getLogger("shopify_client")


class ShopifyClient:
    """Thin HTTP transport for Shopify Admin API."""

    def __init__(self, settings: Settings) -> None:
        raw_domain = settings.shopify_store_domain
        self._store_domain = self._normalize_store_domain(raw_domain)
        self._token = settings.shopify_admin_api_token
        self._api_version = settings.shopify_api_version

    @staticmethod
    def _normalize_store_domain(domain: Optional[str]) -> Optional[str]:
        """Ensure domain ends with .myshopify.com."""
        if not domain:
            return domain
        domain = domain.replace("https://", "").replace("http://", "").rstrip("/")
        if not domain.endswith(".myshopify.com"):
            domain = f"{domain}.myshopify.com"
        return domain

    def _base_url(self) -> str:
        if not self._store_domain or not self._token:
            raise HTTPException(status_code=500, detail="Shopify env vars missing")
        return f"https://{self._store_domain}/admin/api/{self._api_version}"

    def to_gid(self, entity: str, value: str | int) -> str:
        """Convert a numeric ID to Shopify Global ID format."""
        if isinstance(value, str) and value.startswith("gid://"):
            return value
        return f"gid://shopify/{entity}/{value}"

    async def call_shopify(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute a REST API call to Shopify."""
        url = f"{self._base_url()}{path}"
        headers = {
            "X-Shopify-Access-Token": self._token or "",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method=method, url=url, headers=headers, json=json, params=params
            )
        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json() if resp.text else {}

    async def call_shopify_graphql(
        self, query: str, variables: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Execute a GraphQL query against the Shopify Admin API."""
        data = await self.call_shopify(
            "POST", "/graphql.json", json={"query": query, "variables": variables or {}}
        )
        if data.get("errors"):
            raise HTTPException(status_code=502, detail=str(data.get("errors")))
        return data

    async def disconnect_inventory_level(
        self, inventory_item_id: int, location_id: int
    ) -> None:
        """Disconnect an inventory item from a location (removes the 0-qty entry)."""
        await self.call_shopify(
            "DELETE",
            "/inventory_levels.json",
            params={"inventory_item_id": inventory_item_id, "location_id": location_id},
        )

    async def delete_product(self, product_id: int | str) -> bool:
        """Delete a product by ID (single REST call, no orchestration)."""
        await self.call_shopify("DELETE", f"/products/{product_id}.json")
        return True
