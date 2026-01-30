"""
Routes for published products (from the product table).

Note: Search filtering is done in Python (not SQL) to avoid URL encoding issues
where patterns like %1C can be misinterpreted as URL-encoded control characters.
"""
import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel

from app.db.supabase_store import SupabaseStore
from app.core.auth import get_current_user
from app.core.config import settings

logger = logging.getLogger(__name__)


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
        user_id = current_user["user_id"]

        # Select only the columns we need (avoid large payloads that cause worker timeouts)
        columns = (
            "id,sku,title,body_html,vendor,price,cost_per_item,currency,"
            "inventory_quantity,weight,weight_unit,country_of_origin,"
            "dim_length,dim_width,dim_height,dim_uom,"
            "shopify_product_id,image_url,created_at,updated_at"
        )

        # Retry logic for transient Supabase/Cloudflare worker errors
        max_retries = 3
        last_error = None

        # Determine if we need to filter in Python (to avoid URL encoding issues)
        # Patterns like %1C can be misinterpreted as URL-encoded bytes
        search_term = search.strip().lower() if search else None

        for attempt in range(max_retries):
            try:
                # Build query with explicit columns
                query = store._client.table("product").select(columns, count="exact")

                # Filter by user_id
                query = query.eq("user_id", user_id)

                # Order by updated_at descending
                query = query.order("updated_at", desc=True)

                if search_term:
                    # When searching, fetch more records and filter in Python
                    # This avoids URL encoding issues where patterns like %1C are misinterpreted
                    query = query.range(0, 999)  # Fetch up to 1000 records
                    response = query.execute()
                    all_products = response.data or []

                    # Filter in Python (case-insensitive partial match on sku)
                    filtered_products = [
                        p for p in all_products
                        if search_term in (p.get("sku") or "").lower()
                    ]

                    # Apply pagination to filtered results
                    total = len(filtered_products)
                    products = filtered_products[offset:offset + limit]
                else:
                    # No search - use database pagination directly
                    query = query.range(offset, offset + limit - 1)
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
                last_error = exc
                error_str = str(exc)

                # Check if it's a Cloudflare worker error (1101) or timeout
                is_worker_error = (
                    "Worker threw exception" in error_str or
                    "1101" in error_str or
                    "JSON could not be generated" in error_str or
                    "timeout" in error_str.lower()
                )

                if is_worker_error and attempt < max_retries - 1:
                    # Exponential backoff: 0.5s, 1s, 2s
                    wait_time = 0.5 * (2 ** attempt)
                    logger.warning(
                        f"Supabase worker error on attempt {attempt + 1}/{max_retries}, "
                        f"retrying in {wait_time}s: {error_str[:200]}"
                    )
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    # Non-retryable error or max retries reached
                    logger.error(f"Failed to fetch published products: {error_str}")
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to fetch products after {attempt + 1} attempts: {error_str[:500]}"
                    ) from exc

        # Should not reach here, but just in case
        raise HTTPException(status_code=500, detail=str(last_error)) from last_error

    @router.get("/api/products/published/{product_id}", response_model=PublishedProduct)
    async def get_published_product(
        product_id: str,
        current_user: dict = Depends(get_current_user)
    ):
        """Get a single published product by ID."""
        # Use specific columns for consistency
        columns = (
            "id,sku,title,body_html,vendor,price,cost_per_item,currency,"
            "inventory_quantity,weight,weight_unit,country_of_origin,"
            "dim_length,dim_width,dim_height,dim_uom,"
            "shopify_product_id,image_url,created_at,updated_at"
        )

        try:
            user_id = current_user["user_id"]

            response = (
                store._client.table("product")
                .select(columns)
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

