"""
Report generation task — generates and emails sync cycle reports via Celery.
Version: 1.0.0
"""
import logging
from typing import Optional

from app.celery_app.celery_config import celery_app
from app.celery_app.tasks.base import BaseTask
from app.core.config import settings
from app.container import get_report_service

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.report_generation.generate_cycle_report",
    max_retries=1,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def generate_cycle_report(self, cycle_id: Optional[str] = None):
    """Generate a sync cycle report using Gemini LLM and send via email.

    This task is triggered automatically when a sync cycle completes
    (all buckets dispatched) or manually via the /reports/generate endpoint.

    Args:
        cycle_id: Optional cycle identifier. If None, uses current cycle.
    """
    logger.info(f"Report generation task started, cycle_id={cycle_id}")

    try:
        if not settings.gemini_api_key:
            logger.warning("GEMINI_API_KEY not configured, skipping report generation")
            return {"status": "skipped", "reason": "no_gemini_api_key"}

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
