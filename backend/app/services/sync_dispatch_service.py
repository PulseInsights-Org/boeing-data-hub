"""
Sync dispatch service — hourly scheduling and batch dispatch.

Sync dispatch service – hourly/retry/cleanup orchestration.

Replaces: dispatch logic in celery_app/tasks/sync_dispatcher.py
All methods are synchronous (sync_store is synchronous).
Version: 1.0.0
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.db.sync_store import SyncStore
from app.core.constants.sync import MIN_PRODUCTS_FOR_ACTIVE_SLOT
from app.utils.slot_manager import get_slot_distribution, MAX_SKUS_PER_API_CALL
from app.utils.batch_grouping import calculate_batch_groups
from app.utils.schedule_helpers import get_current_hour_utc

logger = logging.getLogger(__name__)

BATCH_SIZE = MAX_SKUS_PER_API_CALL  # 10 SKUs per Boeing API call


class SyncDispatchService:
    def __init__(self, sync_store: SyncStore) -> None:
        self._store = sync_store

    # ------------------------------------------------------------------
    # Hourly dispatch
    # ------------------------------------------------------------------

    def dispatch_hourly(
        self,
        sync_mode: str,
        test_bucket_count: int,
        dispatch_callback,
    ) -> Dict[str, Any]:
        """
        Main sync dispatcher.

        Args:
            sync_mode: "production" or "testing"
            test_bucket_count: Number of minute buckets in testing mode
            dispatch_callback: callable(skus, user_id, bucket) that queues a Boeing batch
        """
        now = datetime.now(timezone.utc)

        if sync_mode == "testing":
            current_bucket = now.minute // 10
            logger.info("=" * 60)
            logger.info(f"[TESTING MODE] Sync Dispatch Started")
            logger.info(f"   Minute bucket: {current_bucket} (of {test_bucket_count})")
            logger.info(f"   Current time: {now.strftime('%H:%M:%S')} UTC")
            logger.info("=" * 60)
        else:
            current_bucket = get_current_hour_utc()
            if now.minute < 45:
                logger.debug(f"Not in sync window (minute={now.minute}), skipping")
                return {"status": "skipped", "reason": "not_in_sync_window"}
            logger.info("=" * 60)
            logger.info(f"[PRODUCTION MODE] Sync Dispatch Started")
            logger.info(f"   Hour bucket: {current_bucket} (of 24)")
            logger.info(f"   Current time: {now.strftime('%H:%M:%S')} UTC")
            logger.info("=" * 60)

        slot_counts = self._store.get_slot_counts()
        distribution = get_slot_distribution(slot_counts)

        logger.info(
            f"Slot distribution: {distribution['active_count']} active, "
            f"{distribution['filling_count']} filling, "
            f"{distribution['dormant_count']} dormant"
        )

        batches_dispatched = 0
        products_dispatched = 0

        current_count = slot_counts.get(current_bucket, 0)

        if current_count >= MIN_PRODUCTS_FOR_ACTIVE_SLOT:
            products = self._store.get_products_for_hour(
                current_bucket, status_filter=["pending", "success"]
            )
            if products:
                batches = calculate_batch_groups(products, BATCH_SIZE)
                for batch in batches:
                    skus = [p["sku"] for p in batch]
                    user_id = batch[0].get("user_id", "system")
                    self._store.mark_products_syncing(skus)
                    dispatch_callback(skus, user_id, current_bucket)
                    batches_dispatched += 1
                    products_dispatched += len(skus)

        elif current_count > 0:
            all_filling: List[Dict] = []
            for slot in distribution["filling_slots"]:
                slot_products = self._store.get_products_for_hour(
                    slot, status_filter=["pending", "success"]
                )
                all_filling.extend(slot_products)
            if all_filling:
                batches = calculate_batch_groups(all_filling, BATCH_SIZE)
                for batch in batches:
                    skus = [p["sku"] for p in batch]
                    user_id = batch[0].get("user_id", "system")
                    self._store.mark_products_syncing(skus)
                    dispatch_callback(skus, user_id, current_bucket)
                    batches_dispatched += 1
                    products_dispatched += len(skus)

        stuck_reset = self._store.reset_stuck_products(stuck_threshold_minutes=30)
        if stuck_reset > 0:
            logger.warning(f"Reset {stuck_reset} stuck products")

        return {
            "status": "completed",
            "mode": sync_mode,
            "bucket": current_bucket,
            "bucket_type": "minute" if sync_mode == "testing" else "hour",
            "batches_dispatched": batches_dispatched,
            "products_dispatched": products_dispatched,
            "stuck_reset": stuck_reset,
        }

    # ------------------------------------------------------------------
    # Retry dispatch
    # ------------------------------------------------------------------

    def dispatch_retry(self, dispatch_callback) -> Dict[str, Any]:
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

        return {
            "status": "completed",
            "batches_dispatched": batches_dispatched,
            "products_retried": products_dispatched,
        }

    # ------------------------------------------------------------------
    # End-of-day cleanup
    # ------------------------------------------------------------------

    def end_of_day_cleanup(self) -> Dict[str, Any]:
        """Daily cleanup: reset stuck, log stats."""
        logger.info("=== End of Day Cleanup ===")

        stuck_reset = self._store.reset_stuck_products(stuck_threshold_minutes=60)
        status_summary = self._store.get_sync_status_summary()
        distribution = self._store.get_slot_distribution_summary()

        logger.info(f"Daily Stats: {status_summary}")
        logger.info(f"Slot Distribution: efficiency={distribution['efficiency_percent']}%")

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
