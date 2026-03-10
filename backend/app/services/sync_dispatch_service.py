"""
Sync dispatch service — hourly scheduling and batch dispatch.

Sync dispatch service – hourly/retry/cleanup orchestration.
Contains all dispatch business logic so that celery task definitions
remain thin wrappers that handle only Celery-specific concerns
(locking, retries, beat scheduling).

Version: 1.1.0
"""
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app.db.sync_store import SyncStore
from app.core.constants.sync import MIN_PRODUCTS_FOR_ACTIVE_SLOT
from app.utils.slot_manager import get_slot_distribution, MAX_SKUS_PER_API_CALL
from app.utils.batch_grouping import calculate_batch_groups
from app.utils.schedule_helpers import get_current_hour_utc
from app.utils.dispatch_lock import (
    get_already_dispatched_skus,
    record_dispatched_skus,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = MAX_SKUS_PER_API_CALL  # 10 SKUs per Boeing API call


class SyncDispatchService:
    def __init__(self, sync_store: SyncStore) -> None:
        self._store = sync_store

    # ------------------------------------------------------------------
    # Conflict guard
    # ------------------------------------------------------------------

    @staticmethod
    def is_extraction_session_active(batch_store) -> bool:
        """Return True if any extraction/publish batch is active.

        Used as a conflict guard: sync is deferred while extraction runs.
        Fail-open: returns False on error so sync is never permanently blocked.
        """
        try:
            active_batches = batch_store.get_active_batches()
            if active_batches:
                batch_ids = [b["id"][:8] for b in active_batches[:3]]
                logger.info(
                    f"Extraction session active: {len(active_batches)} batch(es) "
                    f"in progress ({batch_ids})"
                )
                return True
            return False
        except Exception as e:
            logger.warning(
                f"Could not check active batches (fail-open, sync proceeds): {e}"
            )
            return False

    # ------------------------------------------------------------------
    # Single-bucket dispatch (Layer 2 + Layer 3 dedup)
    # ------------------------------------------------------------------

    def dispatch_bucket(
        self,
        bucket: int,
        window_start: datetime,
        dispatch_callback: Callable,
    ) -> Dict[str, Any]:
        """Dispatch all products for a single hour/minute bucket.

        Applies:
          Layer 2 — DB window_start cooldown (products synced after window_start
                    are excluded by sync_store.get_products_for_hour)
          Layer 3 — Redis SKU dedup set (already-dispatched SKUs are subtracted)

        Layer 1 (Redis dispatch lock) is managed by the task layer.

        Args:
            bucket: Hour (0-23) or minute-bucket (0-5) to dispatch.
            window_start: Lower bound for the window_start DB filter.
            dispatch_callback: callable(skus, user_id, bucket) — queues a Boeing batch.

        Returns a stats dict: {bucket, batches_dispatched, products_dispatched, skus_deduped}.
        """
        stats: Dict[str, Any] = {
            "bucket": bucket,
            "batches_dispatched": 0,
            "products_dispatched": 0,
            "skus_deduped": 0,
        }

        slot_counts = self._store.get_slot_counts()
        distribution = get_slot_distribution(slot_counts)
        current_count = slot_counts.get(bucket, 0)

        if current_count >= MIN_PRODUCTS_FOR_ACTIVE_SLOT:
            # ACTIVE SLOT — process normally
            products = self._store.get_products_for_hour(
                bucket,
                status_filter=["pending", "success"],
                window_start=window_start,
            )

            # Layer 3: subtract already-dispatched SKUs
            products, deduped = self._apply_sku_dedup(products, bucket)
            stats["skus_deduped"] = deduped

            if products:
                dispatched_skus = self._dispatch_products(
                    products, bucket, dispatch_callback, stats
                )
                record_dispatched_skus(bucket, dispatched_skus)
                logger.info(
                    f"Active slot {bucket}: dispatched {stats['batches_dispatched']} "
                    f"batches, {stats['products_dispatched']} products"
                )

        elif current_count > 0:
            # FILLING SLOT — aggregate with other filling slots
            logger.info(f"Filling slot {bucket} ({current_count} products)")

            all_filling_products: List[Dict] = []
            for slot in distribution["filling_slots"]:
                slot_products = self._store.get_products_for_hour(
                    slot,
                    status_filter=["pending", "success"],
                    window_start=window_start,
                )
                all_filling_products.extend(slot_products)

            # Layer 3: dedup against the current bucket's dispatched set
            all_filling_products, deduped = self._apply_sku_dedup(
                all_filling_products, bucket
            )
            stats["skus_deduped"] = deduped

            if all_filling_products:
                dispatched_skus = self._dispatch_products(
                    all_filling_products, bucket, dispatch_callback, stats
                )
                record_dispatched_skus(bucket, dispatched_skus)
                logger.info(
                    f"Filling slots aggregated: {len(distribution['filling_slots'])} slots, "
                    f"{stats['batches_dispatched']} batches, "
                    f"{stats['products_dispatched']} products"
                )
        else:
            logger.debug(f"Dormant slot {bucket}, no products to sync")

        return stats

    # ------------------------------------------------------------------
    # Retry dispatch
    # ------------------------------------------------------------------

    def dispatch_retry(self, dispatch_callback: Callable) -> Dict[str, Any]:
        """Collect failed products and re-dispatch in batches."""
        logger.info("=== Retry Sync Dispatch ===")

        failed_products = self._store.get_failed_products_for_retry(limit=200)
        if not failed_products:
            logger.info("No failed products to retry")
            return {"status": "completed", "retries": 0}

        logger.info(f"Found {len(failed_products)} products for retry")

        user_batches: Dict[str, List[Dict]] = {}
        for product in failed_products:
            uid = product.get("user_id", "system")
            user_batches.setdefault(uid, []).append(product)

        batches_dispatched = 0
        products_dispatched = 0

        for user_id, products in user_batches.items():
            batches = calculate_batch_groups(products, BATCH_SIZE)
            for batch in batches:
                skus = [p["sku"] for p in batch]
                self._store.mark_products_syncing(skus)
                dispatch_callback(skus, user_id, -1)
                batches_dispatched += 1
                products_dispatched += len(skus)

        logger.info(
            f"Retry dispatch: {batches_dispatched} batches, "
            f"{products_dispatched} products"
        )

        return {
            "status": "completed",
            "batches_dispatched": batches_dispatched,
            "products_retried": products_dispatched,
        }

    # ------------------------------------------------------------------
    # End-of-day cleanup
    # ------------------------------------------------------------------

    def end_of_day_cleanup(self) -> Dict[str, Any]:
        """Daily cleanup: reset stuck products and log stats."""
        logger.info("=== End of Day Cleanup ===")

        stuck_reset = self._store.reset_stuck_products(stuck_threshold_minutes=60)
        status_summary = self._store.get_sync_status_summary()
        distribution = self._store.get_slot_distribution_summary()

        logger.info(f"Daily Stats: {status_summary}")
        logger.info(
            f"Slot Distribution: efficiency={distribution['efficiency_percent']}%"
        )

        if status_summary.get("high_failure_count", 0) > 0:
            logger.warning(
                f"Products with 3+ failures: {status_summary['high_failure_count']}"
            )

        return {
            "status": "completed",
            "stuck_reset": stuck_reset,
            "summary": status_summary,
            "efficiency": distribution["efficiency_percent"],
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_sku_dedup(
        products: List[Dict], bucket: int
    ) -> tuple[List[Dict], int]:
        """Subtract already-dispatched SKUs (Layer 3 dedup). Returns (filtered, count)."""
        already_dispatched = get_already_dispatched_skus(bucket)
        if not already_dispatched:
            return products, 0
        before = len(products)
        filtered = [p for p in products if p["sku"] not in already_dispatched]
        deduped = before - len(filtered)
        if deduped > 0:
            logger.info(
                f"SKU dedup (bucket {bucket}): filtered {deduped} "
                f"already-dispatched SKUs"
            )
        return filtered, deduped

    def _dispatch_products(
        self,
        products: List[Dict],
        bucket: int,
        dispatch_callback: Callable,
        stats: Dict[str, Any],
    ) -> List[str]:
        """Split products into batches, mark syncing, and call dispatch_callback."""
        batches = calculate_batch_groups(products, BATCH_SIZE)
        all_dispatched_skus: List[str] = []

        for batch in batches:
            skus = [p["sku"] for p in batch]
            user_id = batch[0].get("user_id", "system")

            self._store.mark_products_syncing(skus)
            dispatch_callback(skus, user_id, bucket)

            all_dispatched_skus.extend(skus)
            stats["batches_dispatched"] += 1
            stats["products_dispatched"] += len(skus)

        return all_dispatched_skus
