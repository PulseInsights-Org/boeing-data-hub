import json
import logging
from typing import Any, Dict

from fastapi import HTTPException

from app.clients.shopify_client import ShopifyClient
from app.db.supabase_store import SupabaseStore


class ShopifyService:
    def __init__(self, client: ShopifyClient, store: SupabaseStore) -> None:
        self._client = client
        self._store = store
        self._logger = logging.getLogger("shopify_service")

    async def publish_product_by_part_number(self, part_number: str, user_id: str = "system") -> Dict[str, Any]:
        """
        Publish or update a product in Shopify.

        Implements idempotency:
        1. Check if product already has shopify_product_id in staging → UPDATE Shopify
        2. Check if SKU exists in Shopify → UPDATE Shopify and save ID
        3. Otherwise → CREATE in Shopify

        Args:
            part_number: The part number/SKU to publish
            user_id: User ID for user-specific data

        Returns:
            dict with success status and shopify_product_id
        """
        # Get product from staging, filtered by user_id for user-specific data
        record = await self._store.get_product_staging_by_part_number(part_number, user_id=user_id)
        if not record:
            raise HTTPException(status_code=404, detail=f"Product staging not found for part number {part_number}")

        self._logger.info("shopify publish staging_record=%s", json.dumps(record, ensure_ascii=True))

        # Check if this product was already published (has shopify_product_id)
        existing_shopify_id = record.get("shopify_product_id")
        if existing_shopify_id:
            self._logger.info("shopify product already published, will update shopify_id=%s", existing_shopify_id)

        # Try to upload Boeing image to Supabase, fall back to placeholder if it fails
        boeing_image_url = record.get("boeing_image_url") or record.get("boeing_thumbnail_url")
        fallback_image_url = "https://placehold.co/800x600/e8e8e8/666666/png?text=Image+Not+Available&font=roboto"

        if boeing_image_url:
            try:
                image_url, image_path = await self._store.upload_image_from_url(
                    boeing_image_url,
                    part_number,
                )
                record["image_url"] = image_url
                record["image_path"] = image_path
                await self._store.update_product_staging_image(part_number, image_url, image_path)
                self._logger.info("shopify publish image uploaded to supabase url=%s", image_url)
            except Exception as exc:
                self._logger.info("shopify publish image upload failed, using placeholder error=%s", str(exc))
                record["image_url"] = fallback_image_url
        else:
            record["image_url"] = fallback_image_url
            self._logger.info("shopify publish no boeing image, using placeholder")

        record.setdefault("shopify", {})
        # derive pricing according to business rules
        list_price = record.get("list_price")
        net_price = record.get("net_price")
        base_cost = list_price if list_price is not None else net_price
        shop_price = (base_cost * 1.1) if base_cost is not None else record.get("price")

        record["shopify"].update(
            {
                "title": record.get("title"),
                "sku": record.get("sku"),
                "description": record.get("boeing_name") or record.get("title"),
                "body_html": record.get("body_html") or "",
                "vendor": record.get("vendor"),
                "manufacturer": record.get("supplier_name") or record.get("vendor"),
                "price": shop_price,
                "cost_per_item": base_cost,
                "currency": record.get("currency"),
                "unit_of_measure": record.get("base_uom"),
                "country_of_origin": record.get("country_of_origin"),
                "length": record.get("dim_length"),
                "width": record.get("dim_width"),
                "height": record.get("dim_height"),
                "dim_uom": record.get("dim_uom"),
                "weight": record.get("weight"),
                "weight_uom": record.get("weight_unit"),
                "inventory_quantity": record.get("inventory_quantity"),
                "location_summary": record.get("location_summary"),
                "product_image": record.get("image_url") or record.get("boeing_image_url"),
                "thumbnail_image": record.get("boeing_thumbnail_url"),
                "cert": "FAA 8130-3",
                "condition": record.get("condition") or "NE",
                "pma": record.get("pma"),
                "estimated_lead_time_days": record.get("estimated_lead_time_days"),
                "trace": record.get("trace"),
                "expiration_date": record.get("expiration_date"),
                "notes": record.get("notes"),
            }
        )

        # Copy additional fields needed for metafields to the root record
        record["name"] = record.get("boeing_name") or record.get("title")
        record["description"] = record.get("boeing_description") or ""
        record["hazmat_code"] = record.get("hazmat_code")
        record["faa_approval_code"] = record.get("faa_approval_code")
        record["eccn"] = record.get("eccn")
        record["schedule_b_code"] = record.get("schedule_b_code")
        record["supplier_name"] = record.get("supplier_name")
        record["base_uom"] = record.get("base_uom")

        # Determine if we should UPDATE or CREATE in Shopify
        shopify_product_id = existing_shopify_id

        if existing_shopify_id:
            # Product was already published - UPDATE Shopify
            self._logger.info("shopify updating existing product shopify_id=%s", existing_shopify_id)
            data = await self._client.update_product(existing_shopify_id, record)
            shopify_product = data.get("product") or {}
            shopify_product_id = shopify_product.get("id") or existing_shopify_id
        else:
            # Check if SKU already exists in Shopify (might have been published outside our system)
            sku = record.get("sku") or part_number
            found_shopify_id = await self._client.find_product_by_sku(sku)

            if found_shopify_id:
                # SKU exists in Shopify - UPDATE instead of CREATE
                self._logger.info("shopify found existing product by sku=%s shopify_id=%s, updating", sku, found_shopify_id)
                data = await self._client.update_product(found_shopify_id, record)
                shopify_product = data.get("product") or {}
                shopify_product_id = shopify_product.get("id") or found_shopify_id
            else:
                # No existing product - CREATE new
                self._logger.info("shopify creating new product sku=%s", sku)
                data = await self._client.publish_product(record)
                shopify_product = data.get("product") or {}
                shopify_product_id = shopify_product.get("id")

        # Save to product table and update staging with shopify_product_id
        shopify_id_str = str(shopify_product_id) if shopify_product_id is not None else None

        await self._store.upsert_product(
            record,
            shopify_product_id=shopify_id_str,
            user_id=user_id,
        )

        # Also update the staging record with the shopify_product_id
        if shopify_id_str:
            await self._store.update_product_staging_shopify_id(
                part_number, shopify_id_str, user_id=user_id
            )

        return {
            "success": True,
            "shopifyProductId": shopify_id_str,
        }

    async def update_product(self, shopify_product_id: str, product: Dict[str, Any]) -> Dict[str, Any]:
        self._logger.info("shopify update input id=%s payload=%s", shopify_product_id, json.dumps(product, ensure_ascii=True))
        data = await self._client.update_product(shopify_product_id, product)
        shopify_product = data.get("product") or {}
        return {
            "success": True,
            "shopifyProductId": str(shopify_product.get("id")) if shopify_product.get("id") is not None else None,
        }

    async def find_product_by_sku(self, sku: str) -> str | None:
        return await self._client.find_product_by_sku(sku)

    async def setup_metafield_definitions(self) -> None:
        await self._client.create_metafield_definitions()
