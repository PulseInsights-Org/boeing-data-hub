"""
Report generation task — generates and emails sync cycle reports via Celery.

Tasks:
- wait_for_cycle_completion: Polls DB until all products finish, then triggers report.
- generate_cycle_report: Builds dashboard HTML report and sends via email.
Version: 1.1.0
"""
import logging
from typing import Optional

from app.celery_app.celery_config import celery_app
from app.celery_app.tasks.base import BaseTask
from app.container import get_report_service
from app.db.sync_store import get_sync_store

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.report_generation.wait_for_cycle_completion",
    max_retries=20,
)
def wait_for_cycle_completion(self, cycle_id: Optional[str] = None):
    """Wait for all products to finish processing, then generate the report.

    After all buckets are dispatched, products may still be in 'syncing' state
    (Boeing API calls or Shopify updates in flight). This task polls the DB
    every 30 seconds until no products are syncing, then triggers report generation.

    Args:
        cycle_id: Optional cycle identifier. If None, uses current cycle.
    """
    sync_store = get_sync_store()
    syncing_count = sync_store.get_syncing_count()

    if syncing_count > 0:
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
def generate_cycle_report(self, cycle_id: Optional[str] = None):
    """Generate a dashboard-style sync cycle report and send via email.

    This task is triggered by wait_for_cycle_completion after all products
    reach a terminal state, or manually via the /reports/generate endpoint.

    Args:
        cycle_id: Optional cycle identifier. If None, uses current cycle.
    """
    logger.info(f"Report generation task started, cycle_id={cycle_id}")

    try:
        service = get_report_service()
        result = service.generate_cycle_report(cycle_id=cycle_id)

        logger.info(
            f"Report generation complete: report_id={result['report_id']}, "
            f"email_sent={result['email_sent']}"
        )

        return {
            "status": "completed",
            "report_id": result["report_id"],
            "cycle_id": result["cycle_id"],
            "email_sent": result["email_sent"],
        }

    except Exception as e:
        logger.error(f"Report generation failed: {e}")
        raise
