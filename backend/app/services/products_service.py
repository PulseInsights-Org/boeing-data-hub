"""
Products service — product listing and status queries.

Products service – published-product listing and detail.

Replaces: inline logic in routes/products.py
Version: 1.0.0
"""
import asyncio
import logging
from typing import Any, Dict, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# Columns selected for published-product queries
_PRODUCT_COLUMNS = (
    "id,sku,title,body_html,vendor,price,cost_per_item,currency,"
    "inventory_quantity,weight,weight_unit,country_of_origin,"
    "dim_length,dim_width,dim_height,dim_uom,"
    "shopify_product_id,image_url,created_at,updated_at"
)


class ProductsService:
    """Read-only queries on the ``product`` table."""

    def __init__(self, supabase_client) -> None:
        self._client = supabase_client

    async def list_published(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return paginated published products with optional SKU search."""
        search_term = search.strip().lower() if search else None

        max_retries = 3
        last_error: Optional[Exception] = None

        for attempt in range(max_retries):
            try:
                query = (
                    self._client.table("product")
                    .select(_PRODUCT_COLUMNS, count="exact")
                    .eq("user_id", user_id)
                    .order("updated_at", desc=True)
                )

                if search_term:
                    query = query.range(0, 999)
                    response = query.execute()
                    all_products = response.data or []

                    filtered = [
                        p
                        for p in all_products
                        if search_term in (p.get("sku") or "").lower()
                    ]
                    total = len(filtered)
                    products = filtered[offset : offset + limit]
                else:
                    query = query.range(offset, offset + limit - 1)
                    response = query.execute()
                    products = response.data or []
                    total = response.count or 0

                store_domain = settings.shopify_store_domain
                store_name = (
                    store_domain.replace(".myshopify.com", "")
                    if store_domain
                    else None
                )

                return {
                    "products": products,
                    "total": total,
                    "shopify_store_domain": store_name,
                }

            except Exception as exc:
                last_error = exc
                error_str = str(exc)

                is_worker_error = (
                    "Worker threw exception" in error_str
                    or "1101" in error_str
                    or "JSON could not be generated" in error_str
                    or "timeout" in error_str.lower()
                )

                if is_worker_error and attempt < max_retries - 1:
                    wait_time = 0.5 * (2 ** attempt)
                    logger.warning(
                        f"Supabase worker error on attempt "
                        f"{attempt + 1}/{max_retries}, "
                        f"retrying in {wait_time}s: {error_str[:200]}"
                    )
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logger.error(
                        f"Failed to fetch published products: {error_str}"
                    )
                    raise

        raise last_error  # type: ignore[misc]

    async def get_published_by_id(
        self, product_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Return a single published product or ``None``."""
        response = (
            self._client.table("product")
            .select(_PRODUCT_COLUMNS)
            .eq("id", product_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        return response.data[0] if response.data else None
