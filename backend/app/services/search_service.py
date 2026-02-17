"""
Search service — multi-part number search logic.

Search Service — Multi-part SKU search against Shopify.

Handles:
- Sanitizing and deduplicating SKUs
- Building GraphQL queries for Shopify Admin API
- Batch processing with rate limiting
- Aggregating results across batches
Version: 1.0.0
"""
import asyncio
import logging
from typing import List, Optional, Dict, Any, Set

import httpx
from fastapi import HTTPException

from app.core.config import settings

# Shopify credentials from centralized settings
_raw_domain = settings.shopify_store_domain or ""
if _raw_domain and not _raw_domain.endswith(".myshopify.com"):
    SHOPIFY_STORE_DOMAIN = f"{_raw_domain}.myshopify.com"
else:
    SHOPIFY_STORE_DOMAIN = _raw_domain

SHOPIFY_ADMIN_API_TOKEN = settings.shopify_admin_api_token
SHOPIFY_API_VERSION = settings.shopify_api_version

# Constants
BATCH_SIZE = 25
MAX_SKUS_ALLOWED = 50
REQUEST_TIMEOUT = 30
DELAY_BETWEEN_BATCHES = 0.1

logger = logging.getLogger(__name__)


class SearchService:
    """Multi-part SKU search service."""

    @staticmethod
    def sanitize_skus(part_numbers: List[str]) -> tuple[List[str], int]:
        """Clean and deduplicate SKUs."""
        seen: Set[str] = set()
        unique_skus: List[str] = []

        for sku in part_numbers:
            cleaned = sku.strip()
            if not cleaned:
                continue
            cleaned = cleaned.replace('"', '\\"')
            cleaned = ''.join(c for c in cleaned if c.isprintable())
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                unique_skus.append(cleaned)

        duplicates_removed = len(part_numbers) - len(unique_skus)
        return unique_skus, duplicates_removed

    @staticmethod
    def build_graphql_query(skus: List[str]) -> str:
        """Build GraphQL query for searching multiple SKUs."""
        sku_filters = " OR ".join([f'sku:\\"{sku}\\"' for sku in skus])

        query = f'''query {{
  productVariants(first: {len(skus)}, query: "{sku_filters}") {{
    edges {{
      node {{
        id
        sku
        price
        compareAtPrice
        inventoryQuantity
        product {{
          id
          title
          handle
          status
          descriptionHtml
          vendor
          productType
          tags
          images(first: 5) {{
            edges {{
              node {{
                url
                altText
              }}
            }}
          }}
          metafields(first: 20) {{
            edges {{
              node {{
                namespace
                key
                value
              }}
            }}
          }}
        }}
      }}
    }}
  }}
}}'''
        return query

    @staticmethod
    async def call_shopify_api(query: str) -> Dict[str, Any]:
        """Execute GraphQL query against Shopify Admin API."""
        if not SHOPIFY_STORE_DOMAIN or not SHOPIFY_ADMIN_API_TOKEN:
            logger.error("Shopify credentials not configured")
            raise HTTPException(
                status_code=500,
                detail="Shopify credentials not configured. Check SHOPIFY_STORE_DOMAIN and SHOPIFY_ADMIN_API_TOKEN in .env"
            )

        url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"
        headers = {
            "X-Shopify-Access-Token": SHOPIFY_ADMIN_API_TOKEN,
            "Content-Type": "application/json",
        }
        body = {"query": query}

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.post(url, json=body, headers=headers)

                if response.status_code == 401:
                    raise HTTPException(status_code=502, detail="Shopify API authentication failed.")
                if response.status_code == 429:
                    raise HTTPException(status_code=429, detail="Shopify API rate limit exceeded.")
                if response.status_code >= 400:
                    raise HTTPException(status_code=502, detail=f"Shopify API error: {response.text}")

                data = response.json()
                if "errors" in data:
                    raise HTTPException(status_code=502, detail=f"Shopify GraphQL error: {data['errors']}")
                return data

        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Shopify API request timed out.")
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Network error connecting to Shopify: {str(e)}")

    @staticmethod
    def parse_variant_response(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Parse Shopify GraphQL response into product dicts."""
        products: List[Dict[str, Any]] = []
        edges = data.get("data", {}).get("productVariants", {}).get("edges", [])

        for edge in edges:
            node = edge.get("node", {})
            product_data = node.get("product", {})

            variant_gid = node.get("id", "")
            product_gid = product_data.get("id", "")
            variant_id = variant_gid.split("/")[-1] if variant_gid else ""
            product_id = product_gid.split("/")[-1] if product_gid else ""

            images = []
            for img_edge in product_data.get("images", {}).get("edges", []):
                img_node = img_edge.get("node", {})
                if img_node.get("url"):
                    images.append({"url": img_node["url"], "alt_text": img_node.get("altText")})

            metafields = []
            for mf_edge in product_data.get("metafields", {}).get("edges", []):
                mf_node = mf_edge.get("node", {})
                if mf_node.get("key"):
                    metafields.append({
                        "namespace": mf_node.get("namespace", ""),
                        "key": mf_node.get("key", ""),
                        "value": mf_node.get("value", ""),
                    })

            products.append({
                "sku": node.get("sku", ""),
                "shopify_product_id": product_id,
                "shopify_variant_id": variant_id,
                "title": product_data.get("title", ""),
                "handle": product_data.get("handle", ""),
                "status": product_data.get("status", ""),
                "price": node.get("price"),
                "compare_at_price": node.get("compareAtPrice"),
                "inventory_quantity": node.get("inventoryQuantity"),
                "vendor": product_data.get("vendor"),
                "product_type": product_data.get("productType"),
                "description": product_data.get("descriptionHtml"),
                "tags": product_data.get("tags", []),
                "images": images,
                "metafields": metafields,
            })

        return products

    async def search_multiple_skus(self, part_numbers: List[str]) -> Dict[str, Any]:
        """Search for multiple SKUs in Shopify store."""
        unique_skus, duplicates_removed = self.sanitize_skus(part_numbers)

        logger.info(f"Multi-part search started: {len(part_numbers)} requested, {len(unique_skus)} unique")

        if not unique_skus:
            return {
                "success": True,
                "found_products": [],
                "not_found_skus": [],
                "summary": {
                    "total_requested": len(part_numbers),
                    "unique_searched": 0,
                    "found": 0,
                    "not_found": 0,
                    "duplicates_removed": duplicates_removed,
                },
                "message": "No valid SKUs provided after sanitization",
            }

        batches: List[List[str]] = []
        for i in range(0, len(unique_skus), BATCH_SIZE):
            batches.append(unique_skus[i:i + BATCH_SIZE])

        logger.info(f"Processing {len(batches)} batch(es)")

        all_found: List[Dict[str, Any]] = []

        for batch_index, batch in enumerate(batches):
            query = self.build_graphql_query(batch)
            response_data = await self.call_shopify_api(query)
            batch_products = self.parse_variant_response(response_data)
            all_found.extend(batch_products)

            logger.info(f"Batch {batch_index + 1}/{len(batches)}: found {len(batch_products)} products")

            if batch_index < len(batches) - 1:
                await asyncio.sleep(DELAY_BETWEEN_BATCHES)

        found_skus: Set[str] = {p["sku"] for p in all_found}
        not_found_skus: List[str] = [sku for sku in unique_skus if sku not in found_skus]

        summary = {
            "total_requested": len(part_numbers),
            "unique_searched": len(unique_skus),
            "found": len(all_found),
            "not_found": len(not_found_skus),
            "duplicates_removed": duplicates_removed,
        }

        logger.info(f"Multi-part search completed: {summary['found']} found, {summary['not_found']} not found")

        return {
            "success": True,
            "found_products": all_found,
            "not_found_skus": not_found_skus,
            "summary": summary,
        }


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------
_instance: SearchService | None = None


def get_search_service() -> SearchService:
    global _instance
    if _instance is None:
        _instance = SearchService()
    return _instance


def reset_search_service() -> None:
    global _instance
    _instance = None
