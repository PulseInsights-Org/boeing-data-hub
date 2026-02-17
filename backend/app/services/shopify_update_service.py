"""
Shopify update service — sync-time Shopify product updates.

Handles price/inventory sync from Boeing data to Shopify products.
All methods are async; Celery tasks wrap with ``run_async()``.
Version: 1.0.0
"""
import logging
from typing import Any, Dict

from app.core.constants.pricing import MARKUP_FACTOR
from app.core.exceptions import RetryableError, NonRetryableError
from app.db.sync_store import SyncStore
from app.db.product_store import ProductStore
from app.services.shopify_orchestrator import ShopifyOrchestrator
from app.utils.hash_utils import compute_boeing_hash

logger = logging.getLogger(__name__)


class ShopifyUpdateService:
    def __init__(
        self,
        shopify: ShopifyOrchestrator,
        sync_store: SyncStore,
        product_store: ProductStore,
    ) -> None:
        self._shopify = shopify
        self._sync = sync_store
        self._products = product_store

    async def update_product(
        self,
        sku: str,
        user_id: str,
        boeing_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Update a single Shopify product with new Boeing data.

        Args:
            sku: Product SKU
            user_id: User context
            boeing_data: Normalized data from Boeing API
        """
        logger.info(f"Updating Shopify for {sku}")

        # Get product from our DB
        product_record = await self._products.get_product_by_sku(sku, user_id)

        if not product_record:
            logger.error(f"Product {sku} not found in database")
            self._sync.update_sync_failure(sku, "Product not found in database")
            raise NonRetryableError(f"Product {sku} not found")

        shopify_product_id = product_record.get("shopify_product_id")
        if not shopify_product_id:
            logger.error(f"Product {sku} has no Shopify product ID")
            self._sync.update_sync_failure(sku, "No Shopify product ID")
            raise NonRetryableError(f"Product {sku} has no Shopify ID")

        # Prepare update data
        new_price = boeing_data.get("list_price") or boeing_data.get("net_price")
        new_quantity = boeing_data.get("inventory_quantity", 0)
        inventory_status = boeing_data.get("inventory_status")
        location_quantities = boeing_data.get("location_quantities") or []
        location_summary = boeing_data.get("location_summary")
        is_out_of_stock = (
            boeing_data.get("is_missing_sku", False)
            or inventory_status == "out_of_stock"
        )

        # Apply markup
        shopify_price = round(new_price * MARKUP_FACTOR, 2) if new_price else None

        # Build metafields if we have location summary
        metafields = None
        if location_summary:
            metafields = [{
                "namespace": "boeing",
                "key": "location_summary",
                "value": location_summary,
                "type": "single_line_text_field",
            }]

        # Update Shopify
        if location_quantities and not is_out_of_stock:
            await self._shopify.update_product_pricing(
                shopify_product_id, price=shopify_price, metafields=metafields
            )
            await self._shopify.update_inventory_by_location(
                shopify_product_id, location_quantities
            )
        else:
            await self._shopify.update_product_pricing(
                shopify_product_id,
                price=shopify_price,
                quantity=new_quantity,
                metafields=metafields,
            )

        # Update sync record
        new_hash = compute_boeing_hash(boeing_data)
        try:
            self._sync.update_sync_success(
                sku,
                new_hash,
                new_price,
                new_quantity,
                inventory_status=inventory_status,
                locations=location_quantities,
            )
        except Exception as db_err:
            error_msg = f"Shopify updated but DB sync record failed: {db_err}"
            logger.error(f"CRITICAL: {error_msg}")
            raise RetryableError(error_msg)

        # Update our products table
        await self._products.update_product_pricing(
            sku, user_id,
            price=shopify_price, cost=new_price, inventory=new_quantity,
        )

        logger.info(
            f"Shopify updated for {sku}: price=${shopify_price}, "
            f"qty={new_quantity}, hash={new_hash}"
        )

        return {
            "status": "success",
            "sku": sku,
            "shopify_product_id": shopify_product_id,
            "new_price": shopify_price,
            "new_quantity": new_quantity,
            "hash": new_hash,
        }
