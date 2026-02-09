"""
Multi-Part Search Endpoint
==========================
Search multiple SKUs/part numbers in Shopify store and return product details.

Endpoint: POST /api/shopify/multi-part-search
Authentication: None (public endpoint)
Max SKUs per request: 50
"""

import os
import asyncio
import logging
from typing import List, Optional, Dict, Any, Set

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from dotenv import load_dotenv


# ==========================================
# SECTION 1: Configuration & Constants
# ==========================================

# Load environment variables
load_dotenv()

# Shopify credentials from .env
_raw_domain = os.getenv("SHOPIFY_STORE_DOMAIN", "")
# Auto-append .myshopify.com if not present
if _raw_domain and not _raw_domain.endswith(".myshopify.com"):
    SHOPIFY_STORE_DOMAIN = f"{_raw_domain}.myshopify.com"
else:
    SHOPIFY_STORE_DOMAIN = _raw_domain

SHOPIFY_ADMIN_API_TOKEN = os.getenv("SHOPIFY_ADMIN_API_TOKEN")
SHOPIFY_API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2024-10")

# Constants
BATCH_SIZE = 25  # SKUs per GraphQL query
MAX_SKUS_ALLOWED = 50  # Maximum SKUs per request
REQUEST_TIMEOUT = 30  # Seconds
DELAY_BETWEEN_BATCHES = 0.1  # 100ms pause between API calls

# Logger
logger = logging.getLogger(__name__)


# ==========================================
# SECTION 2: Pydantic Models
# ==========================================

class MultiPartSearchRequest(BaseModel):
    """Request model for multi-part search endpoint."""
    part_numbers: List[str] = Field(
        ...,
        min_length=1,
        max_length=MAX_SKUS_ALLOWED,
        description="List of SKUs/part numbers to search (1-50 items)"
    )


class ProductImage(BaseModel):
    """Product image details."""
    url: str
    alt_text: Optional[str] = None


class ProductMetafield(BaseModel):
    """Product metafield details."""
    namespace: str
    key: str
    value: str


class FoundProduct(BaseModel):
    """Details of a found product."""
    sku: str
    shopify_product_id: str
    shopify_variant_id: str
    title: str
    handle: str
    status: str
    price: Optional[str] = None
    compare_at_price: Optional[str] = None
    inventory_quantity: Optional[int] = None
    vendor: Optional[str] = None
    product_type: Optional[str] = None
    description: Optional[str] = None
    tags: List[str] = []
    images: List[ProductImage] = []
    metafields: List[ProductMetafield] = []


class SearchSummary(BaseModel):
    """Summary statistics for the search."""
    total_requested: int
    unique_searched: int
    found: int
    not_found: int
    duplicates_removed: int


class MultiPartSearchResponse(BaseModel):
    """Response model for multi-part search endpoint."""
    success: bool
    found_products: List[FoundProduct]
    not_found_skus: List[str]
    summary: SearchSummary
    message: Optional[str] = None


# ==========================================
# SECTION 3: Helper Functions
# ==========================================

def sanitize_skus(part_numbers: List[str]) -> tuple[List[str], int]:
    """
    Clean and deduplicate SKUs.

    Args:
        part_numbers: Raw list of SKUs from request

    Returns:
        Tuple of (cleaned unique SKUs, count of duplicates removed)
    """
    seen: Set[str] = set()
    unique_skus: List[str] = []

    for sku in part_numbers:
        # Strip whitespace
        cleaned = sku.strip()

        # Skip empty strings
        if not cleaned:
            continue

        # Escape double quotes for GraphQL safety
        cleaned = cleaned.replace('"', '\\"')

        # Remove non-printable characters
        cleaned = ''.join(c for c in cleaned if c.isprintable())

        # Deduplicate (case-sensitive)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            unique_skus.append(cleaned)

    duplicates_removed = len(part_numbers) - len(unique_skus)
    return unique_skus, duplicates_removed


