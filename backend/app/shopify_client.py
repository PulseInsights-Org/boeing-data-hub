import os
from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException
from dotenv import load_dotenv

load_dotenv()

from .supabase_client import upsert_product

SHOPIFY_STORE_DOMAIN = os.getenv("SHOPIFY_STORE_DOMAIN")
SHOPIFY_ADMIN_API_TOKEN = os.getenv("SHOPIFY_ADMIN_API_TOKEN")
SHOPIFY_API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2024-01")


def _base_url() -> str:
    if not SHOPIFY_STORE_DOMAIN or not SHOPIFY_ADMIN_API_TOKEN:
        raise HTTPException(status_code=500, detail="Shopify env vars missing")
    return f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/{SHOPIFY_API_VERSION}"


async def _call_shopify(method: str, path: str, json: Optional[Dict[str, Any]] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base = _base_url()
    url = f"{base}{path}"

    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ADMIN_API_TOKEN or "",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(method=method, url=url, headers=headers, json=json, params=params)

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    if resp.text:
        return resp.json()
    return {}


def _to_shopify_product_body(product: Dict[str, Any]) -> Dict[str, Any]:
    """Transform NormalizedProduct-like dict into Shopify product payload."""
    title = product.get("title") or product.get("name") or product.get("partNumber")
    description = product.get("description") or ""
    manufacturer = product.get("manufacturer") or ""
    distr_src = product.get("distrSrc") or ""

    price = product.get("price") or 0
    inventory = product.get("inventory") or 0
    weight = product.get("weight") or 0
    weight_unit = product.get("weightUnit") or "lb"

    part_number = product.get("partNumber") or ""
    dim = {
        "length": product.get("length"),
        "width": product.get("width"),
        "height": product.get("height"),
        "unit": product.get("dimensionUom"),
    }

    payload = {
        "product": {
            "title": title,
            "body_html": f"<p>{description}</p>",
            "vendor": manufacturer,
            "product_type": "Aerospace Component",
            "tags": [
                "boeing",
                "aerospace",
                distr_src.lower().replace(" ", "-") if distr_src else "",
            ],
            "variants": [
                {
                    "sku": part_number,
                    "price": str(price),
                    "inventory_management": "shopify",
                    "inventory_quantity": int(inventory),
                    "weight": float(weight),
                    "weight_unit": "kg" if weight_unit == "kg" else "lb",
                }
            ],
            "metafields": [
                {
                    "namespace": "boeing",
                    "key": "part_number",
                    "value": part_number,
                    "type": "single_line_text_field",
                },
                {
                    "namespace": "boeing",
                    "key": "dimensions",
                    "value": str(dim),
                    "type": "single_line_text_field",
                },
                {
                    "namespace": "boeing",
                    "key": "distribution_source",
                    "value": distr_src,
                    "type": "single_line_text_field",
                },
            ],
        }
    }
    return payload


async def publish_product(product: Dict[str, Any]) -> Dict[str, Any]:
    body = _to_shopify_product_body(product)
    data = await _call_shopify("POST", "/products.json", json=body)
    shopify_product = data.get("product") or {}
    shopify_product_id = shopify_product.get("id")

    # Persist into Supabase `product` table as the single source of truth
    await upsert_product(product, shopify_product_id=str(shopify_product_id) if shopify_product_id is not None else None)

    return {
        "success": True,
        "shopifyProductId": str(shopify_product_id) if shopify_product_id is not None else None,
    }


async def update_product(shopify_product_id: str, product: Dict[str, Any]) -> Dict[str, Any]:
    body = _to_shopify_product_body(product)
    # Shopify expects id inside product for update
    body["product"]["id"] = int(shopify_product_id)
    data = await _call_shopify("PUT", f"/products/{shopify_product_id}.json", json=body)
    shopify_product = data.get("product") or {}
    return {
        "success": True,
        "shopifyProductId": str(shopify_product.get("id")) if shopify_product.get("id") is not None else None,
    }


async def find_product_by_sku(sku: str) -> Optional[str]:
    """Very simple SKU check: search products and inspect variants.

    NOTE: For production, you might want to maintain a mapping table
    instead of scanning.
    """
    params = {
        "limit": 50,
        "fields": "id,variants",
    }
    data = await _call_shopify("GET", "/products.json", params=params)
    products = data.get("products") or []
    for p in products:
        for v in p.get("variants", []):
            if v.get("sku") == sku:
                return str(p.get("id"))
    return None


async def get_variant_by_sku(sku: str) -> Optional[Dict[str, Any]]:
    """Fetch a Shopify variant by SKU using the Admin GraphQL API."""
    query = (
        "query GetSkuData($skuQuery: String!) { "
        "productVariants(first: 5, query: $skuQuery) { "
        "edges { node { id sku title price compareAtPrice inventoryQuantity } } } }"
    )
    body = {
        "query": query,
        "variables": {"skuQuery": sku},
    }
    data = await _call_shopify("POST", "/graphql.json", json=body)
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
