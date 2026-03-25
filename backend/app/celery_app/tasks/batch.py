"""
Batch orchestration tasks.

Tasks:
- check_batch_completion: Check if a batch has completed its current stage
- reconcile_batch:        Safety net — detect and record missing parts
- cancel_batch:           Cancel a batch and revoke Celery tasks
- cleanup_stale_batches:  Mark stuck batches as failed after timeout

All business logic lives in BatchCompletionService.
Version: 1.2.0
"""
import logging

from app.celery_app.celery_config import celery_app
from app.celery_app.tasks.base import BaseTask, get_batch_completion_service

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.batch.check_batch_completion"
)
def check_batch_completion(self, batch_id: str):
    """Check if a batch has completed its current stage and update status."""
    logger.info(f"Checking completion for batch {batch_id}")

    svc = get_batch_completion_service()
    result = svc.check_completion(batch_id)

    if result.get("trigger_catchup"):
        # Lazy import avoids circular dependency with sync_dispatch
        from app.celery_app.tasks.sync_dispatch import dispatch_deferred_catchup
        dispatch_deferred_catchup.delay()
        logger.info("Triggered deferred sync catch-up after batch completion")

    return result


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.batch.reconcile_batch"
)
def reconcile_batch(self, batch_id: str):
    """Safety-net reconciliation — detect and record missing/stuck parts."""
    logger.info(f"Reconciling batch {batch_id}")

    svc = get_batch_completion_service()
    result = svc.reconcile(batch_id)

    if result.get("trigger_completion_check"):
        check_batch_completion.delay(batch_id)

    return result


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.batch.cancel_batch"
)
def cancel_batch(self, batch_id: str):
    """Cancel a batch and revoke all in-flight Celery tasks."""
    logger.info(f"Cancelling batch {batch_id}")

    svc = get_batch_completion_service()
    return svc.cancel(batch_id, celery_app=celery_app)


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.batch.cleanup_stale_batches"
)
def cleanup_stale_batches(self, max_age_hours: int = 24):
    """Mark stuck batches as failed after timeout."""
    logger.info(f"Cleaning up stale batches older than {max_age_hours} hours")

    svc = get_batch_completion_service()
    return svc.cleanup_stale(max_age_hours)
