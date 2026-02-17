"""
Publishing schemas — Shopify publish and update models.

Publishing pipeline schemas.

Defines request/response models for Shopify publishing operations.
Version: 1.0.0
"""
from typing import Optional

from pydantic import BaseModel

from .products import NormalizedProduct


class PublishRequest(BaseModel):
    part_number: str
    batch_id: Optional[str] = None


class PublishResponse(BaseModel):
    success: bool
    shopifyProductId: Optional[str] = None
    batch_id: Optional[str] = None
    message: Optional[str] = None


class UpdateRequest(NormalizedProduct):
    pass


class CheckResponse(BaseModel):
    shopifyProductId: Optional[str] = None


# Backward-compat aliases
ShopifyPublishRequest = PublishRequest
ShopifyPublishResponse = PublishResponse
ShopifyUpdateRequest = UpdateRequest
ShopifyCheckResponse = CheckResponse
