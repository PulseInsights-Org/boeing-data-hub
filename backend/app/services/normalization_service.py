"""
Normalization service — transforms raw Boeing data to staging format.

Normalization service – transforms Boeing data to Shopify-friendly format.
Includes location-blocking logic that marks products "blocked" when none
of their warehouse locations match the configured Shopify location map.

Version: 1.1.0
"""
import logging
from typing import Any, Dict, List

from app.db.staging_store import StagingStore
from app.db.batch_store import BatchStore
from app.utils.boeing_normalize import normalize_boeing_payload

logger = logging.getLogger(__name__)


class NormalizationService:
    def __init__(
        self,
        staging_store: StagingStore,
        batch_store: BatchStore,
        location_map: Dict[str, Any] | None = None,
    ) -> None:
        self._staging_store = staging_store
        self._batch_store = batch_store
        self._location_map = location_map or {}

    async def normalize_chunk(
        self,
        batch_id: str,
        part_numbers: List[str],
        raw_response: Dict[str, Any],
        user_id: str = "system",
    ) -> Dict[str, Any]:
        """Normalize a chunk of products from raw Boeing data into staging.

        Applies location-blocking: if a product has location data but none of
        its locations are in the configured Shopify location map, the product
        is saved with status ``"blocked"`` and counted as skipped (not failed).
        """
        logger.info(
            f"Normalizing {len(part_numbers)} parts for batch {batch_id} (user: {user_id})"
        )

        line_items = raw_response.get("lineItems", [])
        currency = raw_response.get("currency")

        # O(1) lookup by part number
        item_lookup = {
            item.get("aviallPartNumber"): item
            for item in line_items
            if item.get("aviallPartNumber")
        }

        normalized_count = 0
        blocked_count = 0
        blocked_pns: List[str] = []
        failed_count = 0

        for pn in part_numbers:
            try:
                item = item_lookup.get(pn)
                if not item:
                    self._batch_store.record_failure(
                        batch_id, pn, "Not found in Boeing response",
                        stage="normalization",
                    )
                    failed_count += 1
                    continue

                normalized_list = normalize_boeing_payload(
                    pn, {"lineItems": [item], "currency": currency}
                )

                if normalized_list:
                    product = normalized_list[0]

                    # Location-blocking: if every warehouse location is outside
                    # the configured Shopify location map, mark as blocked.
                    if product.get("status") != "blocked" and self._location_map:
                        loc_avails = product.get("location_availabilities") or []
                        if loc_avails:
                            has_mapped = any(
                                loc.get("location") in self._location_map
                                for loc in loc_avails
                                if loc.get("location")
                            )
                            if not has_mapped:
                                unmapped = [
                                    loc.get("location")
                                    for loc in loc_avails
                                    if loc.get("location")
                                ]
                                logger.info(
                                    f"Blocking {pn}: only at non-mapped "
                                    f"locations {unmapped}"
                                )
                                product["status"] = "blocked"

                    await self._staging_store.upsert_product_staging(
                        normalized_list, user_id=user_id, batch_id=batch_id
                    )

                    # Blocked products exist in staging (counted by DB trigger
                    # as extracted_count), so they are NOT recorded as failures.
                    # The batch completion formula: extracted_count + failed_count == total.
                    product_status = normalized_list[0].get("status", "fetched")
                    if product_status == "blocked":
                        blocked_pns.append(pn)
                        blocked_count += 1
                    else:
                        normalized_count += 1
                else:
                    self._batch_store.record_failure(
                        batch_id, pn, "Normalization produced no results",
                        stage="normalization",
                    )
                    failed_count += 1

            except Exception as e:
                logger.error(f"Failed to normalize {pn}: {e}")
                self._batch_store.record_failure(
                    batch_id, pn, f"Normalization error: {e}",
                    stage="normalization",
                )
                failed_count += 1

        # Record blocked products as skipped (separate from failed)
        if blocked_pns:
            self._batch_store.record_skipped(batch_id, blocked_pns)

        logger.info(
            f"Normalized {normalized_count}/{len(part_numbers)} parts "
            f"({blocked_count} blocked, {failed_count} failed)"
        )

        return {
            "batch_id": batch_id,
            "normalized": normalized_count,
            "blocked": blocked_count,
            "failed": failed_count,
            "total": len(part_numbers),
        }
