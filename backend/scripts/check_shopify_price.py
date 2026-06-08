"""
Diagnostic: look up the current Shopify product/variant for a SKU and print
its price, inventory, and (optionally) per-location inventory. Read-only.

Credentials are loaded from .env via Settings (SHOPIFY_STORE_DOMAIN,
SHOPIFY_ADMIN_API_TOKEN, SHOPIFY_API_VERSION). They are NEVER printed.

Usage:
  cd backend/
  python -m scripts.check_shopify_price              # defaults to 2D2019-5=E9
  python -m scripts.check_shopify_price 2D2019-5=E9
  python -m scripts.check_shopify_price P621888=K3
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

from app.core.config import Settings  # noqa: E402
from scripts.manual_sync import ShopifyAPI, strip_variant_suffix  # noqa: E402

DEFAULT_SKU = "2D2019-5=E9"


async def fetch_inventory_levels(
    api: ShopifyAPI, inventory_item_id: int
) -> List[Dict[str, Any]]:
    """Return [{location_name, available}] for the given inventory item."""
    loc_data = await api._call("GET", "/locations.json")
    locations = {loc["id"]: loc.get("name") for loc in (loc_data.get("locations") or [])}

    lvl = await api._call(
        "GET",
        "/inventory_levels.json",
        params={"inventory_item_ids": str(inventory_item_id)},
    )
    levels: List[Dict[str, Any]] = []
    for entry in lvl.get("inventory_levels", []) or []:
        levels.append(
            {
                "location_name": locations.get(entry.get("location_id"), "?"),
                "location_id": entry.get("location_id"),
                "available": entry.get("available"),
            }
        )
    return levels


async def lookup_sku(api: ShopifyAPI, raw_sku: str) -> Dict[str, Any]:
    base_sku = strip_variant_suffix(raw_sku)

    product_id = await api.find_product_by_sku(base_sku)
    if not product_id:
        return {
            "raw_sku": raw_sku,
            "base_sku": base_sku,
            "found": False,
            "message": "No Shopify product found for SKU",
        }

    data = await api._call("GET", f"/products/{product_id}.json")
    product = data.get("product") or {}
    variants = product.get("variants") or []
    variant = next(
        (v for v in variants if v.get("sku") == base_sku), variants[0] if variants else None
    )
    if not variant:
        return {
            "raw_sku": raw_sku,
            "base_sku": base_sku,
            "found": False,
            "shopify_product_id": product_id,
            "message": "Product has no variants",
        }

    inventory_item_id = variant.get("inventory_item_id")
    inventory_levels: List[Dict[str, Any]] = []
    if inventory_item_id:
        try:
            inventory_levels = await fetch_inventory_levels(api, int(inventory_item_id))
        except Exception as exc:
            inventory_levels = [{"error": str(exc)[:200]}]

    return {
        "raw_sku": raw_sku,
        "base_sku": base_sku,
        "found": True,
        "shopify_product_id": product_id,
        "product_title": product.get("title"),
        "product_status": product.get("status"),
        "product_updated_at": product.get("updated_at"),
        "variant": {
            "id": variant.get("id"),
            "sku": variant.get("sku"),
            "price": variant.get("price"),
            "compare_at_price": variant.get("compare_at_price"),
            "inventory_quantity": variant.get("inventory_quantity"),
            "inventory_item_id": inventory_item_id,
            "updated_at": variant.get("updated_at"),
        },
        "inventory_by_location": inventory_levels,
    }


async def main() -> int:
    skus = sys.argv[1:] or [DEFAULT_SKU]

    settings = Settings()
    if not settings.shopify_store_domain or not settings.shopify_admin_api_token:
        print("ERROR: SHOPIFY_STORE_DOMAIN / SHOPIFY_ADMIN_API_TOKEN not set", file=sys.stderr)
        return 1

    api = ShopifyAPI(settings)

    print("=" * 70)
    print(f"  SHOPIFY PRICE/INVENTORY LOOKUP  ({len(skus)} SKU{'s' if len(skus) != 1 else ''})")
    print("=" * 70)
    print()

    exit_code = 0
    for raw_sku in skus:
        try:
            result = await lookup_sku(api, raw_sku)
        except Exception as exc:
            result = {"raw_sku": raw_sku, "found": False, "error": str(exc)[:300]}
            exit_code = 2

        print(json.dumps(result, indent=2, default=str))
        print()

    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
