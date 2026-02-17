"""
Normalization tasks — transforms raw Boeing data to staging format.

Normalization tasks for transforming Boeing data to Shopify-friendly format.

Tasks:
- normalize_chunk: Normalizes a chunk of products from raw Boeing data
Version: 1.0.0
"""
import logging
from typing import List, Dict, Any

from app.celery_app.celery_config import celery_app
from app.celery_app.tasks.base import (
    BaseTask,
    run_async,
    get_staging_store,
    get_batch_store,
    get_settings,
)
from app.utils.boeing_normalize import normalize_boeing_payload
from app.celery_app.tasks.batch import check_batch_completion
from app.core.exceptions import RetryableError, NonRetryableError

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.normalization.normalize_chunk",
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
    """
    logger.info(f"Normalizing {len(part_numbers)} parts for batch {batch_id} (user: {user_id})")

    try:
        staging_store = get_staging_store()
        batch_store = get_batch_store()
        location_map = (get_settings().shopify_location_map or {})

        line_items = raw_response.get("lineItems", [])
        currency = raw_response.get("currency")

        # Create lookup by part number for O(1) access
        item_lookup = {
            item.get("aviallPartNumber"): item
            for item in line_items
            if item.get("aviallPartNumber")
        }

        normalized_count = 0
        blocked_count = 0
        blocked_pns = []
        failed_count = 0

        for pn in part_numbers:
            try:
                item = item_lookup.get(pn)
                if not item:
                    batch_store.record_failure(batch_id, pn, "Not found in Boeing response", stage="normalization")
                    failed_count += 1
                    continue

                normalized_list = normalize_boeing_payload(
                    pn,
                    {"lineItems": [item], "currency": currency}
                )

                if normalized_list:
                    # Check location mapping: if product has location data but
                    # NONE are in the configured Shopify location map, mark blocked.
                    # This catches products at non-US locations (e.g., "Germany CSC")
                    # before they reach the publish queue.
                    product = normalized_list[0]
                    if product.get("status") != "blocked" and location_map:
                        loc_avails = product.get("location_availabilities") or []
                        if loc_avails:
                            has_mapped = any(
                                loc.get("location") in location_map
                                for loc in loc_avails
                                if loc.get("location")
                            )
                            if not has_mapped:
                                unmapped = [loc.get("location") for loc in loc_avails if loc.get("location")]
                                logger.info(
                                    f"Blocking {pn}: only at non-mapped locations {unmapped}"
                                )
                                product["status"] = "blocked"

                    run_async(staging_store.upsert_product_staging(normalized_list, user_id=user_id, batch_id=batch_id))

                    # Check if the product was marked "blocked" (no price, no inventory,
                    # or no mapped locations). Blocked products are tracked as SKIPPED
                    # (not failed) because they exist in product_staging and are counted
                    # in extracted_count by the DB trigger. The completion formula is:
                    # extracted_count + failed_count == total_items
                    product_status = normalized_list[0].get("status", "fetched")
                    if product_status == "blocked":
                        blocked_pns.append(pn)
                        blocked_count += 1
                    else:
                        normalized_count += 1
                else:
                    batch_store.record_failure(batch_id, pn, "Normalization produced no results", stage="normalization")
                    failed_count += 1

            except Exception as e:
                logger.error(f"Failed to normalize {pn}: {e}")
                batch_store.record_failure(batch_id, pn, f"Normalization error: {e}", stage="normalization")
                failed_count += 1

        # Record blocked products as skipped (separate from failed)
        if blocked_pns:
            batch_store.record_skipped(batch_id, blocked_pns)

        # Check if batch is complete
        check_batch_completion.delay(batch_id)

        logger.info(
            f"Normalized {normalized_count}/{len(part_numbers)} parts "
            f"({blocked_count} blocked, {failed_count} failed)"
        )

        return {
            "batch_id": batch_id,
            "normalized": normalized_count,
            "blocked": blocked_count,
            "failed": failed_count,
            "total": len(part_numbers)
        }

    except Exception as e:
        # SAFETY NET: If the entire task crashes (bad response format,
        # dependency init failure, etc.), record ALL parts in this chunk
        # as failed so they are not silently lost.
        logger.error(
            f"normalize_chunk CRASHED for batch {batch_id} "
            f"({len(part_numbers)} parts lost): {e}"
        )
        is_retryable = isinstance(e, (RetryableError, ConnectionError, TimeoutError))
        is_last_attempt = self.request.retries >= self.max_retries

        if not is_retryable or is_last_attempt:
            try:
                _batch_store = get_batch_store()
                for pn in part_numbers:
                    _batch_store.record_failure(
                        batch_id, pn,
                        f"Normalization task crash: {e}",
                        stage="normalization"
                    )
                check_batch_completion.delay(batch_id)
            except Exception as inner:
                logger.critical(
                    f"CANNOT record failures for batch {batch_id} "
                    f"({len(part_numbers)} parts PERMANENTLY LOST): {inner}"
                )
        raise
