from typing import List

from fastapi import APIRouter, HTTPException, Query, Depends

from ..schemas.products import NormalizedProduct
from ..services.boeing_service import BoeingService
from ..core.auth import get_current_user


def build_boeing_router(service: BoeingService) -> APIRouter:
    router = APIRouter()

    @router.get("/api/boeing/product-search", response_model=List[NormalizedProduct])
    async def boeing_product_search(
        query: str = Query(..., min_length=1),
        current_user: dict = Depends(get_current_user)
    ):
        try:
            return await service.search_products(query, user_id=current_user["user_id"])
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return router
