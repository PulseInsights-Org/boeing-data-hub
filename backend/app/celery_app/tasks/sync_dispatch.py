"""
Sync dispatch tasks — hourly scheduler, retry, and end-of-day cleanup.

Tasks:
- dispatch_hourly:          Main sync dispatcher with 3-layer dedup + conflict guard
- dispatch_deferred_catchup: Immediate catch-up after extraction session completes
- dispatch_retry:           Retry failed products (every 4 hours)
- end_of_day_cleanup:       Daily cleanup at midnight UTC

All slot-distribution and dispatch business logic lives in SyncDispatchService.
This file handles only Celery-specific concerns: beat scheduling, Redis locking,
deferred bucket tracking, and task queuing.

Version: 1.2.0
"""
import logging
from datetime import datetime, timezone

from app.celery_app.celery_config import celery_app, SYNC_MODE, SYNC_TEST_BUCKET_COUNT, SYNC_ENABLED
from app.celery_app.tasks.base import BaseTask, get_batch_store, get_sync_dispatch_service
from app.celery_app.tasks.sync_boeing import process_boeing_batch
from app.utils.schedule_helpers import get_current_hour_utc
from app.utils.cycle_tracker import record_bucket_dispatched
from app.celery_app.tasks.report_generation import wait_for_cycle_completion
from app.utils.dispatch_lock import (
    acquire_dispatch_lock,
    release_dispatch_lock,
    compute_window_start,
    record_deferred_bucket,
    get_deferred_buckets,
    clear_deferred_buckets,
    acquire_catchup_lock,
    release_catchup_lock,
)

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.sync_dispatch.dispatch_hourly",
    max_retries=0
)
def dispatch_hourly(self):
    """
    Main sync dispatcher with three-layer deduplication + conflict guard.

    Layer 1: Redis dispatch lock — only ONE dispatch_hourly per bucket window.
    Layer 2: DB window_start filter — handled inside SyncDispatchService.dispatch_bucket.
    Layer 3: Redis SKU dedup set  — handled inside SyncDispatchService.dispatch_bucket.

    CONFLICT GUARD: If an extraction/publish session is active, the current
    bucket is deferred (Redis) and sync is skipped. Deferred buckets are
    processed via two paths:
    - Active path:  dispatch_deferred_catchup fires immediately after extraction.
    - Passive path: this task checks for deferred buckets on the next beat run.
    """
    if not SYNC_ENABLED:
        logger.info("Auto-sync is disabled (SYNC_ENABLED=false), skipping dispatch")
        return {"status": "skipped", "reason": "sync_disabled"}

    now = datetime.now(timezone.utc)
    svc = get_sync_dispatch_service()

    if SYNC_MODE == "testing":
        current_bucket = now.minute // 10
        logger.info("=" * 60)
        logger.info("[TESTING MODE] Sync Dispatch Started")
        logger.info(f"   Minute bucket: {current_bucket} (of {SYNC_TEST_BUCKET_COUNT})")
        logger.info(f"   Current time: {now.strftime('%H:%M:%S')} UTC")
        logger.info("=" * 60)
    else:
        current_bucket = get_current_hour_utc()
        if now.minute < 45:
            logger.debug(f"Not in sync window (minute={now.minute}), skipping")
            return {"status": "skipped", "reason": "not_in_sync_window"}
        logger.info("=" * 60)
        logger.info("[PRODUCTION MODE] Sync Dispatch Started")
        logger.info(f"   Hour bucket: {current_bucket} (of 24)")
        logger.info(f"   Current time: {now.strftime('%H:%M:%S')} UTC")
        logger.info("=" * 60)

    # ── Conflict guard: defer if extraction/publish session is active ──
    if svc.is_extraction_session_active(get_batch_store()):
        record_deferred_bucket(current_bucket)
        logger.warning(
            f"EXTRACTION/PUBLISH IN PROGRESS — deferring bucket {current_bucket}. "
            "Sync will resume after session completes."
        )
        return {
            "status": "deferred",
            "reason": "extraction_in_progress",
            "deferred_bucket": current_bucket,
        }

    # ── Layer 1: Dispatch idempotency lock ────────────────────────────
    task_id = self.request.id or "unknown"
    if not acquire_dispatch_lock(current_bucket, task_id):
        logger.info(f"Dispatch already running for bucket {current_bucket}, skipping")
        return {"status": "skipped", "reason": "already_dispatched", "bucket": current_bucket}

    window_start = compute_window_start()
    logger.info(
        f"Window start: {window_start.isoformat()} "
        f"(products synced after this are excluded)"
    )

    try:
        # ── Passive path: catch up deferred buckets (safety net) ──────
        deferred_buckets = get_deferred_buckets()
        deferred_stats = []
        if deferred_buckets:
            if acquire_catchup_lock(task_id):
                try:
                    logger.info(
                        f"Passive catch-up: processing {len(deferred_buckets)} "
                        f"deferred bucket(s): {sorted(deferred_buckets)}"
                    )
                    for bucket in sorted(deferred_buckets):
                        bucket_stats = svc.dispatch_bucket(
                            bucket, window_start, process_boeing_batch.delay
                        )
                        deferred_stats.append(bucket_stats)
                        try:
                            record_bucket_dispatched(bucket)
                        except Exception as tracker_err:
                            logger.warning(
                                f"Cycle tracker error for deferred bucket {bucket}: {tracker_err}"
                            )
                    clear_deferred_buckets()
                finally:
                    release_catchup_lock()
            else:
                logger.info("Active catch-up already running, skipping deferred processing")

        # ── Process current bucket ────────────────────────────────────
        current_stats = svc.dispatch_bucket(current_bucket, window_start, process_boeing_batch.delay)

        stuck_reset = svc._store.reset_stuck_products(stuck_threshold_minutes=30)
        if stuck_reset > 0:
            logger.warning(f"Reset {stuck_reset} stuck products")

        # Record bucket and auto-trigger report on cycle completion
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
            "batches_dispatched": current_stats["batches_dispatched"],
            "products_dispatched": current_stats["products_dispatched"],
            "skus_deduped": current_stats["skus_deduped"],
            "stuck_reset": stuck_reset,
            "cycle_complete": cycle_complete,
            "deferred_catchup": deferred_stats if deferred_stats else None,
        }

    except Exception as e:
        logger.error(f"Sync dispatch failed (mode={SYNC_MODE}): {e}")
        raise

    finally:
        release_dispatch_lock(current_bucket)


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.sync_dispatch.dispatch_deferred_catchup",
    max_retries=0
)
def dispatch_deferred_catchup(self):
    """Active catch-up path — triggered when the last extraction batch completes.

    Fires immediately so deferred buckets are processed within seconds of
    extraction completing, without waiting for the next beat run.
    Uses a shared Redis lock to prevent overlap with the passive path.
    """
    if not SYNC_ENABLED:
        return {"status": "skipped", "reason": "sync_disabled"}

    svc = get_sync_dispatch_service()

    if svc.is_extraction_session_active(get_batch_store()):
        logger.info("Another extraction session active, deferring catch-up")
        return {"status": "deferred", "reason": "new_extraction_in_progress"}

    deferred_buckets = get_deferred_buckets()
    if not deferred_buckets:
        logger.info("No deferred buckets to catch up")
        return {"status": "skipped", "reason": "no_deferred_buckets"}

    task_id = self.request.id or "catchup"
    if not acquire_catchup_lock(task_id):
        logger.info("Catch-up lock held (dispatch_hourly is handling it), skipping")
        return {"status": "skipped", "reason": "catchup_lock_held"}

    logger.info(
        f"Active catch-up: processing {len(deferred_buckets)} "
        f"deferred bucket(s): {sorted(deferred_buckets)}"
    )

    window_start = compute_window_start()
    deferred_stats = []

    try:
        cycle_complete = False
        for bucket in sorted(deferred_buckets):
            bucket_stats = svc.dispatch_bucket(bucket, window_start, process_boeing_batch.delay)
            deferred_stats.append(bucket_stats)
            try:
                if record_bucket_dispatched(bucket):
                    cycle_complete = True
            except Exception as tracker_err:
                logger.warning(
                    f"Cycle tracker error for deferred bucket {bucket}: {tracker_err}"
                )

        clear_deferred_buckets()

        total_products = sum(s["products_dispatched"] for s in deferred_stats)
        total_batches = sum(s["batches_dispatched"] for s in deferred_stats)
        logger.info(
            f"Active catch-up complete: {len(deferred_buckets)} buckets, "
            f"{total_batches} batches, {total_products} products dispatched"
        )

        if cycle_complete:
            wait_for_cycle_completion.delay()
            logger.info("Sync cycle complete after catch-up — triggering report")

        return {
            "status": "completed",
            "deferred_buckets": sorted(deferred_buckets),
            "total_batches": total_batches,
            "total_products": total_products,
            "bucket_stats": deferred_stats,
            "cycle_complete": cycle_complete,
        }

    except Exception as e:
        logger.error(f"Deferred catch-up failed: {e}")
        raise

    finally:
        release_catchup_lock()


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.sync_dispatch.dispatch_retry",
    max_retries=0
)
def dispatch_retry(self):
    """Retry dispatcher for failed products. Called every 4 hours by Celery Beat."""
    if not SYNC_ENABLED:
        logger.info("Auto-sync is disabled (SYNC_ENABLED=false), skipping retry dispatch")
        return {"status": "skipped", "reason": "sync_disabled"}

    svc = get_sync_dispatch_service()

    if svc.is_extraction_session_active(get_batch_store()):
        logger.warning(
            "EXTRACTION/PUBLISH IN PROGRESS — deferring retry dispatch. "
            "Retries will run on next schedule."
        )
        return {"status": "deferred", "reason": "extraction_in_progress"}

    logger.info("=== Retry Sync Dispatch ===")
    return svc.dispatch_retry(process_boeing_batch.delay)


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.sync_dispatch.end_of_day_cleanup",
    max_retries=0
)
def end_of_day_cleanup(self):
    """Daily cleanup task at midnight UTC — resets stuck products and logs stats."""
    if not SYNC_ENABLED:
        logger.info("Auto-sync is disabled (SYNC_ENABLED=false), skipping cleanup")
        return {"status": "skipped", "reason": "sync_disabled"}

    svc = get_sync_dispatch_service()
    return svc.end_of_day_cleanup()
