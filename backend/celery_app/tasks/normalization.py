"""
Normalization tasks for transforming Boeing data to Shopify-friendly format.

Tasks:
- normalize_chunk: Normalizes a chunk of products from raw Boeing data
"""
import logging
from typing import List, Dict, Any

from celery_app.celery_config import celery_app
from celery_app.tasks.base import (
    BaseTask,
    run_async,
    get_supabase_store,
    get_batch_store
)
from app.utils.boeing_normalize import normalize_boeing_payload
from app.core.exceptions import RetryableError, NonRetryableError

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="celery_app.tasks.normalization.normalize_chunk",
    autoretry_for=(RetryableError, ConnectionError, TimeoutError),
    dont_autoretry_for=(NonRetryableError,),
    retry_backoff=True,
    max_retries=3
)
def normalize_chunk(self, batch_id: str, part_numbers: List[str], raw_response: Dict[str, Any], user_id: str = "system"):
    """
    Normalize a chunk of products from raw Boeing data.

    Transforms Boeing API response format to Shopify-friendly format
    and stores in product_staging table.

    Args:
        batch_id: Batch identifier for tracking
        part_numbers: List of part numbers in this chunk
        raw_response: Raw response from Boeing API
        user_id: User ID who initiated the search

    Returns:
        dict: Summary of normalization results
    """
    logger.info(f"Normalizing {len(part_numbers)} parts for batch {batch_id} (user: {user_id})")

    supabase_store = get_supabase_store()
    batch_store = get_batch_store()

    line_items = raw_response.get("lineItems", [])
    currency = raw_response.get("currency")

    # Create lookup by part number for O(1) access
    item_lookup = {
        item.get("aviallPartNumber"): item
        for item in line_items
        if item.get("aviallPartNumber")
    }

    normalized_count = 0
    failed_count = 0

    for pn in part_numbers:
        try:
            item = item_lookup.get(pn)
            if not item:
                batch_store.record_failure(batch_id, pn, "Not found in Boeing response")
                failed_count += 1
                continue

            # Use existing normalize function
            normalized_list = normalize_boeing_payload(
                pn,
                {"lineItems": [item], "currency": currency}
            )

            if normalized_list:
                # Pass user_id and batch_id when storing products
                run_async(supabase_store.upsert_product_staging(normalized_list, user_id=user_id, batch_id=batch_id))
                normalized_count += 1
            else:
                batch_store.record_failure(batch_id, pn, "Normalization produced no results")
                failed_count += 1

        except Exception as e:
            logger.error(f"Failed to normalize {pn}: {e}")
            batch_store.record_failure(batch_id, pn, f"Normalization error: {e}")
            failed_count += 1

    # Update batch progress
    if normalized_count > 0:
        batch_store.increment_normalized(batch_id, normalized_count)

    # Check if batch is complete
    from celery_app.tasks.batch import check_batch_completion
    check_batch_completion.delay(batch_id)

    logger.info(f"Normalized {normalized_count}/{len(part_numbers)} parts ({failed_count} failed)")

    return {
        "batch_id": batch_id,
        "normalized": normalized_count,
        "failed": failed_count,
        "total": len(part_numbers)
    }
