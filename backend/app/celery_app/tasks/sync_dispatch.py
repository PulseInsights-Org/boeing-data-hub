"""
Sync dispatch tasks — hourly scheduler, retry, and end-of-day cleanup.

Sync dispatch tasks.

Tasks:
- dispatch_hourly: Runs periodically, dispatches batches for current time bucket
- dispatch_retry: Runs every 4 hours, retries failed products
- end_of_day_cleanup: Daily cleanup at midnight UTC
Version: 1.0.0
"""
import logging
from datetime import datetime, timezone
from typing import Dict, List

from app.celery_app.celery_config import celery_app, SYNC_MODE, SYNC_TEST_BUCKET_COUNT, SYNC_ENABLED
from app.celery_app.tasks.base import BaseTask
from app.celery_app.tasks.sync_boeing import process_boeing_batch
from app.db.sync_store import get_sync_store
from app.utils.slot_manager import get_slot_distribution, MAX_SKUS_PER_API_CALL
from app.utils.batch_grouping import calculate_batch_groups
from app.utils.schedule_helpers import get_current_hour_utc
from app.utils.cycle_tracker import record_bucket_dispatched
from app.celery_app.tasks.report_generation import wait_for_cycle_completion
from app.utils.dispatch_lock import (
    acquire_dispatch_lock,
    release_dispatch_lock,
    record_dispatched_skus,
    get_already_dispatched_skus,
    compute_window_start,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = MAX_SKUS_PER_API_CALL  # 10 SKUs per Boeing API call
MIN_PRODUCTS_FOR_ACTIVE_SLOT = 10


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.sync_dispatch.dispatch_hourly",
    max_retries=0
)
def dispatch_hourly(self):
    """
    Main sync dispatcher with three-layer deduplication.

    Layer 1: Redis dispatch lock — only ONE dispatch_hourly per bucket window.
    Layer 2: DB window_start filter — exclude products already synced this window.
    Layer 3: Redis SKU dedup set — subtract already-dispatched SKUs.

    PRODUCTION: Called at :45 of each hour, uses hour buckets (0-23).
    TESTING: Called every 10 minutes, uses minute buckets (0-5).
    """
    if not SYNC_ENABLED:
        logger.info("Auto-sync is disabled (SYNC_ENABLED=false), skipping dispatch")
        return {"status": "skipped", "reason": "sync_disabled"}

    now = datetime.now(timezone.utc)

    if SYNC_MODE == "testing":
        current_bucket = now.minute // 10
        logger.info("=" * 60)
        logger.info(f"[TESTING MODE] Sync Dispatch Started")
        logger.info(f"   Minute bucket: {current_bucket} (of {SYNC_TEST_BUCKET_COUNT})")
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

    # ── Layer 1: Dispatch idempotency lock ──────────────────────────────
    task_id = self.request.id or "unknown"
    if not acquire_dispatch_lock(current_bucket, task_id):
        logger.info(f"Dispatch already running for bucket {current_bucket}, skipping")
        return {"status": "skipped", "reason": "already_dispatched", "bucket": current_bucket}

    # Compute the start of the current bucket window for the DB cooldown filter
    window_start = compute_window_start()
    logger.info(f"Window start: {window_start.isoformat()} (products synced after this are excluded)")

    sync_store = get_sync_store()

    try:
        slot_counts = sync_store.get_slot_counts()
        distribution = get_slot_distribution(slot_counts)

        logger.info(
            f"Slot distribution: {distribution['active_count']} active, "
            f"{distribution['filling_count']} filling, "
            f"{distribution['dormant_count']} dormant"
        )

        batches_dispatched = 0
        products_dispatched = 0
        skus_deduped = 0

        current_count = slot_counts.get(current_bucket, 0)

        if current_count >= MIN_PRODUCTS_FOR_ACTIVE_SLOT:
            # ACTIVE SLOT: Process normally
            # ── Layer 2: DB query with window_start cooldown ────────────
            products = sync_store.get_products_for_hour(
                current_bucket,
                status_filter=["pending", "success"],
                window_start=window_start,
            )

            # ── Layer 3: Redis SKU dedup — subtract already-dispatched ──
            already_dispatched = get_already_dispatched_skus(current_bucket)
            if already_dispatched:
                before_count = len(products)
                products = [p for p in products if p["sku"] not in already_dispatched]
                skus_deduped = before_count - len(products)
                if skus_deduped > 0:
                    logger.info(f"SKU dedup: filtered {skus_deduped} already-dispatched SKUs")

            if products:
                batches = calculate_batch_groups(products, BATCH_SIZE)
                all_dispatched_skus = []

                for batch in batches:
                    skus = [p["sku"] for p in batch]
                    user_id = batch[0].get("user_id", "system")

                    sync_store.mark_products_syncing(skus)
                    process_boeing_batch.delay(skus, user_id, current_bucket)

                    all_dispatched_skus.extend(skus)
                    batches_dispatched += 1
                    products_dispatched += len(skus)

                # Record dispatched SKUs in Redis for future dedup
                record_dispatched_skus(current_bucket, all_dispatched_skus)

                logger.info(
                    f"Active slot {current_bucket}: dispatched {batches_dispatched} batches, "
                    f"{products_dispatched} products"
                )

        elif current_count > 0:
            # FILLING SLOT: Aggregate with other filling slots
            logger.info(f"Filling slot {current_bucket} ({current_count} products)")

            all_filling_products = []
            for slot in distribution['filling_slots']:
                slot_products = sync_store.get_products_for_hour(
                    slot,
                    status_filter=["pending", "success"],
                    window_start=window_start,
                )
                all_filling_products.extend(slot_products)

            # ── Layer 3: Redis SKU dedup for filling slots ──────────────
            already_dispatched = get_already_dispatched_skus(current_bucket)
            if already_dispatched:
                before_count = len(all_filling_products)
                all_filling_products = [p for p in all_filling_products if p["sku"] not in already_dispatched]
                skus_deduped = before_count - len(all_filling_products)
                if skus_deduped > 0:
                    logger.info(f"SKU dedup (filling): filtered {skus_deduped} already-dispatched SKUs")

            if all_filling_products:
                batches = calculate_batch_groups(all_filling_products, BATCH_SIZE)
                all_dispatched_skus = []

                for batch in batches:
                    skus = [p["sku"] for p in batch]
                    user_id = batch[0].get("user_id", "system")

                    sync_store.mark_products_syncing(skus)
                    process_boeing_batch.delay(skus, user_id, current_bucket)

                    all_dispatched_skus.extend(skus)
                    batches_dispatched += 1
                    products_dispatched += len(skus)

                record_dispatched_skus(current_bucket, all_dispatched_skus)

                logger.info(
                    f"Filling slots aggregated: {len(distribution['filling_slots'])} slots, "
                    f"{batches_dispatched} batches, {products_dispatched} products"
                )

        else:
            logger.debug(f"Dormant slot {current_bucket}, no products to sync")

        stuck_reset = sync_store.reset_stuck_products(stuck_threshold_minutes=30)
        if stuck_reset > 0:
            logger.warning(f"Reset {stuck_reset} stuck products")

        # Record this bucket in the cycle tracker and auto-trigger report on completion
        cycle_complete = False
        try:
            cycle_complete = record_bucket_dispatched(current_bucket)
            if cycle_complete:
                wait_for_cycle_completion.delay()
                logger.info("Sync cycle complete — waiting for products to finish before report")
        except Exception as tracker_err:
            logger.warning(f"Cycle tracker error (non-fatal): {tracker_err}")

        return {
            "status": "completed",
            "mode": SYNC_MODE,
            "bucket": current_bucket,
            "bucket_type": "minute" if SYNC_MODE == "testing" else "hour",
            "batches_dispatched": batches_dispatched,
            "products_dispatched": products_dispatched,
            "skus_deduped": skus_deduped,
            "stuck_reset": stuck_reset,
            "cycle_complete": cycle_complete,
        }

    except Exception as e:
        logger.error(f"Sync dispatch failed (mode={SYNC_MODE}): {e}")
        raise

    finally:
        release_dispatch_lock(current_bucket)


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.sync_dispatch.dispatch_retry",
    max_retries=0
)
def dispatch_retry(self):
    """
    Retry dispatcher for failed products.
    Called every 4 hours by Celery Beat.
    """
    if not SYNC_ENABLED:
        logger.info("Auto-sync is disabled (SYNC_ENABLED=false), skipping retry dispatch")
        return {"status": "skipped", "reason": "sync_disabled"}

    logger.info("=== Retry Sync Dispatch ===")

    sync_store = get_sync_store()

    try:
        failed_products = sync_store.get_failed_products_for_retry(limit=200)

        if not failed_products:
            logger.info("No failed products to retry")
            return {"status": "completed", "retries": 0}

        logger.info(f"Found {len(failed_products)} products for retry")

        user_batches: Dict[str, List[Dict]] = {}
        for product in failed_products:
            user_id = product.get("user_id", "system")
            if user_id not in user_batches:
                user_batches[user_id] = []
            user_batches[user_id].append(product)

        batches_dispatched = 0
        products_dispatched = 0

        for user_id, products in user_batches.items():
            batches = calculate_batch_groups(products, BATCH_SIZE)

            for batch in batches:
                skus = [p["sku"] for p in batch]
                sync_store.mark_products_syncing(skus)
                process_boeing_batch.delay(skus, user_id, -1)  # -1 indicates retry

                batches_dispatched += 1
                products_dispatched += len(skus)

        logger.info(
            f"Retry dispatch: {batches_dispatched} batches, {products_dispatched} products"
        )

        return {
            "status": "completed",
            "batches_dispatched": batches_dispatched,
            "products_retried": products_dispatched,
        }

    except Exception as e:
        logger.error(f"Retry sync dispatch failed: {e}")
        raise


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.sync_dispatch.end_of_day_cleanup",
    max_retries=0
)
def end_of_day_cleanup(self):
    """
    Daily cleanup task at midnight UTC.

    Resets stuck products, logs daily statistics.
    """
    if not SYNC_ENABLED:
        logger.info("Auto-sync is disabled (SYNC_ENABLED=false), skipping cleanup")
        return {"status": "skipped", "reason": "sync_disabled"}

    logger.info("=== End of Day Cleanup ===")

    sync_store = get_sync_store()

    try:
        stuck_reset = sync_store.reset_stuck_products(stuck_threshold_minutes=60)

        status_summary = sync_store.get_sync_status_summary()
        distribution = sync_store.get_slot_distribution_summary()

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

    except Exception as e:
        logger.error(f"End of day cleanup failed: {e}")
        raise