def build_graphql_query(skus: List[str]) -> str:
    """
    Build GraphQL query for searching multiple SKUs.

    Args:
        skus: List of sanitized SKUs to search

    Returns:
        Complete GraphQL query string
    """
    # Build OR-connected SKU filter with escaped quotes for GraphQL
    # Each SKU needs to be wrapped in escaped quotes within the query string
    sku_filters = " OR ".join([f'sku:\\"{sku}\\"' for sku in skus])

    query = f'''query {{
  productVariants(first: {len(skus)}, query: "{sku_filters}") {{
    edges {{
      node {{
        id
        sku
        price
        compareAtPrice
        inventoryQuantity
        product {{
          id
          title
          handle
          status
          descriptionHtml
          vendor
          productType
          tags
          images(first: 5) {{
            edges {{
              node {{
                url
                altText
              }}
            }}
          }}
          metafields(first: 20) {{
            edges {{
              node {{
                namespace
                key
                value
              }}
            }}
          }}
        }}
      }}
    }}
  }}
}}'''
    return query


async def call_shopify_api(query: str) -> Dict[str, Any]:
    """
    Execute GraphQL query against Shopify Admin API.

    Args:
        query: GraphQL query string

    Returns:
        Parsed JSON response from Shopify

    Raises:
        HTTPException: On API errors or network issues
    """
    # Validate credentials
    if not SHOPIFY_STORE_DOMAIN or not SHOPIFY_ADMIN_API_TOKEN:
        logger.error("Shopify credentials not configured")
        raise HTTPException(
            status_code=500,
            detail="Shopify credentials not configured. Check SHOPIFY_STORE_DOMAIN and SHOPIFY_ADMIN_API_TOKEN in .env"
        )

    # Build URL
    url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"

    # Headers
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ADMIN_API_TOKEN,
        "Content-Type": "application/json",
    }

    # Request body
    body = {"query": query}

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(url, json=body, headers=headers)

            # Check HTTP status
            if response.status_code == 401:
                logger.error("Shopify API authentication failed")
                raise HTTPException(
                    status_code=502,
                    detail="Shopify API authentication failed. Check SHOPIFY_ADMIN_API_TOKEN."
                )

            if response.status_code == 429:
                logger.warning("Shopify API rate limit exceeded")
                raise HTTPException(
                    status_code=429,
                    detail="Shopify API rate limit exceeded. Please try again later."
                )

            if response.status_code >= 400:
                logger.error(f"Shopify API error: {response.status_code} - {response.text}")
                raise HTTPException(
                    status_code=502,
                    detail=f"Shopify API error: {response.text}"
                )

            # Parse response
            data = response.json()

            # Check for GraphQL errors
            if "errors" in data:
                logger.error(f"GraphQL errors: {data['errors']}")
                raise HTTPException(
                    status_code=502,
                    detail=f"Shopify GraphQL error: {data['errors']}"
                )

            return data

    except httpx.TimeoutException:
        logger.error("Shopify API request timed out")
        raise HTTPException(
            status_code=504,
            detail="Shopify API request timed out. Please try again."
        )
    except httpx.RequestError as e:
        logger.error(f"Network error calling Shopify API: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Network error connecting to Shopify: {str(e)}"
        )


def parse_variant_response(data: Dict[str, Any]) -> List[FoundProduct]:
    """
    Parse Shopify GraphQL response into FoundProduct models.

    Args:
        data: Raw GraphQL response data

    Returns:
        List of FoundProduct objects
    """
    products: List[FoundProduct] = []

    edges = data.get("data", {}).get("productVariants", {}).get("edges", [])

    for edge in edges:
        node = edge.get("node", {})
        product_data = node.get("product", {})

        # Extract Shopify IDs (remove gid:// prefix)
        variant_gid = node.get("id", "")
        product_gid = product_data.get("id", "")

        # Extract numeric ID from GID (e.g., "gid://shopify/Product/123" -> "123")
        variant_id = variant_gid.split("/")[-1] if variant_gid else ""
        product_id = product_gid.split("/")[-1] if product_gid else ""

        # Extract images
        images: List[ProductImage] = []
        image_edges = product_data.get("images", {}).get("edges", [])
        for img_edge in image_edges:
            img_node = img_edge.get("node", {})
            if img_node.get("url"):
                images.append(ProductImage(
                    url=img_node.get("url"),
                    alt_text=img_node.get("altText")
                ))

        # Extract metafields
        metafields: List[ProductMetafield] = []
        metafield_edges = product_data.get("metafields", {}).get("edges", [])
        for mf_edge in metafield_edges:
            mf_node = mf_edge.get("node", {})
            if mf_node.get("key"):
                metafields.append(ProductMetafield(
                    namespace=mf_node.get("namespace", ""),
                    key=mf_node.get("key", ""),
                    value=mf_node.get("value", "")
                ))

        # Build FoundProduct
        found_product = FoundProduct(
            sku=node.get("sku", ""),
            shopify_product_id=product_id,
            shopify_variant_id=variant_id,
            title=product_data.get("title", ""),
            handle=product_data.get("handle", ""),
            status=product_data.get("status", ""),
            price=node.get("price"),
            compare_at_price=node.get("compareAtPrice"),
            inventory_quantity=node.get("inventoryQuantity"),
            vendor=product_data.get("vendor"),
            product_type=product_data.get("productType"),
            description=product_data.get("descriptionHtml"),
            tags=product_data.get("tags", []),
            images=images,
            metafields=metafields
        )

        products.append(found_product)

    return products


