"""
Routes for published products (from the product table).
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel

from app.db.supabase_store import SupabaseStore
from app.core.auth import get_current_user
from app.core.config import settings


class PublishedProduct(BaseModel):
    """Published product response model."""
    id: str
    sku: str
    title: Optional[str] = None
    body_html: Optional[str] = None
    vendor: Optional[str] = None
    price: Optional[float] = None
    cost_per_item: Optional[float] = None
    currency: Optional[str] = None
    inventory_quantity: Optional[int] = None
    weight: Optional[float] = None
    weight_unit: Optional[str] = None
    country_of_origin: Optional[str] = None
    dim_length: Optional[float] = None
    dim_width: Optional[float] = None
    dim_height: Optional[float] = None
    dim_uom: Optional[str] = None
    shopify_product_id: Optional[str] = None
    image_url: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class PublishedProductsResponse(BaseModel):
    """Response for list of published products."""
    products: List[PublishedProduct]
    total: int
    shopify_store_domain: Optional[str] = None


def build_products_router(store: SupabaseStore) -> APIRouter:
    router = APIRouter()

    @router.get("/api/products/published", response_model=PublishedProductsResponse)
    async def get_published_products(
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        search: Optional[str] = Query(default=None),
        current_user: dict = Depends(get_current_user)
    ):
        """
        Get published products from the product table.
        Supports pagination and search by SKU.
        """
        try:
            user_id = current_user["user_id"]

            # Build query
            query = store._client.table("product").select("*", count="exact")

            # Filter by user_id
            query = query.eq("user_id", user_id)

            # Search by sku if provided
            if search and search.strip():
                query = query.ilike("sku", f"%{search.strip()}%")

            # Order by updated_at descending
            query = query.order("updated_at", desc=True)

            # Apply pagination
            query = query.range(offset, offset + limit - 1)

            # Execute query
            response = query.execute()

            products = response.data or []
            total = response.count or 0

            # Extract store name from domain (e.g., "zap-integration-test.myshopify.com" -> "zap-integration-test")
            store_domain = settings.shopify_store_domain
            store_name = store_domain.replace(".myshopify.com", "") if store_domain else None

            return PublishedProductsResponse(
                products=[PublishedProduct(**p) for p in products],
                total=total,
                shopify_store_domain=store_name
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/api/products/published/{product_id}", response_model=PublishedProduct)
    async def get_published_product(
        product_id: str,
        current_user: dict = Depends(get_current_user)
    ):
        """Get a single published product by ID."""
        try:
            user_id = current_user["user_id"]

            response = (
                store._client.table("product")
                .select("*")
                .eq("id", product_id)
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )

            if not response.data:
                raise HTTPException(status_code=404, detail="Product not found")

            return PublishedProduct(**response.data[0])
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return router

