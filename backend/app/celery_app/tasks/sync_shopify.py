"""
Shopify sync tasks — updates products in Shopify with new Boeing data.

Tasks:
- update_shopify_product:      Update a single product's price/inventory
- sync_single_product_immediate: Trigger immediate sync for one product

All Shopify update logic lives in ShopifyUpdateService.
This task handles only Celery-specific concerns: retry routing and
sync-failure recording on unrecoverable errors.

Version: 1.1.0
"""
import logging
from typing import Dict, Any

import httpx
from fastapi import HTTPException

from app.celery_app.celery_config import celery_app
from app.celery_app.tasks.base import BaseTask, run_async, get_shopify_update_service
from app.core.exceptions import RetryableError, NonRetryableError
from app.db.sync_store import get_sync_store

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
    """Update a single product in Shopify with new Boeing data.

    Delegates price/inventory update, sync-record write, and product-table
    update to ShopifyUpdateService.

    Args:
        sku:        Product SKU
        user_id:    User ID
        boeing_data: Normalized data from Boeing API
    """
    logger.info(f"Updating Shopify for {sku}")

    try:
        svc = get_shopify_update_service()
        return run_async(svc.update_product(sku, user_id, boeing_data))

    except HTTPException as e:
        error_msg = f"Shopify API error {e.status_code}: {e.detail}"
        logger.error(f"Failed to update Shopify for {sku}: {error_msg}")
        if 400 <= e.status_code < 500:
            get_sync_store().update_sync_failure(sku, error_msg)
            raise NonRetryableError(error_msg)
        else:
            raise RetryableError(error_msg)

    except NonRetryableError:
        raise

    except Exception as e:
        logger.error(f"Failed to update Shopify for {sku}: {e}")
        get_sync_store().update_sync_failure(sku, str(e))
        raise


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.sync_shopify.sync_single_product_immediate",
    max_retries=2,
)
def sync_single_product_immediate(self, sku: str, user_id: str):
    """Immediately sync a single product (used after initial publish).

    Bypasses the hourly scheduler for instant sync. Rate limiter still applies.
    """
    logger.info(f"Immediate sync requested for {sku}")
    celery_app.send_task("tasks.sync_boeing.process_boeing_batch", args=[[sku], user_id, -2])
    return {"status": "queued", "sku": sku}
