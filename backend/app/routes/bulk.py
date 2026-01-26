"""
Bulk operation API endpoints.

Provides endpoints for:
- Starting bulk search operations
- Starting bulk publish operations
- Checking batch status
- Cancelling batches
"""
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Depends, Response

from app.schemas.bulk import (
    BulkSearchRequest,
    BulkPublishRequest,
    BulkOperationResponse,
    BatchStatusResponse,
    BatchListResponse,
    FailedItem,
)
from app.db.batch_store import BatchStore
from app.core.config import settings
from app.core.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["bulk"])

# Initialize batch store
batch_store = BatchStore(settings)


@router.post("/bulk-search", response_model=BulkOperationResponse)
async def bulk_search(
    request: BulkSearchRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Start a bulk search operation.

    Accepts a list of part numbers and queues them for extraction from Boeing API.
    Returns immediately with a batch_id for progress tracking.

    Supports idempotency: if the same idempotency_key is provided twice,
    returns the existing batch instead of creating a duplicate.
    """
    user_id = current_user["user_id"]

    # Check for existing batch with same idempotency key
    if request.idempotency_key:
        existing = batch_store.get_batch_by_idempotency_key(request.idempotency_key)
        if existing:
            logger.info(f"Returning existing batch {existing['id']} for idempotency key")
            return BulkOperationResponse(
                batch_id=existing["id"],
                total_items=existing["total_items"],
                status=existing["status"],
                message="Returning existing batch (idempotent request)",
                idempotency_key=request.idempotency_key
            )

    # Import here to avoid circular imports
    from celery_app.tasks.extraction import process_bulk_search

    # Create batch record with user_id and part_numbers
    batch = batch_store.create_batch(
        batch_type="search",
        total_items=len(request.part_numbers),
        idempotency_key=request.idempotency_key,
        user_id=user_id,
        part_numbers=request.part_numbers
    )

    # Queue the bulk search task with user_id
    task = process_bulk_search.delay(batch["id"], request.part_numbers, user_id)

    # Update batch with celery task ID
    batch_store.client.table("batches").update({
        "celery_task_id": task.id
    }).eq("id", batch["id"]).execute()

    logger.info(f"Started bulk search batch {batch['id']} with {len(request.part_numbers)} parts for user {user_id}")

    return BulkOperationResponse(
        batch_id=batch["id"],
        total_items=len(request.part_numbers),
        status="processing",
        message=f"Bulk search started. Processing {len(request.part_numbers)} part numbers.",
        idempotency_key=request.idempotency_key
    )


@router.post("/bulk-publish", response_model=BulkOperationResponse)
async def bulk_publish(
    request: BulkPublishRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Start a bulk publish operation.

    Accepts a list of part numbers (must exist in product_staging) and
    queues them for publishing to Shopify.
    """
    user_id = current_user["user_id"]

    # Check for existing batch with same idempotency key
    if request.idempotency_key:
        existing = batch_store.get_batch_by_idempotency_key(request.idempotency_key)
        if existing:
            logger.info(f"Returning existing batch {existing['id']} for idempotency key")
            return BulkOperationResponse(
                batch_id=existing["id"],
                total_items=existing["total_items"],
                status=existing["status"],
                message="Returning existing batch (idempotent request)",
                idempotency_key=request.idempotency_key
            )

    from celery_app.tasks.publishing import publish_batch

    # Create batch record with user_id and part_numbers
    batch = batch_store.create_batch(
        batch_type="publish",
        total_items=len(request.part_numbers),
        idempotency_key=request.idempotency_key,
        user_id=user_id,
        part_numbers=request.part_numbers
    )

    # Queue the publish task with user_id
    task = publish_batch.delay(batch["id"], request.part_numbers, user_id)

    # Update batch with celery task ID
    batch_store.client.table("batches").update({
        "celery_task_id": task.id
    }).eq("id", batch["id"]).execute()

    logger.info(f"Started bulk publish batch {batch['id']} with {len(request.part_numbers)} products for user {user_id}")

    return BulkOperationResponse(
        batch_id=batch["id"],
        total_items=len(request.part_numbers),
        status="processing",
        message=f"Bulk publish started. Publishing {len(request.part_numbers)} products to Shopify.",
        idempotency_key=request.idempotency_key
    )


@router.get("/batches", response_model=BatchListResponse)
async def list_batches(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, description="Filter by status"),
    current_user: dict = Depends(get_current_user)
):
    """
    List all batches with pagination for the current user.

    Query parameters:
    - limit: Max batches to return (1-100, default 50)
    - offset: Number to skip for pagination
    - status: Optional filter (pending, processing, completed, failed, cancelled)
    """
    user_id = current_user["user_id"]
    batches, total = batch_store.list_batches(limit=limit, offset=offset, status=status, user_id=user_id)

    # Convert to response models
    batch_responses = []
    for b in batches:
        progress = _calculate_progress(b)
        batch_responses.append(BatchStatusResponse(
            id=b["id"],
            batch_type=b["batch_type"],
            status=b["status"],
            total_items=b["total_items"],
            extracted_count=b["extracted_count"],
            normalized_count=b["normalized_count"],
            published_count=b["published_count"],
            failed_count=b["failed_count"],
            progress_percent=progress,
            part_numbers=b.get("part_numbers"),
            error_message=b.get("error_message"),
            idempotency_key=b.get("idempotency_key"),
            created_at=b["created_at"],
            updated_at=b["updated_at"],
            completed_at=b.get("completed_at"),
        ))

    return BatchListResponse(batches=batch_responses, total=total)


