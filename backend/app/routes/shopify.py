from fastapi import APIRouter, Body, HTTPException, Query, Depends

from app.schemas.shopify import (
    ShopifyCheckResponse,
    ShopifyPublishRequest,
    ShopifyPublishResponse,
    ShopifyUpdateRequest,
)
from app.services.shopify_service import ShopifyService
from app.core.auth import get_current_user


def build_shopify_router(service: ShopifyService) -> APIRouter:
    router = APIRouter()

    @router.post("/api/shopify/publish", response_model=ShopifyPublishResponse)
    async def shopify_publish(
        payload: ShopifyPublishRequest = Body(...),
        current_user: dict = Depends(get_current_user)
    ):
        try:
            return await service.publish_product_by_part_number(
                payload.part_number,
                user_id=current_user["user_id"]
            )
        except HTTPException:
            raise
        except Exception as exc:  # pragma: no cover
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
