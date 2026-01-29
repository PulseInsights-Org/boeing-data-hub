import logging
from fastapi import APIRouter, Body, HTTPException, Query, Depends

from app.schemas.shopify import (
    ShopifyCheckResponse,
    ShopifyPublishRequest,
    ShopifyPublishResponse,
    ShopifyUpdateRequest,
)
from app.services.shopify_service import ShopifyService
from app.core.auth import get_current_user
from app.core.config import settings
from app.db.batch_store import BatchStore

logger = logging.getLogger(__name__)

# Initialize batch store for individual publish operations
batch_store = BatchStore(settings)


def build_shopify_router(service: ShopifyService) -> APIRouter:
    router = APIRouter()

    @router.post("/api/shopify/publish", response_model=ShopifyPublishResponse)
    async def shopify_publish(
        payload: ShopifyPublishRequest = Body(...),
        current_user: dict = Depends(get_current_user)
    ):
        """
        Publish a single product to Shopify via Celery queue.

        Instead of making a direct API call, this endpoint queues the publish
        task to be processed by a Celery worker, ensuring consistent behavior
        with bulk publish operations.

        If batch_id is provided, uses that existing batch (continues the pipeline).
        Otherwise, this is a standalone publish that doesn't update any batch.

        Returns a batch_id for tracking the publish progress.
        """
        try:
            user_id = current_user["user_id"]
            part_number = payload.part_number

            # Import here to avoid circular imports
            from celery_app.tasks.publishing import publish_product

            # If batch_id is provided, use the existing batch
            if payload.batch_id:
                batch = batch_store.get_batch_by_user(payload.batch_id, user_id)
                if not batch:
                    raise HTTPException(status_code=404, detail="Batch not found")

                # Update batch type to "publishing" if not already
                if batch["batch_type"] != "publishing":
                    batch_store.update_batch_type(payload.batch_id, "publishing")
                    batch_store.update_status(payload.batch_id, "processing")

                # Queue individual product publish task
                publish_product.delay(payload.batch_id, part_number, user_id)

                logger.info(f"Queued individual publish for part {part_number} in batch {payload.batch_id}")

                return ShopifyPublishResponse(
                    success=True,
                    shopifyProductId=None,  # Will be set by the worker
                    batch_id=payload.batch_id,
                    message=f"Publish queued for {part_number}. Batch: {payload.batch_id}"
                )

            # No batch_id provided - queue a standalone publish (no batch tracking)
            # This is useful for quick individual publishes outside of the batch flow
            from celery_app.tasks.publishing import publish_batch

            # Create a minimal batch for tracking
            batch = batch_store.create_batch(
                batch_type="publishing",
                total_items=1,
                idempotency_key=None,
                user_id=user_id,
                part_numbers=[part_number]
            )

            # Queue the publish task via Celery
            task = publish_batch.delay(batch["id"], [part_number], user_id)

            # Update batch with celery task ID
            batch_store.client.table("batches").update({
                "celery_task_id": task.id
            }).eq("id", batch["id"]).execute()

            logger.info(f"Queued standalone publish for part {part_number}, batch_id={batch['id']}")

            return ShopifyPublishResponse(
                success=True,
                shopifyProductId=None,  # Will be set by the worker
                batch_id=batch["id"],
                message=f"Publish queued for processing. Track progress with batch_id: {batch['id']}"
            )
        except HTTPException:
            raise
        except Exception as exc:  # pragma: no cover
            logger.error(f"Failed to queue publish for {payload.part_number}: {exc}")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.put("/api/shopify/products/{shopify_product_id}", response_model=ShopifyPublishResponse)
    async def shopify_update(
        shopify_product_id: str,
        product: ShopifyUpdateRequest = Body(...),
        current_user: dict = Depends(get_current_user)
    ):
        try:
            return await service.update_product(shopify_product_id, product.model_dump())
        except HTTPException:
            raise
        except Exception as exc:  # pragma: no cover
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/api/shopify/check", response_model=ShopifyCheckResponse)
    async def shopify_check(
        sku: str = Query(...),
        current_user: dict = Depends(get_current_user)
    ):
        try:
            shopify_product_id = await service.find_product_by_sku(sku)
            return {"shopifyProductId": shopify_product_id}
        except HTTPException:
            raise
        except Exception as exc:  # pragma: no cover
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/api/shopify/metafields/setup")
    async def shopify_setup_metafields(current_user: dict = Depends(get_current_user)):
        try:
            await service.setup_metafield_definitions()
            return {"success": True}
        except HTTPException:
            raise
        except Exception as exc:  # pragma: no cover
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return router
