"""
Boeing fetch service — sync-time price/availability retrieval.

Boeing fetch service – sync-time Boeing API calls + change detection.

Replaces: sync_boeing_batch logic in celery_app/tasks/sync_dispatcher.py

All methods are async. Celery tasks wrap with ``run_async()``.
Version: 1.0.0
"""
import logging
from typing import Any, Dict, List

from app.clients.boeing_client import BoeingClient
from app.db.sync_store import SyncStore
from app.utils.rate_limiter import RateLimiter
from app.utils.boeing_data_extract import extract_boeing_product_data, create_out_of_stock_data
from app.utils.change_detection import should_update_shopify
from app.utils.hash_utils import compute_boeing_hash
from app.core.exceptions import RetryableError
from app.utils.cycle_tracker import record_product_change

logger = logging.getLogger(__name__)


class BoeingFetchService:
    def __init__(
        self,
        boeing_client: BoeingClient,
        sync_store: SyncStore,
        rate_limiter: RateLimiter,
    ) -> None:
        self._client = boeing_client
        self._sync = sync_store
        self._limiter = rate_limiter

    async def process_batch(
        self,
        skus: List[str],
        user_id: str,
        source_hour: int,
        shopify_update_callback=None,
    ) -> Dict[str, Any]:
        """
        Fetch price/availability from Boeing API for a batch of SKUs.

        Args:
            skus: SKUs with variant suffix (max 10)
            user_id: User context
            source_hour: Hour bucket (-1 = retry, -2 = immediate)
            shopify_update_callback: callable(sku, user_id, boeing_data) to queue Shopify update
        """
        if not skus:
            return {"status": "skipped", "reason": "empty_batch"}

        logger.info(f"Boeing batch sync: {len(skus)} SKUs from hour={source_hour}")

        # Acquire rate limiter token
        logger.debug("Acquiring rate limiter token...")
        token_acquired = self._limiter.wait_for_token(timeout=120)
        if not token_acquired:
            logger.warning("Rate limiter timeout - requeueing batch")
            raise RetryableError("Rate limiter timeout")

        logger.debug("Token acquired, calling Boeing API...")

        boeing_response = await self._client.fetch_price_availability_batch(skus)

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

                records = self._sync.get_products_by_skus([sku])
                record = records[0] if records else {}

                should_update, reason = should_update_shopify(
                    product_data,
                    record.get("last_boeing_hash"),
                    record.get("last_price"),
                    record.get("last_quantity"),
                )

                if should_update:
                    record_product_change(sku, reason)
                    if shopify_update_callback:
                        shopify_update_callback(sku, user_id, product_data)
                    success_count += 1
                    logger.debug(f"Queued Shopify update for {sku}: {reason}")
                else:
                    new_hash = compute_boeing_hash(product_data)
                    self._sync.update_sync_success(
                        sku,
                        new_hash,
                        product_data.get("list_price"),
                        product_data.get("inventory_quantity"),
                        inventory_status=product_data.get("inventory_status"),
                        locations=product_data.get("location_quantities"),
                    )
                    no_change_count += 1

            except Exception as sku_err:
                logger.error(f"Error processing SKU {sku}: {sku_err}")
                self._sync.update_sync_failure(sku, str(sku_err))
                failure_count += 1

        logger.info(
            f"Boeing batch complete: {success_count} updates queued, "
            f"{no_change_count} unchanged, {out_of_stock_count} out-of-stock, "
            f"{failure_count} failed"
        )

        return {
            "status": "completed",
            "skus_processed": len(skus),
            "updates_queued": success_count,
            "no_change": no_change_count,
            "out_of_stock": out_of_stock_count,
            "failures": failure_count,
        }
