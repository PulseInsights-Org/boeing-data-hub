"""
Publishing tasks — Shopify product create/update with saga pattern.

Tasks:
- publish_batch:   Orchestrates publishing a batch of products
- publish_product: Publishes a single product (delegates to PublishingService)

All publish business logic (4-tier idempotency, image upload, location
mapping, saga compensation) lives in PublishingService.

PublishTask provides a last-resort Celery on_failure safety net to ensure
no product is silently lost even when the in-task error handler itself fails.

Version: 1.1.0
"""
import logging
from typing import List, Optional

import httpx
from fastapi import HTTPException

from app.celery_app.celery_config import celery_app
from app.celery_app.tasks.base import (
    BaseTask,
    run_async,
    get_publishing_service,
    get_staging_store,
    get_batch_store,
)
from app.celery_app.tasks.batch import check_batch_completion, reconcile_batch
from app.core.exceptions import RetryableError, NonRetryableError
from app.db.sync_store import get_sync_store
from app.utils.slot_manager import precompute_slot_assignments

logger = logging.getLogger(__name__)


class PublishTask(BaseTask):
    """Custom base for publish_product — adds last-resort failure recording."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """
        Last-resort handler invoked by Celery AFTER all retries are exhausted
        and the task's own except blocks have run.

        Ensures the part is never silently lost even if the in-task handler
        itself raised (double failure).
        """
        super().on_failure(exc, task_id, args, kwargs, einfo)
        batch_id = args[0] if args and len(args) > 0 else kwargs.get("batch_id")
        part_number = args[1] if args and len(args) > 1 else kwargs.get("part_number")
        user_id = args[2] if args and len(args) > 2 else kwargs.get("user_id", "system")

        if not batch_id or not part_number:
            return

        try:
            _bs = get_batch_store()
            batch = _bs.get_batch(batch_id)
            if batch:
                already_recorded = any(
                    item.get("part_number") == part_number
                    for item in (batch.get("failed_items") or [])
                )
                if not already_recorded:
                    logger.warning(
                        f"on_failure safety net: recording missed failure for "
                        f"{part_number} in batch {batch_id}"
                    )
                    _bs.record_failure(
                        batch_id, part_number,
                        f"Task failed (on_failure safety net): {exc}",
                        stage="publishing",
                    )
            try:
                _ss = get_staging_store()
                run_async(_ss.update_product_staging_status(part_number, "failed", user_id))
            except Exception:
                pass
            check_batch_completion.delay(batch_id)
        except Exception as e:
            logger.critical(
                f"on_failure safety net ALSO failed for {part_number} in "
                f"batch {batch_id}: {e}"
            )


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.publishing.publish_batch",
    max_retries=0  # Orchestrator doesn't retry — child tasks do
)
def publish_batch(self, batch_id: str, part_numbers: List[str], user_id: str = "system"):
    """Orchestrate publishing a batch of products to Shopify.

    Pre-computes sync-schedule slot assignments to eliminate the
    read-compute-write race condition that arises when concurrent
    publish_product workers each independently call get_least_loaded_slot().
    """
    logger.info(
        f"Starting publish batch {batch_id} with {len(part_numbers)} products "
        f"for user {user_id}"
    )
    batch_store = get_batch_store()

    try:
        batch_store.update_status(batch_id, "processing")

        try:
            sync_store = get_sync_store()
            slot_counts = sync_store.get_slot_counts()
            slot_assignments = precompute_slot_assignments(slot_counts, len(part_numbers))
            logger.info(
                f"Pre-computed slot assignments for {len(part_numbers)} products "
                f"(distribution: {slot_counts})"
            )
        except Exception as slot_err:
            logger.warning(
                f"Could not pre-compute slot assignments, "
                f"falling back to per-task allocation: {slot_err}"
            )
            slot_assignments = [None] * len(part_numbers)

        for i, pn in enumerate(part_numbers):
            publish_product.delay(batch_id, pn, user_id, assigned_slot=slot_assignments[i])

        logger.info(f"Queued {len(part_numbers)} publish tasks for batch {batch_id}")

        # Safety-net reconciliation in case any publish task is silently lost.
        # Delay = 3 min per 10 products (min 5 min, max 30 min).
        reconcile_delay = min(max((len(part_numbers) // 10) * 3 * 60, 300), 1800)
        reconcile_batch.apply_async(args=[batch_id], countdown=reconcile_delay)
        logger.info(f"Scheduled reconciliation for batch {batch_id} in {reconcile_delay}s")

        return {"batch_id": batch_id, "products_queued": len(part_numbers)}

    except Exception as e:
        logger.error(f"Publish batch failed: {e}")
        batch_store.update_status(batch_id, "failed", str(e))
        raise


@celery_app.task(
    bind=True,
    base=PublishTask,
    name="tasks.publishing.publish_product",
    autoretry_for=(RetryableError, ConnectionError, TimeoutError, httpx.ConnectError, httpx.ReadTimeout),
    dont_autoretry_for=(NonRetryableError,),
    retry_backoff=True,
    max_retries=3,
    rate_limit="30/m"  # Shopify rate limit
)
def publish_product(
    self,
    batch_id: str,
    part_number: str,
    user_id: str = "system",
    assigned_slot: Optional[int] = None,
):
    """Publish a single product to Shopify.

    Delegates the full publish pipeline (validation, 4-tier idempotency,
    image upload, location mapping, saga compensation, sync schedule) to
    PublishingService. This task only handles Celery-specific concerns:
    fetching the staging record, error routing, and batch accounting.
    """
    logger.info(f"Publishing {part_number} to Shopify for user {user_id}")

    staging_store = None
    batch_store = None

    try:
        staging_store = get_staging_store()
        batch_store = get_batch_store()

        record = run_async(
            staging_store.get_product_staging_by_part_number(part_number, user_id=user_id)
        )
        if not record:
            raise NonRetryableError(
                f"Product {part_number} not found in staging for user {user_id}"
            )

        svc = get_publishing_service()
        result = run_async(
            svc.publish_product_for_batch(record, part_number, user_id, assigned_slot=assigned_slot)
        )

        # DB trigger updates published_count when staging status becomes 'published'
        check_batch_completion.delay(batch_id)

        logger.info(
            f"Published {part_number} -> Shopify ID: {result['shopify_product_id']} "
            f"({result['action']})"
        )
        return {
            "success": True,
            "part_number": part_number,
            "shopify_product_id": result["shopify_product_id"],
            "action": result["action"],
        }

    except NonRetryableError as e:
        try:
            _ss = staging_store or get_staging_store()
            run_async(_ss.update_product_staging_status(part_number, "blocked", user_id))
        except Exception:
            logger.warning(f"Could not update staging status to 'blocked' for {part_number}")
        try:
            _bs = batch_store or get_batch_store()
            _bs.record_failure(batch_id, part_number, str(e), stage="publishing")
            check_batch_completion.delay(batch_id)
        except Exception as inner:
            logger.critical(
                f"CANNOT record failure for {part_number} in batch {batch_id}: {inner}"
            )
        raise

    except HTTPException as e:
        error_msg = f"Shopify API error {e.status_code}: {e.detail}"
        logger.error(f"Failed to publish {part_number}: {error_msg}")

        if 400 <= e.status_code < 500:
            try:
                _ss = staging_store or get_staging_store()
                run_async(_ss.update_product_staging_status(part_number, "blocked", user_id))
            except Exception:
                logger.warning(
                    f"Could not update staging status to 'blocked' for {part_number}"
                )
            try:
                _bs = batch_store or get_batch_store()
                _bs.record_failure(batch_id, part_number, error_msg, stage="publishing")
                check_batch_completion.delay(batch_id)
            except Exception as inner:
                logger.critical(
                    f"CANNOT record failure for {part_number} in batch {batch_id}: {inner}"
                )
            raise NonRetryableError(error_msg)
        else:
            if self.request.retries >= self.max_retries:
                try:
                    _ss = staging_store or get_staging_store()
                    run_async(_ss.update_product_staging_status(part_number, "failed", user_id))
                except Exception:
                    logger.warning(
                        f"Could not update staging status to 'failed' for {part_number}"
                    )
                try:
                    _bs = batch_store or get_batch_store()
                    _bs.record_failure(batch_id, part_number, error_msg, stage="publishing")
                    check_batch_completion.delay(batch_id)
                except Exception as inner:
                    logger.critical(
                        f"CANNOT record failure for {part_number} in batch {batch_id}: {inner}"
                    )
            raise RetryableError(error_msg)

    except Exception as e:
        logger.error(f"Failed to publish {part_number}: {e}")

        is_retryable = isinstance(
            e, (RetryableError, ConnectionError, TimeoutError,
                httpx.ConnectError, httpx.ReadTimeout)
        )
        is_last_attempt = self.request.retries >= self.max_retries

        if not is_retryable or is_last_attempt:
            try:
                _ss = staging_store or get_staging_store()
                run_async(_ss.update_product_staging_status(part_number, "failed", user_id))
            except Exception:
                logger.warning(
                    f"Could not update staging status to 'failed' for {part_number}"
                )
            try:
                _bs = batch_store or get_batch_store()
                _bs.record_failure(batch_id, part_number, str(e), stage="publishing")
                check_batch_completion.delay(batch_id)
            except Exception as inner:
                logger.critical(
                    f"CANNOT record failure for {part_number} in batch {batch_id}: {inner}"
                )
        raise
