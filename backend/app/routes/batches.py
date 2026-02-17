"""
Batch routes — batch CRUD and status endpoints.

Batch management routes.

Provides:
- GET    /batches         – list batches
- GET    /batches/{id}    – batch status
- DELETE /batches/{id}    – cancel batch
Version: 1.0.0
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Depends, Response

from app.schemas.batches import BatchStatusResponse, BatchListResponse, FailedItem
from app.core.auth import get_current_user
from app.core.config import settings
from app.db.batch_store import BatchStore
from app.celery_app.tasks.batch import cancel_batch as cancel_batch_task
from app.services.batch_service import calculate_progress

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/batches", tags=["batches"])

batch_store = BatchStore(settings)


@router.get("", response_model=BatchListResponse)
async def list_batches(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, description="Filter by status"),
    current_user: dict = Depends(get_current_user),
):
    """List all batches with pagination for the current user."""
    user_id = current_user["user_id"]
    batches, total = batch_store.list_batches(
        limit=limit, offset=offset, status=status, user_id=user_id
    )

    batch_responses = []
    for b in batches:
        progress = calculate_progress(b)

        failed_items = None
        if b.get("failed_items"):
            failed_items = [
                FailedItem(
                    part_number=item.get("part_number", "unknown"),
                    error=item.get("error", "Unknown error"),
                    stage=item.get("stage"),
                    timestamp=item.get("timestamp"),
                )
                for item in b["failed_items"]
            ]

        batch_responses.append(BatchStatusResponse(
            id=b["id"], batch_type=b["batch_type"], status=b["status"],
            total_items=b["total_items"],
            extracted_count=b["extracted_count"],
            normalized_count=b["normalized_count"],
            published_count=b["published_count"],
            failed_count=b["failed_count"],
            progress_percent=progress,
            failed_items=failed_items,
            skipped_count=b.get("skipped_count", 0),
            skipped_part_numbers=b.get("skipped_part_numbers"),
            part_numbers=b.get("part_numbers"),
            publish_part_numbers=b.get("publish_part_numbers"),
            error_message=b.get("error_message"),
            idempotency_key=b.get("idempotency_key"),
            created_at=b["created_at"],
            updated_at=b["updated_at"],
            completed_at=b.get("completed_at"),
        ))

    return BatchListResponse(batches=batch_responses, total=total)


@router.get("/{batch_id}", response_model=BatchStatusResponse)
async def get_batch_status(
    batch_id: str,
    response: Response,
    current_user: dict = Depends(get_current_user),
):
    """Get detailed status of a specific batch."""
    user_id = current_user["user_id"]
    batch = batch_store.get_batch_by_user(batch_id, user_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    progress = calculate_progress(batch)

    failed_items = None
    if batch.get("failed_items"):
        failed_items = [
            FailedItem(
                part_number=item.get("part_number", "unknown"),
                error=item.get("error", "Unknown error"),
                stage=item.get("stage"),
                timestamp=item.get("timestamp"),
            )
            for item in batch["failed_items"]
        ]

    if batch["status"] == "failed":
        response.status_code = 500
    elif batch["status"] == "completed" and batch["failed_count"] > 0:
        response.status_code = 207

    return BatchStatusResponse(
        id=batch["id"], batch_type=batch["batch_type"], status=batch["status"],
        total_items=batch["total_items"],
        extracted_count=batch["extracted_count"],
        normalized_count=batch["normalized_count"],
        published_count=batch["published_count"],
        failed_count=batch["failed_count"],
        progress_percent=progress,
        failed_items=failed_items,
        skipped_count=batch.get("skipped_count", 0),
        skipped_part_numbers=batch.get("skipped_part_numbers"),
        part_numbers=batch.get("part_numbers"),
        publish_part_numbers=batch.get("publish_part_numbers"),
        error_message=batch.get("error_message"),
        idempotency_key=batch.get("idempotency_key"),
        created_at=batch["created_at"],
        updated_at=batch["updated_at"],
        completed_at=batch.get("completed_at"),
    )


@router.delete("/{batch_id}")
async def cancel_batch_endpoint(
    batch_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Cancel a batch operation."""
    user_id = current_user["user_id"]
    batch = batch_store.get_batch_by_user(batch_id, user_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    if batch["status"] in ("completed", "failed", "cancelled"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel batch with status: {batch['status']}",
        )

    cancel_batch_task.delay(batch_id)

    return {"message": "Batch cancellation initiated", "batch_id": batch_id}
