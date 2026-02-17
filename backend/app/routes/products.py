"""
Product routes — staging, published, and raw data endpoints.

Products pipeline routes.

Provides endpoints for:
- Published products (from product table)
- Staging products
- Raw Boeing data lookup
Version: 1.0.0
"""
import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel

from supabase import create_client

from app.core.auth import get_current_user
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/products", tags=["products"])


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


def _get_client():
    return create_client(settings.supabase_url, settings.supabase_key)


@router.get("/published", response_model=PublishedProductsResponse)
async def get_published_products(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: Optional[str] = Query(default=None),
    current_user: dict = Depends(get_current_user)
):
    """Get published products from the product table."""
    user_id = current_user["user_id"]
    client = _get_client()

    columns = (
        "id,sku,title,body_html,vendor,price,cost_per_item,currency,"
        "inventory_quantity,weight,weight_unit,country_of_origin,"
        "dim_length,dim_width,dim_height,dim_uom,"
        "shopify_product_id,image_url,created_at,updated_at"
    )

    max_retries = 3
    last_error = None
    search_term = search.strip().lower() if search else None

    for attempt in range(max_retries):
        try:
            query = client.table("product").select(columns, count="exact")
            query = query.eq("user_id", user_id)
            query = query.order("updated_at", desc=True)

            if search_term:
                query = query.range(0, 999)
                response = query.execute()
                all_products = response.data or []

                filtered_products = [
                    p for p in all_products
                    if search_term in (p.get("sku") or "").lower()
                ]

                total = len(filtered_products)
                products = filtered_products[offset:offset + limit]
            else:
                query = query.range(offset, offset + limit - 1)
                response = query.execute()
                products = response.data or []
                total = response.count or 0

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

            is_worker_error = (
                "Worker threw exception" in error_str or
                "1101" in error_str or
                "JSON could not be generated" in error_str or
                "timeout" in error_str.lower()
            )

            if is_worker_error and attempt < max_retries - 1:
                wait_time = 0.5 * (2 ** attempt)
                logger.warning(
                    f"Supabase worker error on attempt {attempt + 1}/{max_retries}, "
                    f"retrying in {wait_time}s: {error_str[:200]}"
                )
                await asyncio.sleep(wait_time)
                continue
            else:
                logger.error(f"Failed to fetch published products: {error_str}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to fetch products after {attempt + 1} attempts: {error_str[:500]}"
                ) from exc

    raise HTTPException(status_code=500, detail=str(last_error)) from last_error


@router.get("/published/{product_id}", response_model=PublishedProduct)
async def get_published_product(
    product_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get a single published product by ID."""
    client = _get_client()
    columns = (
        "id,sku,title,body_html,vendor,price,cost_per_item,currency,"
        "inventory_quantity,weight,weight_unit,country_of_origin,"
        "dim_length,dim_width,dim_height,dim_uom,"
        "shopify_product_id,image_url,created_at,updated_at"
    )

    try:
        user_id = current_user["user_id"]

        response = (
            client.table("product")
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


@router.get("/staging")
async def get_staging_products(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, description="Filter by status (fetched, enriched, published)"),
    batch_id: Optional[str] = Query(None, description="Filter by batch_id"),
    current_user: dict = Depends(get_current_user)
):
    """Get products from product_staging table for the current user."""
    user_id = current_user["user_id"]
    client = _get_client()

    try:
        query = client.table("product_staging").select("*", count="exact")
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


@router.get("/raw-data/{part_number}")
async def get_raw_boeing_data(
    part_number: str,
    current_user: dict = Depends(get_current_user)
):
    """Get raw Boeing API data for a specific part number."""
    user_id = current_user["user_id"]
    client = _get_client()

    def strip_suffix(pn: str) -> str:
        return pn.split("=")[0] if pn else ""

    search_pn_stripped = strip_suffix(part_number)

    try:
        result = client.table("boeing_raw_data")\
            .select("id, search_query, created_at")\
            .eq("user_id", user_id)\
            .order("created_at", desc=True)\
            .limit(50)\
            .execute()

        if not result.data:
            return {"raw_data": None, "message": "No raw data found for this part number"}

        matching_record_id = None
        matching_search_query = None
        matching_created_at = None

        for record in result.data:
            search_query = record.get("search_query", "")
            search_parts = [p.strip() for p in search_query.split(",")]
            search_parts_stripped = [strip_suffix(p) for p in search_parts]

            if part_number in search_parts or search_pn_stripped in search_parts_stripped:
                matching_record_id = record.get("id")
                matching_search_query = search_query
                matching_created_at = record.get("created_at")
                break

        if not matching_record_id:
            return {"raw_data": None, "message": "No raw data found for this part number"}

        full_result = client.table("boeing_raw_data")\
            .select("raw_payload")\
            .eq("id", matching_record_id)\
            .single()\
            .execute()

        if not full_result.data:
            return {"raw_data": None, "message": "Failed to fetch raw payload"}

        raw_payload = full_result.data.get("raw_payload", {})

        line_items = raw_payload.get("lineItems", [])
        matching_item = None

        for item in line_items:
            aviall_pn = item.get("aviallPartNumber") or ""
            if aviall_pn == part_number or strip_suffix(aviall_pn) == search_pn_stripped:
                matching_item = item
                break

        if matching_item:
            return {
                "raw_data": {
                    **matching_item,
                    "currency": raw_payload.get("currency")
                },
                "search_query": matching_search_query,
                "fetched_at": matching_created_at
            }
        else:
            return {
                "raw_data": raw_payload,
                "search_query": matching_search_query,
                "fetched_at": matching_created_at
            }

    except Exception as e:
        logger.error(f"Failed to fetch raw Boeing data for {part_number}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch raw data: {e}")
