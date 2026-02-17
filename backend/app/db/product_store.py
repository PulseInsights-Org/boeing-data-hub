"""
Product store — published product CRUD and pricing updates.

Product store – product table operations.
Version: 1.0.0
"""

import logging
import uuid
from typing import Any, Dict

from fastapi import HTTPException
from postgrest.exceptions import APIError

from app.db.base_store import BaseStore

logger = logging.getLogger("product_store")


class ProductStore(BaseStore):
    """CRUD for the product table."""

    async def upsert_product(
        self, record: Dict[str, Any], shopify_product_id: str | None = None, user_id: str = "system"
    ) -> None:
        shopify_data: Dict[str, Any] = record.get("shopify") or {}

        part_number = (
            record.get("sku")
            or record.get("aviall_part_number")
            or shopify_data.get("sku")
        )
        name = shopify_data.get("title") or record.get("title") or part_number
        description = shopify_data.get("body_html") or record.get("description") or ""

        manufacturer = shopify_data.get("manufacturer") or record.get("manufacturer") or ""
        vendor = shopify_data.get("vendor") or record.get("vendor") or manufacturer

        price = (
            shopify_data.get("price")
            or shopify_data.get("cost_per_item")
            or record.get("price")
            or record.get("cost_per_item")
        )
        cost_per_item = shopify_data.get("cost_per_item") or record.get("cost_per_item")
        list_price = record.get("list_price")
        net_price = record.get("net_price")
        currency = shopify_data.get("currency") or record.get("currency")

        inventory_qty = shopify_data.get("inventory_quantity") or record.get("inventory_quantity")
        inventory_status = record.get("inventory_status")
        location_summary = shopify_data.get("location_summary") or record.get("location_summary")

        weight = shopify_data.get("weight") or record.get("weight")
        weight_unit = shopify_data.get("weight_uom") or record.get("weight_uom")
        country_of_origin = shopify_data.get("country_of_origin") or record.get("country_of_origin")

        dim_length = shopify_data.get("length") or record.get("dim_length")
        dim_width = shopify_data.get("width") or record.get("dim_width")
        dim_height = shopify_data.get("height") or record.get("dim_height")
        dim_uom = shopify_data.get("dim_uom") or record.get("dim_uom")
        base_uom = shopify_data.get("unit_of_measure") or record.get("base_uom")
        hazmat_code = record.get("hazmat_code")
        faa_approval_code = record.get("faa_approval_code")
        eccn = record.get("eccn")
        schedule_b_code = record.get("schedule_b_code")
        supplier_name = record.get("supplier_name")
        boeing_name = record.get("name")
        boeing_description = record.get("description")
        boeing_image_url = record.get("boeing_image_url") or record.get("product_image")
        boeing_thumbnail_url = record.get("boeing_thumbnail_url") or record.get("thumbnail_image")
        image_url = record.get("image_url")
        image_path = record.get("image_path")
        condition = record.get("condition")
        pma = record.get("pma")
        estimated_lead_time_days = record.get("estimated_lead_time_days")
        trace = record.get("trace")
        expiration_date = record.get("expiration_date")
        notes = record.get("notes")

        db_row = {
            "id": str(uuid.uuid4()),
            "sku": part_number,
            "title": name,
            "body_html": description,
            "vendor": vendor,
            "price": price,
            "cost_per_item": cost_per_item,
            "list_price": list_price,
            "net_price": net_price,
            "currency": currency,
            "inventory_quantity": inventory_qty,
            "inventory_status": inventory_status,
            "location_summary": location_summary,
            "weight": weight,
            "weight_unit": weight_unit,
            "country_of_origin": country_of_origin,
            "dim_length": dim_length,
            "dim_width": dim_width,
            "dim_height": dim_height,
            "dim_uom": dim_uom,
            "base_uom": base_uom,
            "hazmat_code": hazmat_code,
            "faa_approval_code": faa_approval_code,
            "eccn": eccn,
            "schedule_b_code": schedule_b_code,
            "supplier_name": supplier_name,
            "boeing_name": boeing_name,
            "boeing_description": boeing_description,
            "boeing_image_url": boeing_image_url,
            "boeing_thumbnail_url": boeing_thumbnail_url,
            "image_url": image_url,
            "image_path": image_path,
            "condition": condition,
            "pma": pma,
            "estimated_lead_time_days": estimated_lead_time_days,
            "trace": trace,
            "expiration_date": expiration_date,
            "notes": notes,
            "shopify_product_id": shopify_product_id,
            "user_id": user_id,
        }

        await self._upsert("product", [db_row], on_conflict="user_id,sku")

    async def upsert_quote_form_data(self, record: Dict[str, Any]) -> None:
        await self._upsert("quotes", [record])

    async def get_product_by_part_number(
        self, part_number: str, user_id: str | None = None
    ) -> Dict[str, Any] | None:
        """Get a product record by part number (checks both id and sku)."""
        try:
            query = self._client.table("product").select("*")
            if user_id:
                query = query.eq("user_id", user_id)

            response = query.eq("sku", part_number).limit(1).execute()
            if response.data:
                return response.data[0]

            query = self._client.table("product").select("*")
            if user_id:
                query = query.eq("user_id", user_id)
            response = query.eq("id", part_number).limit(1).execute()
            return response.data[0] if response.data else None
        except APIError as e:
            logger.info("supabase error table=product detail=%s", str(e))
            raise HTTPException(
                status_code=500,
                detail=f"Supabase select from product failed: {e}",
            )

    async def get_product_by_sku(
        self, sku: str, user_id: str | None = None
    ) -> Dict[str, Any] | None:
        """Alias for get_product_by_part_number."""
        return await self.get_product_by_part_number(sku, user_id)

    async def update_product_pricing(
        self,
        sku: str,
        user_id: str,
        price: float | None = None,
        cost: float | None = None,
        inventory: int | None = None,
    ) -> None:
        """Update product pricing and inventory."""
        payload = {}
        if price is not None:
            payload["price"] = price
        if cost is not None:
            payload["cost_per_item"] = cost
        if inventory is not None:
            payload["inventory_quantity"] = inventory

        if not payload:
            return

        try:
            self._client.table("product") \
                .update(payload) \
                .eq("user_id", user_id) \
                .eq("sku", sku) \
                .execute()

            logger.info(f"Updated product pricing: sku={sku}, user_id={user_id}, changes={payload}")
        except APIError as e:
            logger.error(f"Failed to update product pricing: sku={sku}, error={e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to update product pricing: {e}",
            )
