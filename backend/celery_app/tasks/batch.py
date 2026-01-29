"""
Batch orchestration tasks.

Tasks:
- check_batch_completion: Check if a batch has completed
- cancel_batch: Cancel a batch and revoke tasks
- cleanup_stale_batches: Mark stuck batches as failed
"""
import logging
from datetime import datetime, timedelta

from celery_app.celery_config import celery_app
from celery_app.tasks.base import BaseTask, get_batch_store

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="celery_app.tasks.batch.check_batch_completion"
)
def check_batch_completion(self, batch_id: str):
    """
    Check if a batch has completed its current stage and update status.

    Pipeline stages (single batch progresses through all):
    1. "search" → extraction & normalization in progress
    2. "normalized" → normalization complete, ready for publishing
    3. "publishing" → publishing in progress
    4. "completed" → all publishing done

    A stage is complete when:
    - Search stage: (normalized_count + failed_count) >= total_items → becomes "normalized"
    - Publishing stage: (published_count + failed_count) >= total_items → becomes "completed"

    Args:
        batch_id: Batch identifier to check

    Returns:
        dict: Completion status
    """
    logger.info(f"Checking completion for batch {batch_id}")

    batch_store = get_batch_store()
    batch = batch_store.get_batch(batch_id)

    if not batch:
        logger.warning(f"Batch {batch_id} not found")
        return {"batch_id": batch_id, "error": "not_found"}

    # Skip if already finalized
    if batch["status"] in ("completed", "failed", "cancelled"):
        return {
            "batch_id": batch_id,
            "status": batch["status"],
            "already_finalized": True
        }

    total = batch["total_items"]
    normalized = batch["normalized_count"]
    published = batch["published_count"]
    failed = batch["failed_count"]
    batch_type = batch["batch_type"]

    is_stage_complete = False

    # Check if current stage is complete based on batch_type
    if batch_type == "search":
        # Search stage: check if normalization is complete
        is_stage_complete = (normalized + failed) >= total
    elif batch_type == "publishing":
        # Publishing stage: check if all items are published
        is_stage_complete = (published + failed) >= total
    elif batch_type == "normalized":
        # Normalized stage: waiting for user to trigger publish
        # This stage doesn't auto-complete
        return {
            "batch_id": batch_id,
            "status": "processing",
            "batch_type": "normalized",
            "message": "Normalization complete. Ready for publishing."
        }

    if is_stage_complete:
        if batch_type == "search":
            # Search/normalization stage complete
            # Check for complete failure
            if normalized == 0:
                batch_store.update_status(batch_id, "failed", "All items failed during extraction/normalization")
                logger.warning(f"Batch {batch_id} failed (0 items normalized)")
                return {
                    "batch_id": batch_id,
                    "status": "failed",
                    "batch_type": "search",
                    "total": total,
                    "succeeded": 0,
                    "failed": failed
                }

            # Transition to "normalized" stage (not "completed")
            # User can then trigger publishing
            batch_store.update_batch_type(batch_id, "normalized")
            batch_store.update_status(batch_id, "completed")  # Status is completed for this stage
            logger.info(f"Batch {batch_id} normalization complete ({normalized}/{total} items). Ready for publishing.")

            return {
                "batch_id": batch_id,
                "status": "completed",
                "batch_type": "normalized",
                "total": total,
                "succeeded": normalized,
                "failed": failed
            }

        elif batch_type == "publishing":
            # Publishing stage complete
            succeeded = published

            if failed == total or succeeded == 0:
                # All items failed
                batch_store.update_status(batch_id, "failed", f"Publishing failed ({failed} failures)")
                logger.warning(f"Batch {batch_id} publishing failed ({failed} failures)")
                return {
                    "batch_id": batch_id,
                    "status": "failed",
                    "batch_type": "publishing",
                    "total": total,
                    "succeeded": succeeded,
                    "failed": failed
                }
            elif failed > 0:
                # Partial success
                batch_store.update_status(batch_id, "completed")
                logger.warning(f"Batch {batch_id} publishing completed with {failed} failures")
            else:
                # All succeeded
                batch_store.update_status(batch_id, "completed")
                logger.info(f"Batch {batch_id} publishing completed successfully ({succeeded} items)")

            return {
                "batch_id": batch_id,
                "status": "completed",
                "batch_type": "publishing",
                "total": total,
                "succeeded": succeeded,
                "failed": failed
            }

    return {
        "batch_id": batch_id,
        "status": "processing",
        "batch_type": batch_type,
        "progress": {
            "total": total,
            "normalized": normalized,
            "published": published,
            "failed": failed
        }
    }


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="celery_app.tasks.batch.cancel_batch"
)
def cancel_batch(self, batch_id: str):
    """
    Cancel a batch and revoke all in-flight Celery tasks.

    This attempts to terminate any tasks currently running for this batch.
    Note: Tasks already completed cannot be undone.

    Args:
        batch_id: Batch identifier to cancel

    Returns:
        dict: Cancellation result
    """
    logger.info(f"Cancelling batch {batch_id}")

    batch_store = get_batch_store()
    batch = batch_store.get_batch(batch_id)

    if not batch:
        logger.warning(f"Batch {batch_id} not found for cancellation")
        return {"success": False, "error": "Batch not found"}

    if batch["status"] in ("completed", "failed", "cancelled"):
        logger.info(f"Batch {batch_id} already finalized: {batch['status']}")
        return {"success": False, "error": f"Batch already {batch['status']}"}

    # Revoke the main orchestrator task if it exists
    celery_task_id = batch.get("celery_task_id")
    if celery_task_id:
        try:
            # Revoke with terminate=True to kill running tasks
            celery_app.control.revoke(celery_task_id, terminate=True, signal='SIGTERM')
            logger.info(f"Revoked main task {celery_task_id} for batch {batch_id}")
        except Exception as e:
            logger.warning(f"Failed to revoke task {celery_task_id}: {e}")

    # Note: Child tasks (extract_chunk, normalize_chunk, etc.) will continue
    # but their results will be ignored since batch is cancelled.
    # For full cancellation, you'd need to track all child task IDs.

    batch_store.update_status(batch_id, "cancelled", "Cancelled by user request")
    logger.info(f"Batch {batch_id} marked as cancelled")

    return {
        "success": True,
        "batch_id": batch_id,
        "status": "cancelled"
    }


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="celery_app.tasks.batch.cleanup_stale_batches"
)
def cleanup_stale_batches(self, max_age_hours: int = 24):
    """
    Mark stuck batches as failed after timeout.

    Run this periodically (e.g., via Celery Beat) to clean up
    batches that got stuck due to worker crashes or other issues.

    Args:
        max_age_hours: Maximum age in hours before marking as failed

    Returns:
        dict: Cleanup results
    """
    logger.info(f"Cleaning up stale batches older than {max_age_hours} hours")

    batch_store = get_batch_store()
    active_batches = batch_store.get_active_batches()
    cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)

    cleaned_count = 0

    for batch in active_batches:
        created_at_str = batch["created_at"]
        # Handle different timestamp formats
        if isinstance(created_at_str, str):
            created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        else:
            created_at = created_at_str

        if created_at.replace(tzinfo=None) < cutoff:
            batch_store.update_status(
                batch["id"],
                "failed",
                f"Timed out after {max_age_hours} hours"
            )
            logger.warning(f"Marked batch {batch['id']} as failed (timed out)")
            cleaned_count += 1

    return {
        "batches_checked": len(active_batches),
        "batches_cleaned": cleaned_count
    }
