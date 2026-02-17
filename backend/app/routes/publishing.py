"""
Publishing routes — Shopify publish, update, and metafield setup.

Publishing pipeline routes.

Provides:
- POST /publishing/publish           – single product publish
- POST /publishing/bulk-publish      – bulk publish
- PUT  /publishing/products/{id}     – update product in Shopify
- GET  /publishing/check             – check SKU in Shopify
- POST /publishing/metafields/setup  – create metafield definitions
Version: 1.0.0
"""
import logging

from fastapi import APIRouter, Body, HTTPException, Query, Depends

from app.schemas.publishing import PublishRequest, PublishResponse, UpdateRequest, CheckResponse
from app.schemas.batches import BulkPublishRequest, BulkOperationResponse
from app.core.auth import get_current_user
from app.core.config import settings
from app.db.batch_store import BatchStore
from app.container import (
    get_shopify_orchestrator, get_staging_store, get_product_store,
    get_image_store, get_sync_store,
)
from app.celery_app.tasks.publishing import publish_product as pub_task, publish_batch
from app.services.publishing_service import PublishingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/publishing", tags=["publishing"])

batch_store = BatchStore(settings)


def _get_service() -> PublishingService:
    return PublishingService(
        shopify=get_shopify_orchestrator(),
        staging_store=get_staging_store(),
        product_store=get_product_store(),
        image_store=get_image_store(),
        sync_store=get_sync_store(),
        settings=settings,
    )


@router.post("/publish", response_model=PublishResponse)
async def publish_product(
    payload: PublishRequest = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """Publish a single product to Shopify via Celery queue."""
    try:
        user_id = current_user["user_id"]
        part_number = payload.part_number

        if payload.batch_id:
            batch = batch_store.get_batch_by_user(payload.batch_id, user_id)
            if not batch:
                raise HTTPException(status_code=404, detail="Batch not found")
            if batch["batch_type"] != "publish":
                batch_store.update_batch_type(payload.batch_id, "publish")
                batch_store.update_status(payload.batch_id, "processing")
            pub_task.delay(payload.batch_id, part_number, user_id)
            return PublishResponse(
                success=True, shopifyProductId=None,
                batch_id=payload.batch_id,
                message=f"Publish queued for {part_number}. Batch: {payload.batch_id}",
            )

        batch = batch_store.create_batch(
            batch_type="publish", total_items=1,
            idempotency_key=None, user_id=user_id, part_numbers=[part_number],
        )
        task = publish_batch.delay(batch["id"], [part_number], user_id)
        batch_store.client.table("batches").update(
            {"celery_task_id": task.id}
        ).eq("id", batch["id"]).execute()

        return PublishResponse(
            success=True, shopifyProductId=None, batch_id=batch["id"],
            message=f"Publish queued. Track with batch_id: {batch['id']}",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/bulk-publish", response_model=BulkOperationResponse)
async def bulk_publish(
    request: BulkPublishRequest,
    current_user: dict = Depends(get_current_user),
):
    """Start a bulk publish operation."""
    user_id = current_user["user_id"]

    if request.idempotency_key:
        existing = batch_store.get_batch_by_idempotency_key(request.idempotency_key)
        if existing:
            return BulkOperationResponse(
                batch_id=existing["id"], total_items=existing["total_items"],
                status=existing["status"],
                message="Returning existing batch (idempotent request)",
                idempotency_key=request.idempotency_key,
            )

    if request.batch_id:
        batch = batch_store.get_batch_by_user(request.batch_id, user_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")
        if batch["batch_type"] not in ("normalize", "extract"):
            if batch["batch_type"] == "publish" and batch["status"] == "processing":
                raise HTTPException(status_code=400, detail="Publishing already in progress")

        publish_count = len(request.part_numbers)
        batch_store.update_batch_type(
            request.batch_id, "publish",
            new_total_items=publish_count, publish_part_numbers=request.part_numbers,
        )
        batch_store.update_status(request.batch_id, "processing")

        # Reset publish-phase counters only; preserve normalization-phase
        # skipped_count / skipped_part_numbers (set by record_skipped during
        # the normalize stage).
        batch_store.client.table("batches").update({
            "published_count": 0, "failed_count": 0, "failed_items": [],
        }).eq("id", request.batch_id).execute()

        task = publish_batch.delay(request.batch_id, request.part_numbers, user_id)
        batch_store.client.table("batches").update(
            {"celery_task_id": task.id}
        ).eq("id", request.batch_id).execute()

        return BulkOperationResponse(
            batch_id=request.batch_id, total_items=publish_count,
            status="processing",
            message=f"Publishing started. Processing {publish_count} products.",
            idempotency_key=request.idempotency_key,
        )

    batch = batch_store.create_batch(
        batch_type="publish", total_items=len(request.part_numbers),
        idempotency_key=request.idempotency_key,
        user_id=user_id, part_numbers=request.part_numbers,
    )
    task = publish_batch.delay(batch["id"], request.part_numbers, user_id)
    batch_store.client.table("batches").update(
        {"celery_task_id": task.id}
    ).eq("id", batch["id"]).execute()

    return BulkOperationResponse(
        batch_id=batch["id"], total_items=len(request.part_numbers),
        status="processing",
        message=f"Bulk publish started. Publishing {len(request.part_numbers)} products.",
        idempotency_key=request.idempotency_key,
    )


@router.put("/products/{shopify_product_id}", response_model=PublishResponse)
async def update_product(
    shopify_product_id: str,
    product: UpdateRequest = Body(...),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = _get_service()
        return await service.update_product(shopify_product_id, product.model_dump())
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/check", response_model=CheckResponse)
async def check_sku(
    sku: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = _get_service()
        found = await service.find_product_by_sku(sku)
        return {"shopifyProductId": found}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/metafields/setup")
async def setup_metafields(current_user: dict = Depends(get_current_user)):
    try:
        service = _get_service()
        await service.setup_metafield_definitions()
        return {"success": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