@router.get("/batches/{batch_id}", response_model=BatchStatusResponse)
async def get_batch_status(
    batch_id: str,
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    """
    Get detailed status of a specific batch.

    Includes progress percentages and list of failed items.
    Only returns batch if owned by the current user.

    HTTP Status Codes:
    - 200: Batch completed successfully or is still processing
    - 207: Batch completed with partial failures (some items succeeded, some failed)
    - 404: Batch not found
    - 500: Batch failed completely (all items failed)
    """
    user_id = current_user["user_id"]
    batch = batch_store.get_batch_by_user(batch_id, user_id)

    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    progress = _calculate_progress(batch)

    # Parse failed items
    failed_items = None
    if batch.get("failed_items"):
        failed_items = [
            FailedItem(
                part_number=item.get("part_number", "unknown"),
                error=item.get("error", "Unknown error"),
                timestamp=item.get("timestamp")
            )
            for item in batch["failed_items"]
        ]

    # Determine HTTP status code based on batch status
    batch_status = batch["status"]
    failed_count = batch["failed_count"]
    total_items = batch["total_items"]

    if batch_status == "failed":
        # Batch failed completely
        response.status_code = 500
    elif batch_status == "completed" and failed_count > 0:
        # Batch completed with partial failures
        response.status_code = 207  # Multi-Status
    # else: 200 OK (processing, completed with no failures, cancelled, etc.)

    return BatchStatusResponse(
        id=batch["id"],
        batch_type=batch["batch_type"],
        status=batch["status"],
        total_items=batch["total_items"],
        extracted_count=batch["extracted_count"],
        normalized_count=batch["normalized_count"],
        published_count=batch["published_count"],
        failed_count=batch["failed_count"],
        progress_percent=progress,
        failed_items=failed_items,
        part_numbers=batch.get("part_numbers"),
        error_message=batch.get("error_message"),
        idempotency_key=batch.get("idempotency_key"),
        created_at=batch["created_at"],
        updated_at=batch["updated_at"],
        completed_at=batch.get("completed_at"),
    )


@router.delete("/batches/{batch_id}")
async def cancel_batch_endpoint(
    batch_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Cancel a batch operation.

    Marks the batch as cancelled and attempts to revoke in-flight Celery tasks.
    Note: Already completed items cannot be rolled back.
    Only allows cancellation of batches owned by the current user.
    """
    user_id = current_user["user_id"]
    batch = batch_store.get_batch_by_user(batch_id, user_id)

    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    if batch["status"] in ("completed", "failed", "cancelled"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel batch with status: {batch['status']}"
        )

    # Queue cancellation task
    from celery_app.tasks.batch import cancel_batch as cancel_batch_task
    cancel_batch_task.delay(batch_id)

    return {
        "message": "Batch cancellation initiated",
        "batch_id": batch_id
    }


@router.get("/products/staging")
async def get_staging_products(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, description="Filter by status (fetched, enriched, published)"),
    batch_id: Optional[str] = Query(None, description="Filter by batch_id"),
    current_user: dict = Depends(get_current_user)
):
    """
    Get products from product_staging table for the current user.

    This endpoint fetches normalized products that have been processed
    through bulk search operations. Products are returned in descending
    order by creation date (most recent first).

    If batch_id is provided, only returns products associated with that batch.
    """
    from supabase import create_client

    user_id = current_user["user_id"]
    client = create_client(settings.supabase_url, settings.supabase_key)

    try:
        query = client.table("product_staging").select("*", count="exact")

        # Filter by user_id
        query = query.eq("user_id", user_id)

        if status:
            query = query.eq("status", status)

        if batch_id:
            query = query.eq("batch_id", batch_id)

        result = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()

        products = result.data or []
        total = result.count or 0

        logger.info(f"Fetched {len(products)} products from product_staging for user {user_id} (batch: {batch_id}, total: {total})")

        return {
            "products": products,
            "total": total,
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        logger.error(f"Failed to fetch staging products: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch products: {e}")


@router.get("/products/raw-data/{part_number}")
async def get_raw_boeing_data(
    part_number: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get raw Boeing API data for a specific part number.

    Searches the boeing_raw_data table for entries containing the part number
    in the search_query field. Only returns data for the current user.
    """
    from supabase import create_client

    user_id = current_user["user_id"]
    client = create_client(settings.supabase_url, settings.supabase_key)

    try:
        # Search for the part number in boeing_raw_data table
        # The search_query field contains comma-separated part numbers
        # Filter by user_id for user-specific data
        result = client.table("boeing_raw_data")\
            .select("*")\
            .eq("user_id", user_id)\
            .ilike("search_query", f"%{part_number}%")\
            .order("created_at", desc=True)\
            .limit(1)\
            .execute()

        if not result.data:
            return {"raw_data": None, "message": "No raw data found for this part number"}

        raw_record = result.data[0]
        raw_payload = raw_record.get("raw_payload", {})

        # Extract the specific line item for this part number from the payload
        line_items = raw_payload.get("lineItems", [])
        matching_item = None

        for item in line_items:
            if item.get("aviallPartNumber") == part_number:
                matching_item = item
                break

        if matching_item:
            return {
                "raw_data": {
                    **matching_item,
                    "currency": raw_payload.get("currency")
                },
                "search_query": raw_record.get("search_query"),
                "fetched_at": raw_record.get("created_at")
            }
        else:
            # Return the full payload if specific item not found
            return {
                "raw_data": raw_payload,
                "search_query": raw_record.get("search_query"),
                "fetched_at": raw_record.get("created_at")
            }

    except Exception as e:
        logger.error(f"Failed to fetch raw Boeing data for {part_number}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch raw data: {e}")


def _calculate_progress(batch: dict) -> float:
    """Calculate progress percentage based on batch type."""
    total = batch["total_items"]
    if total == 0:
        return 0.0

    if batch["batch_type"] == "search":
        completed = batch["normalized_count"] + batch["failed_count"]
    else:  # publish
        completed = batch["published_count"] + batch["failed_count"]

    return round((completed / total) * 100, 2)
