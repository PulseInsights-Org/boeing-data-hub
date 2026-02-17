"""
Report routes — sync report generation, retrieval, and cycle progress.
Version: 1.0.0
"""
import logging

from fastapi import APIRouter, HTTPException, Depends

from app.schemas.reports import (
    ReportGenerateRequest,
    ReportGenerateResponse,
    ReportResponse,
    CycleProgressResponse,
)
from app.core.auth import get_current_user
from app.core.config import settings
from app.utils.cycle_tracker import get_cycle_progress
from app.container import get_report_store
from app.celery_app.tasks.report_generation import generate_cycle_report

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reports", tags=["reports"])


@router.post("/generate", response_model=ReportGenerateResponse)
async def generate_report(
    body: ReportGenerateRequest = ReportGenerateRequest(),
    current_user: dict = Depends(get_current_user),
):
    """Manually trigger sync report generation.

    Queues a Celery task that:
    1. Fetches all sync data from product_sync_schedule
    2. Generates a report via Google Gemini LLM
    3. Saves the report and emails it to configured recipients
    """
    try:
        generate_cycle_report.delay(cycle_id=body.cycle_id)

        return ReportGenerateResponse(
            status="queued",
            message="Report generation task queued. Check /reports/latest for results.",
            cycle_id=body.cycle_id,
        )
    except Exception as e:
        logger.error(f"Error queuing report generation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/latest", response_model=ReportResponse)
async def get_latest_report(
    current_user: dict = Depends(get_current_user),
):
    """Get the most recently generated sync report."""
    try:
        store = get_report_store()
        report = store.get_latest_report()

        if not report:
            raise HTTPException(status_code=404, detail="No reports found")

        return ReportResponse(
            id=str(report["id"]),
            cycle_id=report["cycle_id"],
            report_text=report["report_text"],
            summary_stats=report.get("summary_stats", {}),
            file_path=report.get("file_path"),
            email_sent=report.get("email_sent", False),
            email_recipients=report.get("email_recipients", []),
            created_at=report["created_at"],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting latest report: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cycle-progress", response_model=CycleProgressResponse)
async def get_cycle_progress_endpoint(
    current_user: dict = Depends(get_current_user),
):
    """Get current sync cycle progress (which buckets have been dispatched)."""
    try:
        progress = get_cycle_progress()

        return CycleProgressResponse(
            cycle_id=progress["cycle_id"],
            buckets_completed=progress["buckets_completed"],
            total_buckets=progress["total_buckets"],
            is_complete=progress["is_complete"],
            progress_percent=progress["progress_percent"],
        )
    except Exception as e:
        logger.error(f"Error getting cycle progress: {e}")
        raise HTTPException(status_code=500, detail=str(e))
