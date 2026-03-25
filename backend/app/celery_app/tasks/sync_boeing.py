"""
Boeing sync tasks — fetches price/availability updates from Boeing API.

Tasks:
- process_boeing_batch: Fetch data for a batch of SKUs (rate limited)

All fetch, rate-limiting, and change-detection logic lives in BoeingFetchService.
This task handles only Celery-specific concerns: batch idempotency locking
and retry/error routing.

CRITICAL: All Boeing API calls go through the global rate limiter inside
BoeingFetchService — we NEVER exceed 2 requests/minute across all workers.

Version: 1.1.0
"""
import logging
from typing import List

import httpx

from app.celery_app.celery_config import celery_app
from app.celery_app.tasks.base import BaseTask, run_async, get_boeing_fetch_service
from app.core.exceptions import RetryableError, NonRetryableError
from app.db.sync_store import get_sync_store
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
    """Fetch price/availability from Boeing API for a batch of SKUs.

    Args:
        skus:        List of SKUs with full variant suffix (max 10)
        user_id:     User ID for context
        source_hour: Source hour bucket (-1 for retries, -2 for immediate)
    """
    if not skus:
        return {"status": "skipped", "reason": "empty_batch"}

    logger.info(f"Boeing batch sync: {len(skus)} SKUs from hour={source_hour}")

    # ── Batch idempotency lock — prevent duplicate processing ──────────
    sku_hash = compute_batch_hash(skus)
    worker_id = self.request.id or "unknown"

    if not acquire_batch_lock(sku_hash, worker_id):
        logger.info(f"Batch already being processed (hash={sku_hash[:12]}...), skipping")
        return {"status": "skipped", "reason": "duplicate_batch", "skus": skus}

    sync_store = get_sync_store()

    try:
        from app.celery_app.tasks.sync_shopify import update_shopify_product

        svc = get_boeing_fetch_service()
        result = run_async(
            svc.process_batch(
                skus,
                user_id,
                source_hour,
                shopify_update_callback=lambda sku, uid, data: update_shopify_product.delay(
                    sku, uid, data
                ),
            )
        )
        return result

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