# ==========================================
# SECTION 4: Main Search Function
# ==========================================

async def search_multiple_skus(part_numbers: List[str]) -> MultiPartSearchResponse:
    """
    Search for multiple SKUs in Shopify store.

    This is the main orchestration function that:
    1. Sanitizes and deduplicates input SKUs
    2. Splits into batches of 25 SKUs
    3. Executes GraphQL queries for each batch
    4. Aggregates results and identifies not-found SKUs

    Args:
        part_numbers: List of SKUs to search

    Returns:
        MultiPartSearchResponse with found products and not-found SKUs
    """
    # Step 1: Sanitize and deduplicate
    unique_skus, duplicates_removed = sanitize_skus(part_numbers)

    logger.info(f"Multi-part search started: {len(part_numbers)} requested, {len(unique_skus)} unique")

    # Handle empty input after sanitization
    if not unique_skus:
        return MultiPartSearchResponse(
            success=True,
            found_products=[],
            not_found_skus=[],
            summary=SearchSummary(
                total_requested=len(part_numbers),
                unique_searched=0,
                found=0,
                not_found=0,
                duplicates_removed=duplicates_removed
            ),
            message="No valid SKUs provided after sanitization"
        )

    # Step 2: Split into batches
    batches: List[List[str]] = []
    for i in range(0, len(unique_skus), BATCH_SIZE):
        batches.append(unique_skus[i:i + BATCH_SIZE])

    logger.info(f"Processing {len(batches)} batch(es)")

    # Step 3: Process each batch
    all_found_products: List[FoundProduct] = []

    for batch_index, batch in enumerate(batches):
        # Build and execute query
        query = build_graphql_query(batch)
        response_data = await call_shopify_api(query)

        # Parse results
        batch_products = parse_variant_response(response_data)
        all_found_products.extend(batch_products)

        logger.info(f"Batch {batch_index + 1}/{len(batches)}: found {len(batch_products)} products")

        # Rate limit buffer (skip on last batch)
        if batch_index < len(batches) - 1:
            await asyncio.sleep(DELAY_BETWEEN_BATCHES)

    # Step 4: Identify not-found SKUs
    found_skus: Set[str] = {p.sku for p in all_found_products}
    not_found_skus: List[str] = [sku for sku in unique_skus if sku not in found_skus]

    # Step 5: Build response
    summary = SearchSummary(
        total_requested=len(part_numbers),
        unique_searched=len(unique_skus),
        found=len(all_found_products),
        not_found=len(not_found_skus),
        duplicates_removed=duplicates_removed
    )

    logger.info(f"Multi-part search completed: {summary.found} found, {summary.not_found} not found")

    return MultiPartSearchResponse(
        success=True,
        found_products=all_found_products,
        not_found_skus=not_found_skus,
        summary=summary
    )


# ==========================================
# SECTION 5: Endpoint Definition
# ==========================================

router = APIRouter(tags=["shopify-search"])


@router.post(
    "/api/shopify/multi-part-search",
    response_model=MultiPartSearchResponse,
    summary="Search multiple SKUs in Shopify",
    description="Search for multiple SKUs/part numbers in the Shopify store and return product details."
)
async def multi_part_search(request: MultiPartSearchRequest) -> MultiPartSearchResponse:
    """
    Search for multiple SKUs in Shopify store.

    - **part_numbers**: List of SKUs to search (1-50 items)

    Returns found products with full details and list of SKUs not found.
    """
    return await search_multiple_skus(request.part_numbers)


# ==========================================
# SECTION 6: Router Export
# ==========================================

multi_part_search_router = router
