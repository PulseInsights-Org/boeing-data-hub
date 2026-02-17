"""
Normalization service — transforms raw Boeing data to staging format.

Normalization service – transforms Boeing data to Shopify-friendly format.

Replaces: inline logic in celery_app/tasks/normalization.py
Version: 1.0.0
"""
import logging
from typing import Any, Dict, List

from app.db.staging_store import StagingStore
from app.db.batch_store import BatchStore
from app.utils.boeing_normalize import normalize_boeing_payload

logger = logging.getLogger(__name__)


class NormalizationService:
    def __init__(
        self, staging_store: StagingStore, batch_store: BatchStore
    ) -> None:
        self._staging_store = staging_store
        self._batch_store = batch_store

    async def normalize_chunk(
        self,
        batch_id: str,
        part_numbers: List[str],
        raw_response: Dict[str, Any],
        user_id: str = "system",
    ) -> Dict[str, Any]:
        """Normalize a chunk of products from raw Boeing data into staging."""
        logger.info(
            f"Normalizing {len(part_numbers)} parts for batch {batch_id}"
        )

        line_items = raw_response.get("lineItems", [])
        currency = raw_response.get("currency")

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
                    self._batch_store.record_failure(
                        batch_id, pn, "Not found in Boeing response"
                    )
                    failed_count += 1
                    continue

                normalized_list = normalize_boeing_payload(
                    pn, {"lineItems": [item], "currency": currency}
                )

                if normalized_list:
                    await self._staging_store.upsert_product_staging(
                        normalized_list, user_id=user_id, batch_id=batch_id
                    )
                    normalized_count += 1
                else:
                    self._batch_store.record_failure(
                        batch_id, pn, "Normalization produced no results"
                    )
                    failed_count += 1

            except Exception as e:
                logger.error(f"Failed to normalize {pn}: {e}")
                self._batch_store.record_failure(
                    batch_id, pn, f"Normalization error: {e}"
                )
                failed_count += 1

        return {
            "batch_id": batch_id,
            "normalized": normalized_count,
            "failed": failed_count,
            "total": len(part_numbers),
        }
