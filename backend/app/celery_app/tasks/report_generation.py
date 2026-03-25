"""
Report generation tasks — sync cycle notifications and reports via Celery.

Tasks:
- send_cycle_start_notification: Sends 'Sync Cycle Started' email on first bucket dispatch.
- wait_for_cycle_completion: Polls DB until all products finish, then triggers report.
- generate_cycle_report: Builds dashboard HTML report and sends via email.

Version: 2.0.0
"""
import logging
from typing import Optional

from app.celery_app.celery_config import celery_app
from app.celery_app.tasks.base import BaseTask

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.report_generation.send_cycle_start_notification",
    max_retries=2,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def send_cycle_start_notification(self):
    """Send a 'Sync Cycle Started' email notification.

    Triggered by dispatch_hourly when the first bucket of a new cycle
    is dispatched. Idempotency is guaranteed by record_cycle_start()
    in the caller — this task only fires once per cycle.
    """
    logger.info("Sending cycle start notification")

    try:
        from app.container import get_report_service
        service = get_report_service()
        result = service.generate_cycle_start_report()

        logger.info(
            f"Cycle start notification sent: report_id={result['report_id']}, "
            f"email_sent={result['email_sent']}"
        )
        return {
            "status": "completed",
            "report_id": result["report_id"],
            "email_sent": result["email_sent"],
        }

    except Exception as e:
        logger.error(f"Cycle start notification failed: {e}")
        raise


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.report_generation.wait_for_cycle_completion",
    max_retries=60,
)
def wait_for_cycle_completion(self, cycle_id: Optional[str] = None):
    """Wait for all products to finish processing, then generate the report.

    After all buckets are dispatched, products may still be in 'syncing' state
    (Boeing API calls or Shopify updates in flight). This task polls the DB
    every 30 seconds until no products are syncing, then triggers report generation.

    Timeout safety: max_retries=60 x 30s = 30 minutes. If products are still
    syncing after 30 minutes, the report is generated anyway with a warning
    banner showing how many products were still in-flight. This ensures
    stakeholders always receive a report rather than silent failure.

    Args:
        cycle_id: Optional cycle identifier. If None, uses current cycle.
    """
    from app.db.sync_store import get_sync_store
    sync_store = get_sync_store()
    syncing_count = sync_store.get_syncing_count()

    if syncing_count > 0:
        is_final_attempt = self.request.retries >= self.max_retries - 1

        if is_final_attempt:
            logger.warning(
                f"Timeout reached with {syncing_count} products still syncing. "
                f"Generating report with incomplete data."
            )
            generate_cycle_report.delay(cycle_id, still_syncing=syncing_count)
            return {
                "status": "timeout_fallback",
                "cycle_id": cycle_id,
                "still_syncing": syncing_count,
            }

        logger.info(
            f"Still {syncing_count} products syncing, "
            f"retrying in 30s (attempt {self.request.retries + 1}/{self.max_retries})"
        )
        raise self.retry(countdown=30)

    logger.info("All products completed — triggering report generation")
    generate_cycle_report.delay(cycle_id)

    return {"status": "forwarded_to_report", "cycle_id": cycle_id}


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.report_generation.generate_cycle_report",
    max_retries=1,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def generate_cycle_report(
    self,
    cycle_id: Optional[str] = None,
    still_syncing: int = 0,
):
    """Generate a dashboard-style sync cycle report and send via email.

    This task is triggered by wait_for_cycle_completion after all products
    reach a terminal state, or manually via the /reports/generate endpoint.

    Args:
        cycle_id: Optional cycle identifier. If None, uses current cycle.
        still_syncing: Number of products still syncing (0 = normal completion,
                       >0 = timeout fallback with incomplete data).
    """
    logger.info(
        f"Report generation task started, cycle_id={cycle_id}, "
        f"still_syncing={still_syncing}"
    )

    try:
        from app.container import get_report_service
        service = get_report_service()
        result = service.generate_cycle_report(
            cycle_id=cycle_id,
            still_syncing=still_syncing,
        )

        logger.info(
            f"Report generation complete: report_id={result['report_id']}, "
            f"email_sent={result['email_sent']}"
        )

        return {
            "status": "completed",
            "report_id": result["report_id"],
            "cycle_id": result["cycle_id"],
            "email_sent": result["email_sent"],
            "still_syncing": still_syncing,
        }

    except Exception as e:
        logger.error(f"Report generation failed: {e}")
        raise
