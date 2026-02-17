"""
Staging store — normalized product staging CRUD.

Staging store – product_staging table operations.
Version: 1.0.0
"""

import logging
import uuid
from typing import Any, Dict, List

from fastapi import HTTPException
from postgrest.exceptions import APIError

from app.db.base_store import BaseStore

logger = logging.getLogger("staging_store")


class StagingStore(BaseStore):
    """CRUD for the product_staging table."""

    async def upsert_product_staging(
        self, records: List[Dict[str, Any]], user_id: str = "system", batch_id: str | None = None
    ) -> None:
        if not records:
            return

        db_rows: List[Dict[str, Any]] = []

        for rec in records:
            shopify_data: Dict[str, Any] = rec.get("shopify") or {}

            part_number = (
                rec.get("sku") or rec.get("aviall_part_number") or shopify_data.get("sku")
            )
            name = shopify_data.get("title") or rec.get("title") or part_number
            description = shopify_data.get("body_html") or rec.get("description") or ""

            manufacturer = shopify_data.get("manufacturer") or rec.get("manufacturer") or ""
            vendor = shopify_data.get("vendor") or rec.get("vendor") or manufacturer

            price = (
                shopify_data.get("price")
                or shopify_data.get("cost_per_item")
                or rec.get("price")
                or rec.get("cost_per_item")
            )
            cost_per_item = shopify_data.get("cost_per_item") or rec.get("cost_per_item")
            list_price = rec.get("list_price")
            net_price = rec.get("net_price")
            currency = shopify_data.get("currency") or rec.get("currency")

            inventory_qty = shopify_data.get("inventory_quantity") or rec.get("inventory_quantity")
            inventory_status = rec.get("inventory_status")
            location_summary = shopify_data.get("location_summary") or rec.get("location_summary")

            weight = shopify_data.get("weight") or rec.get("weight")
            weight_unit = shopify_data.get("weight_uom") or rec.get("weight_uom")
            country_of_origin = shopify_data.get("country_of_origin") or rec.get("country_of_origin")

            dim_length = shopify_data.get("length") or rec.get("dim_length")
            dim_width = shopify_data.get("width") or rec.get("dim_width")
            dim_height = shopify_data.get("height") or rec.get("dim_height")
            dim_uom = shopify_data.get("dim_uom") or rec.get("dim_uom")
            base_uom = shopify_data.get("unit_of_measure") or rec.get("base_uom")
            hazmat_code = rec.get("hazmat_code")
            faa_approval_code = rec.get("faa_approval_code")
            eccn = rec.get("eccn")
            schedule_b_code = rec.get("schedule_b_code")
            supplier_name = rec.get("supplier_name")
            boeing_name = rec.get("name")
            boeing_description = rec.get("description")
            boeing_image_url = rec.get("boeing_image_url") or rec.get("product_image")
            boeing_thumbnail_url = rec.get("boeing_thumbnail_url") or rec.get("thumbnail_image")
            image_url = rec.get("image_url")
            image_path = rec.get("image_path")
            condition = rec.get("condition")
            pma = rec.get("pma")
            estimated_lead_time_days = rec.get("estimated_lead_time_days")
            trace = rec.get("trace")
            expiration_date = rec.get("expiration_date")
            notes = rec.get("notes")

            status = rec.get("status") or "fetched"

            row_data = {
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
                "status": status,
                "user_id": user_id,
            }
            if batch_id:
                row_data["batch_id"] = batch_id
            db_rows.append(row_data)

        await self._upsert("product_staging", db_rows, on_conflict="user_id,sku")

    async def get_product_staging_by_part_number(
        self, part_number: str, user_id: str | None = None
    ) -> Dict[str, Any] | None:
        """Get a product staging record by part number (checks both id and sku)."""
        try:
            query = self._client.table("product_staging").select("*")
            if user_id:
                query = query.eq("user_id", user_id)

            response = query.eq("sku", part_number).limit(1).execute()
            if response.data:
                return response.data[0]

            query = self._client.table("product_staging").select("*")
            if user_id:
                query = query.eq("user_id", user_id)
            response = query.eq("id", part_number).limit(1).execute()
            return response.data[0] if response.data else None
        except APIError as e:
            logger.info("supabase error table=product_staging detail=%s", str(e))
            raise HTTPException(
                status_code=500,
                detail=f"Supabase select from product_staging failed: {e}",
            )

    async def update_product_staging_shopify_id(
        self, part_number: str, shopify_product_id: str, user_id: str | None = None
    ) -> None:
        """Update the shopify_product_id and status for a product staging record."""
        payload = {
            "shopify_product_id": shopify_product_id,
            "status": "published",
        }
        try:
            if user_id:
                response = (
                    self._client.table("product_staging")
                    .update(payload)
                    .eq("user_id", user_id)
                    .eq("sku", part_number)
                    .execute()
                )
            else:
                response = (
                    self._client.table("product_staging")
                    .update(payload)
                    .eq("sku", part_number)
                    .execute()
                )

            if not response.data:
                logger.info(f"No rows updated by sku={part_number}, trying by id")
                if user_id:
                    response = (
                        self._client.table("product_staging")
                        .update(payload)
                        .eq("user_id", user_id)
                        .eq("id", part_number)
                        .execute()
                    )
                else:
                    response = (
                        self._client.table("product_staging")
                        .update(payload)
                        .eq("id", part_number)
                        .execute()
                    )

            if response.data:
                logger.info(f"Updated product_staging status to published for {part_number}, user_id={user_id}")
            else:
                logger.warning(f"No product_staging record found to update for {part_number}, user_id={user_id}")

        except APIError as e:
            logger.info("supabase error table=product_staging detail=%s", str(e))
            raise HTTPException(
                status_code=500,
                detail=f"Supabase update product_staging failed: {e}",
            )

    async def update_product_staging_status(
        self, part_number: str, status: str, user_id: str | None = None
    ) -> None:
        """Update the status of a product staging record (e.g., to 'blocked' or 'failed')."""
        payload = {"status": status}
        try:
            if user_id:
                response = (
                    self._client.table("product_staging")
                    .update(payload)
                    .eq("user_id", user_id)
                    .eq("sku", part_number)
                    .execute()
                )
            else:
                response = (
                    self._client.table("product_staging")
                    .update(payload)
                    .eq("sku", part_number)
                    .execute()
                )

            if not response.data:
                if user_id:
                    response = (
                        self._client.table("product_staging")
                        .update(payload)
                        .eq("user_id", user_id)
                        .eq("id", part_number)
                        .execute()
                    )
                else:
                    response = (
                        self._client.table("product_staging")
                        .update(payload)
                        .eq("id", part_number)
                        .execute()
                    )

            if response.data:
                logger.info(f"Updated product_staging status to '{status}' for {part_number}")
            else:
                logger.warning(f"No product_staging record found to update status for {part_number}")

        except APIError as e:
            logger.info("supabase error table=product_staging detail=%s", str(e))
            raise HTTPException(
                status_code=500,
                detail=f"Supabase update product_staging status failed: {e}",
            )

    async def update_product_staging_image(
        self, part_number: str, image_url: str, image_path: str
    ) -> None:
        """Update product staging image by part number (checks both id and sku)."""
        payload = {
            "image_url": image_url,
            "image_path": image_path,
        }
        try:
            response = (
                self._client.table("product_staging")
                .update(payload)
                .eq("id", part_number)
                .execute()
            )
            if not response.data:
                self._client.table("product_staging").update(payload).eq(
                    "sku", part_number
                ).execute()
        except APIError as e:
            logger.info("supabase error table=product_staging detail=%s", str(e))
            raise HTTPException(
                status_code=500,
                detail=f"Supabase update product_staging failed: {e}",
            )
