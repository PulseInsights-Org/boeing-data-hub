"""
Search schemas — multi-part search request/response models.

Search pipeline schemas.

Defines request/response models for multi-part SKU search.
Version: 1.0.0
"""
from typing import List, Optional

from pydantic import BaseModel, Field


MAX_SKUS_ALLOWED = 50


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
