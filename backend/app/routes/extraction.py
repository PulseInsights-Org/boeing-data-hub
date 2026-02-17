"""
Extraction routes — Boeing product search and bulk extraction.

Extraction pipeline routes.

Provides:
- GET  /extraction/search     – single product search via Boeing API
- POST /extraction/bulk-search – bulk search operation
Version: 1.0.0
"""
import logging
from typing import List

from fastapi import APIRouter, HTTPException, Query, Depends

from app.schemas.products import NormalizedProduct
from app.schemas.batches import BulkSearchRequest, BulkOperationResponse
from app.core.auth import get_current_user
from app.core.config import settings
from app.db.batch_store import BatchStore
from app.container import get_boeing_client, get_raw_data_store, get_staging_store
from app.celery_app.tasks.extraction import process_bulk_search
from app.services.extraction_service import ExtractionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/extraction", tags=["extraction"])

batch_store = BatchStore(settings)


def _get_service() -> ExtractionService:
    return ExtractionService(
        client=get_boeing_client(),
        raw_store=get_raw_data_store(),
        staging_store=get_staging_store(),
    )


@router.get("/search", response_model=List[NormalizedProduct])
async def extraction_search(
    query: str = Query(..., min_length=1),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = _get_service()
        return await service.search_products(query, user_id=current_user["user_id"])
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/bulk-search", response_model=BulkOperationResponse)
async def bulk_search(
    request: BulkSearchRequest,
    current_user: dict = Depends(get_current_user),
):
    """Start a bulk search operation."""
    user_id = current_user["user_id"]

    if request.idempotency_key:
        existing = batch_store.get_batch_by_idempotency_key(request.idempotency_key)
        if existing:
            return BulkOperationResponse(
                batch_id=existing["id"],
                total_items=existing["total_items"],
                status=existing["status"],
                message="Returning existing batch (idempotent request)",
                idempotency_key=request.idempotency_key,
            )

    batch = batch_store.create_batch(
        batch_type="extract",
        total_items=len(request.part_numbers),
        idempotency_key=request.idempotency_key,
        user_id=user_id,
        part_numbers=request.part_numbers,
    )

    task = process_bulk_search.delay(batch["id"], request.part_numbers, user_id)
    batch_store.client.table("batches").update(
        {"celery_task_id": task.id}
    ).eq("id", batch["id"]).execute()

    return BulkOperationResponse(
        batch_id=batch["id"],
        total_items=len(request.part_numbers),
        status="processing",
        message=f"Bulk search started. Processing {len(request.part_numbers)} part numbers.",
        idempotency_key=request.idempotency_key,
    )
