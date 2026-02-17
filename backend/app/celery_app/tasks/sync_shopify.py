"""
Shopify sync tasks — updates products in Shopify with new Boeing data.

Shopify sync tasks - updates products in Shopify with new Boeing data.

Tasks:
- update_shopify_product: Updates a single product in Shopify
- sync_single_product_immediate: Triggers immediate sync for one product
Version: 1.0.0
"""
import logging
from typing import Dict, Any

import httpx
from fastapi import HTTPException

from app.celery_app.celery_config import celery_app
from app.celery_app.tasks.base import (
    BaseTask,
    run_async,
    get_shopify_orchestrator,
    get_product_store,
)
from app.core.exceptions import RetryableError, NonRetryableError
from app.db.sync_store import get_sync_store
from app.utils.hash_utils import compute_boeing_hash

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.sync_shopify.update_shopify_product",
    autoretry_for=(RetryableError, ConnectionError, TimeoutError, httpx.ConnectError, httpx.ReadTimeout),
    dont_autoretry_for=(NonRetryableError,),
    retry_backoff=True,
    max_retries=3,
    rate_limit="30/m"  # Shopify rate limit
)
def update_shopify_product(self, sku: str, user_id: str, boeing_data: Dict[str, Any]):
    """
    Update a single product in Shopify with new Boeing data.

    Args:
        sku: Product SKU
        user_id: User ID
        boeing_data: Normalized data from Boeing API
    """
    logger.info(f"Updating Shopify for {sku}")

    sync_store = get_sync_store()
    shopify = get_shopify_orchestrator()
    product_store = get_product_store()

    try:
        # Get Shopify product ID from our database
        product_record = run_async(
            product_store.get_product_by_sku(sku, user_id)
        )

        if not product_record:
            logger.error(f"Product {sku} not found in database")
            sync_store.update_sync_failure(sku, "Product not found in database")
            raise NonRetryableError(f"Product {sku} not found")

        shopify_product_id = product_record.get("shopify_product_id")
        if not shopify_product_id:
            logger.error(f"Product {sku} has no Shopify product ID")
            sync_store.update_sync_failure(sku, "No Shopify product ID")
            raise NonRetryableError(f"Product {sku} has no Shopify ID")

        # Prepare update data
        new_price = boeing_data.get("list_price") or boeing_data.get("net_price")
        new_quantity = boeing_data.get("inventory_quantity", 0)
        inventory_status = boeing_data.get("inventory_status")
        location_quantities = boeing_data.get("location_quantities") or []
        location_summary = boeing_data.get("location_summary")
        is_out_of_stock = boeing_data.get("is_missing_sku", False) or inventory_status == "out_of_stock"

        # Apply 10% markup
        shopify_price = round(new_price * 1.1, 2) if new_price else None

        # Build metafields if we have location summary
        metafields = None
        if location_summary:
            metafields = [{
                "namespace": "boeing",
                "key": "location_summary",
                "value": location_summary,
                "type": "single_line_text_field"
            }]

        # Update Shopify - use location-based inventory if available
        if location_quantities and not is_out_of_stock:
            result = run_async(
                shopify.update_product_pricing(
                    shopify_product_id,
                    price=shopify_price,
                    metafields=metafields,
                )
            )
            run_async(
                shopify.update_inventory_by_location(
                    shopify_product_id,
                    location_quantities,
                )
            )
        else:
            result = run_async(
                shopify.update_product_pricing(
                    shopify_product_id,
                    price=shopify_price,
                    quantity=new_quantity,
                    metafields=metafields,
                )
            )

        # Update sync record - CRITICAL
        new_hash = compute_boeing_hash(boeing_data)
        try:
            sync_store.update_sync_success(
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

        # Also update our products table
        run_async(
            product_store.update_product_pricing(
                sku,
                user_id,
                price=shopify_price,
                cost=new_price,
                inventory=new_quantity,
            )
        )

        logger.info(
            f"Shopify updated for {sku}: price=${shopify_price}, qty={new_quantity}, hash={new_hash}"
        )

        return {
            "status": "success",
            "sku": sku,
            "shopify_product_id": shopify_product_id,
            "new_price": shopify_price,
            "new_quantity": new_quantity,
            "hash": new_hash,
        }

    except HTTPException as e:
        error_msg = f"Shopify API error {e.status_code}: {e.detail}"
        logger.error(f"Failed to update Shopify for {sku}: {error_msg}")

        if 400 <= e.status_code < 500:
            sync_store.update_sync_failure(sku, error_msg)
            raise NonRetryableError(error_msg)
        else:
            raise RetryableError(error_msg)

    except NonRetryableError:
        raise

    except Exception as e:
        logger.error(f"Failed to update Shopify for {sku}: {e}")
        sync_store.update_sync_failure(sku, str(e))
        raise


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.sync_shopify.sync_single_product_immediate",
    max_retries=2,
)
def sync_single_product_immediate(self, sku: str, user_id: str):
    """
    Immediately sync a single product (used after initial publish).

    This bypasses the hourly scheduler for immediate sync.
    Still respects rate limiter.
    """
    logger.info(f"Immediate sync requested for {sku}")

    celery_app.send_task("tasks.sync_boeing.process_boeing_batch", args=[[sku], user_id, -2])

    return {
        "status": "queued",
        "sku": sku,
    }
