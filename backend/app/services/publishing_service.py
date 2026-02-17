"""
Publishing service — Shopify product create/update with saga pattern.

Handles route-level and batch-level product publishing, including
image upload, location mapping, and saga compensation on failure.
Version: 1.0.0
"""
import json
import logging
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app.core.config import Settings
from app.core.constants.pricing import (
    FALLBACK_IMAGE_URL,
    MARKUP_FACTOR,
    DEFAULT_CERTIFICATE,
    DEFAULT_CONDITION,
)
from app.core.exceptions import NonRetryableError
from app.db.staging_store import StagingStore
from app.db.product_store import ProductStore
from app.db.image_store import ImageStore
from app.db.sync_store import SyncStore
from app.services.shopify_orchestrator import ShopifyOrchestrator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def strip_variant_suffix(value: str) -> str:
    """Strip variant suffix from SKU (e.g., 'WF338109=K3' -> 'WF338109')."""
    if not value:
        return ""
    return value.split("=", 1)[0]


def prepare_shopify_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Transform normalized Boeing data to Shopify product format."""
    record.setdefault("shopify", {})

    list_price = record.get("list_price")
    net_price = record.get("net_price")
    base_cost = list_price if list_price is not None else net_price
    shop_price = (base_cost * MARKUP_FACTOR) if base_cost is not None else record.get("price")

    shopify_sku = strip_variant_suffix(record.get("sku") or "")
    shopify_title = strip_variant_suffix(record.get("title") or "")

    record["shopify"].update({
        "title": shopify_title,
        "sku": shopify_sku,
        "description": record.get("boeing_name") or shopify_title,
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
        "product_image": record.get("image_url"),
        "thumbnail_image": record.get("boeing_thumbnail_url"),
        "cert": DEFAULT_CERTIFICATE,
        "condition": record.get("condition") or DEFAULT_CONDITION,
        "pma": record.get("pma"),
        "estimated_lead_time_days": record.get("estimated_lead_time_days"),
        "trace": record.get("trace"),
        "expiration_date": record.get("expiration_date"),
        "notes": record.get("notes"),
    })

    record["name"] = record.get("boeing_name") or shopify_title
    record["description"] = record.get("boeing_description") or ""
    return record


def _parse_location_summary(summary: str):
    """Parse 'Dallas Central: 243; Miami, FL: 10' into list of dicts."""
    parsed = []
    if not summary:
        return parsed
    for part in summary.split(";"):
        part = part.strip()
        if ":" in part:
            loc_name, qty_str = part.rsplit(":", 1)
            loc_name = loc_name.strip()
            try:
                qty = int(qty_str.strip())
            except ValueError:
                qty = 0
            if loc_name:
                parsed.append({"location": loc_name, "quantity": qty})
    return parsed


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class PublishingService:
    def __init__(
        self,
        shopify: ShopifyOrchestrator,
        staging_store: StagingStore,
        product_store: ProductStore,
        image_store: ImageStore,
        sync_store: Optional[SyncStore] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._shopify = shopify
        self._staging = staging_store
        self._products = product_store
        self._images = image_store
        self._sync = sync_store
        self._settings = settings
        self._logger = logging.getLogger("publishing_service")

    # ------------------------------------------------------------------
    # Route-level publish (simple path, from shopify_service.py)
    # ------------------------------------------------------------------

    async def publish_product_by_part_number(
        self, part_number: str, user_id: str = "system"
    ) -> Dict[str, Any]:
        """
        Publish or update a product in Shopify (idempotent).

        1. Check staging for shopify_product_id -> UPDATE
        2. Check Shopify by SKU -> UPDATE
        3. Otherwise -> CREATE
        """
        record = await self._staging.get_product_staging_by_part_number(
            part_number, user_id=user_id
        )
        if not record:
            raise HTTPException(
                status_code=404,
                detail=f"Product staging not found for part number {part_number}",
            )

        self._logger.info(
            "shopify publish staging_record=%s",
            json.dumps(record, ensure_ascii=True),
        )

        existing_shopify_id = record.get("shopify_product_id")

        # Image upload
        record = await self._upload_image(record, part_number)

        # Prepare shopify payload
        record = self._prepare_record_for_route(record)

        # Publish or update
        shopify_product_id = await self._create_or_update(
            record, part_number, existing_shopify_id
        )

        shopify_id_str = str(shopify_product_id) if shopify_product_id else None

        await self._products.upsert_product(
            record, shopify_product_id=shopify_id_str, user_id=user_id
        )
        if shopify_id_str:
            await self._staging.update_product_staging_shopify_id(
                part_number, shopify_id_str, user_id=user_id
            )

        return {"success": True, "shopifyProductId": shopify_id_str}

    # ------------------------------------------------------------------
    # Batch-level publish (full path, from publishing task logic)
    # ------------------------------------------------------------------

    async def publish_product_for_batch(
        self,
        record: Dict[str, Any],
        part_number: str,
        user_id: str = "system",
    ) -> Dict[str, Any]:
        """
        Full publish with validation, location mapping, image upload,
        saga compensation, and sync schedule creation.

        Returns dict with success, shopify_product_id, action, is_new_product.
        Raises NonRetryableError for validation failures.
        """
        # --- 1. Validate price & inventory ---
        price = (
            record.get("price")
            or record.get("list_price")
            or record.get("net_price")
            or record.get("cost_per_item")
        )
        inventory = record.get("inventory_quantity")

        if price is None or price == 0:
            raise NonRetryableError(
                f"Product {part_number} has no valid price. Cannot publish."
            )
        if inventory is None or inventory == 0:
            raise NonRetryableError(
                f"Product {part_number} has no inventory. Cannot publish."
            )

        # --- 2. Location mapping ---
        location_map = (self._settings.shopify_location_map or {}) if self._settings else {}
        location_summary = record.get("location_summary") or ""
        location_availabilities = record.get("location_availabilities") or []

        parsed_locations = _parse_location_summary(location_summary)
        locations_to_check = location_availabilities or parsed_locations

        if locations_to_check:
            mapped, skipped = [], []
            for loc in locations_to_check:
                loc_name = loc.get("location")
                if not loc_name:
                    continue
                if loc_name in location_map:
                    mapped.append(loc)
                else:
                    skipped.append(loc_name)

            if skipped:
                self._logger.warning(
                    f"Product {part_number}: skipping non-mapped locations {skipped}"
                )
            if not mapped:
                raise NonRetryableError(
                    f"Product {part_number} only at non-mapped locations {skipped}. "
                    "No publishable US inventory."
                )
            record.setdefault("shopify", {})
            record["shopify"]["location_quantities"] = mapped
        else:
            self._logger.warning(
                f"Product {part_number} has no location data. "
                "Inventory will use default Shopify location."
            )

        existing_shopify_id = record.get("shopify_product_id")

        # --- 3. Image upload ---
        record = await self._upload_image(record, part_number)

        # --- 4. Prepare Shopify payload ---
        record = prepare_shopify_record(record)

        # --- 5. Publish or update in Shopify (idempotent) ---
        is_new_product = False
        if existing_shopify_id:
            result = await self._shopify.update_product(existing_shopify_id, record)
            shopify_product_id = result.get("product", {}).get("id") or existing_shopify_id
        else:
            sku = record.get("sku") or part_number
            found_id = await self._shopify.find_product_by_sku(sku)
            if found_id:
                result = await self._shopify.update_product(found_id, record)
                shopify_product_id = result.get("product", {}).get("id") or found_id
            else:
                result = await self._shopify.publish_product(record)
                shopify_product_id = result.get("product", {}).get("id")
                is_new_product = True

        if not shopify_product_id:
            raise ValueError("Shopify did not return product ID")

        # --- 6. DB save with saga compensation ---
        try:
            await self._products.upsert_product(
                record, shopify_product_id=str(shopify_product_id), user_id=user_id
            )
            await self._staging.update_product_staging_shopify_id(
                part_number, str(shopify_product_id), user_id=user_id
            )
        except Exception as db_err:
            if is_new_product:
                self._logger.error(
                    f"DB save failed after Shopify CREATE. "
                    f"Compensating: deleting Shopify product {shopify_product_id}"
                )
                try:
                    await self._shopify.delete_product(shopify_product_id)
                except Exception as rollback_err:
                    self._logger.critical(
                        f"ORPHANED PRODUCT: Shopify ID {shopify_product_id} "
                        f"for {part_number}. DB: {db_err}, Rollback: {rollback_err}"
                    )
            raise db_err

        # --- 7. Sync schedule ---
        if self._sync:
            try:
                full_sku = record.get("sku") or record.get("aviall_part_number") or part_number
                self._sync.upsert_sync_schedule(
                    sku=full_sku,
                    user_id=user_id,
                    initial_price=record.get("list_price") or record.get("net_price"),
                    initial_quantity=record.get("inventory_quantity"),
                    shopify_product_id=str(shopify_product_id),
                )
            except Exception as sync_err:
                self._logger.warning(
                    f"Failed to upsert sync schedule for {part_number}: {sync_err}"
                )

        action = "updated" if not is_new_product else "created"
        return {
            "success": True,
            "shopify_product_id": str(shopify_product_id),
            "action": action,
            "is_new_product": is_new_product,
        }

    # ------------------------------------------------------------------
    # Other operations
    # ------------------------------------------------------------------

    async def update_product(
        self, shopify_product_id: str, product: Dict[str, Any]
    ) -> Dict[str, Any]:
        self._logger.info(
            "shopify update id=%s payload=%s",
            shopify_product_id,
            json.dumps(product, ensure_ascii=True),
        )
        data = await self._shopify.update_product(shopify_product_id, product)
        sp = data.get("product") or {}
        return {
            "success": True,
            "shopifyProductId": str(sp["id"]) if sp.get("id") is not None else None,
        }

    async def find_product_by_sku(self, sku: str) -> str | None:
        return await self._shopify.find_product_by_sku(sku)

    async def setup_metafield_definitions(self) -> None:
        await self._shopify.create_metafield_definitions()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _upload_image(
        self, record: Dict[str, Any], part_number: str
    ) -> Dict[str, Any]:
        """Upload Boeing image to Supabase or use fallback."""
        boeing_url = record.get("boeing_image_url") or record.get("boeing_thumbnail_url")
        if boeing_url:
            try:
                image_url, image_path = await self._images.upload_image_from_url(
                    boeing_url, part_number
                )
                record["image_url"] = image_url
                record["image_path"] = image_path
                await self._staging.update_product_staging_image(
                    part_number, image_url, image_path
                )
            except Exception as exc:
                self._logger.warning(
                    f"Image upload failed, using placeholder: {exc}"
                )
                record["image_url"] = FALLBACK_IMAGE_URL
        else:
            record["image_url"] = FALLBACK_IMAGE_URL
        return record

    def _prepare_record_for_route(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare record for route-level publish (delegates to shared function)."""
        return prepare_shopify_record(record)

    async def _create_or_update(
        self,
        record: Dict[str, Any],
        part_number: str,
        existing_shopify_id: Optional[str],
    ):
        """Idempotent create-or-update in Shopify."""
        if existing_shopify_id:
            data = await self._shopify.update_product(existing_shopify_id, record)
            return data.get("product", {}).get("id") or existing_shopify_id

        sku = record.get("sku") or part_number
        found_id = await self._shopify.find_product_by_sku(sku)
        if found_id:
            data = await self._shopify.update_product(found_id, record)
            return data.get("product", {}).get("id") or found_id

        data = await self._shopify.publish_product(record)
        return data.get("product", {}).get("id")
