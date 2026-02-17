"""
Boeing sync tasks — fetches price/availability updates from Boeing API.

Boeing sync task - fetches price/availability data from Boeing API.

Tasks:
- process_boeing_batch: Fetches data for a batch of SKUs (rate limited)

CRITICAL: All Boeing API calls go through the global rate limiter.
This ensures we NEVER exceed 2 requests/minute across all workers.
Version: 1.0.0
"""
import logging
from typing import List, Dict, Any

import httpx

from app.celery_app.celery_config import celery_app
from app.celery_app.tasks.base import (
    BaseTask,
    run_async,
    get_settings,
)
from app.core.exceptions import RetryableError, NonRetryableError
from app.db.sync_store import get_sync_store
from app.utils.rate_limiter import get_boeing_rate_limiter
from app.utils.hash_utils import compute_boeing_hash
from app.utils.boeing_data_extract import extract_boeing_product_data, create_out_of_stock_data
from app.utils.change_detection import should_update_shopify
from app.celery_app.tasks.sync_shopify import update_shopify_product
from app.clients.boeing_client import BoeingClient
from app.utils.dispatch_lock import acquire_batch_lock, release_batch_lock, compute_batch_hash

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.sync_boeing.process_boeing_batch",
    autoretry_for=(RetryableError, ConnectionError, TimeoutError, httpx.ConnectError, httpx.ReadTimeout),
    dont_autoretry_for=(NonRetryableError,),
    retry_backoff=True,
    max_retries=2,
)
def process_boeing_batch(self, skus: List[str], user_id: str, source_hour: int):
    """
    Fetch price/availability from Boeing API for a batch of SKUs.

    Uses global rate limiter to ensure we never exceed 2 req/min.

    Args:
        skus: List of SKUs with full variant suffix (max 10)
        user_id: User ID for context
        source_hour: Source hour bucket (-1 for retries, -2 for immediate)
    """
    if not skus:
        return {"status": "skipped", "reason": "empty_batch"}

    logger.info(f"Boeing batch sync: {len(skus)} SKUs from hour={source_hour}")

    # ── Batch idempotency lock — prevent duplicate processing ───────────
    sku_hash = compute_batch_hash(skus)
    worker_id = self.request.id or "unknown"

    if not acquire_batch_lock(sku_hash, worker_id):
        logger.info(f"Batch already being processed (hash={sku_hash[:12]}...), skipping")
        return {"status": "skipped", "reason": "duplicate_batch", "skus": skus}

    sync_store = get_sync_store()
    rate_limiter = get_boeing_rate_limiter()

    try:
        logger.debug("Acquiring rate limiter token...")
        token_acquired = rate_limiter.wait_for_token(timeout=120)

        if not token_acquired:
            logger.warning("Rate limiter timeout - requeueing batch")
            raise RetryableError("Rate limiter timeout")

        logger.debug("Token acquired, calling Boeing API...")

        settings = get_settings()
        boeing_client = BoeingClient(settings)

        boeing_response = run_async(
            boeing_client.fetch_price_availability_batch(skus)
        )

        success_count = 0
        failure_count = 0
        no_change_count = 0
        out_of_stock_count = 0

        for sku in skus:
            try:
                product_data = extract_boeing_product_data(boeing_response, sku)

                if not product_data:
                    logger.info(f"SKU {sku} not in Boeing response - treating as out of stock")
                    product_data = create_out_of_stock_data(sku)
                    out_of_stock_count += 1

                records = sync_store.get_products_by_skus([sku])
                record = records[0] if records else {}

                should_update, reason = should_update_shopify(
                    product_data,
                    record.get("last_boeing_hash"),
                    record.get("last_price"),
                    record.get("last_quantity"),
                )

                if should_update:
                    update_shopify_product.delay(sku, user_id, product_data)
                    success_count += 1
                    logger.debug(f"Queued Shopify update for {sku}: {reason}")
                else:
                    new_hash = compute_boeing_hash(product_data)
                    sync_store.update_sync_success(
                        sku,
                        new_hash,
                        product_data.get("list_price"),
                        product_data.get("inventory_quantity"),
                        inventory_status=product_data.get("inventory_status"),
                        locations=product_data.get("location_quantities"),
                    )
                    no_change_count += 1
                    logger.debug(f"No change for {sku}")

            except Exception as sku_err:
                logger.error(f"Error processing SKU {sku}: {sku_err}")
                sync_store.update_sync_failure(sku, str(sku_err))
                failure_count += 1

        logger.info(
            f"Boeing batch complete: {success_count} updates queued, "
            f"{no_change_count} unchanged, {out_of_stock_count} out-of-stock, {failure_count} failed"
        )

        return {
            "status": "completed",
            "skus_processed": len(skus),
            "updates_queued": success_count,
            "no_change": no_change_count,
            "out_of_stock": out_of_stock_count,
            "failures": failure_count,
        }

    except (RetryableError, ConnectionError, TimeoutError) as e:
        for sku in skus:
            sync_store.update_sync_failure(sku, str(e))
        raise

    except Exception as e:
        logger.error(f"Boeing batch sync failed: {e}")
        for sku in skus:
            sync_store.update_sync_failure(sku, str(e))
        raise

    finally:
        release_batch_lock(sku_hash)
