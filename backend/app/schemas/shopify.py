from typing import Optional

from pydantic import BaseModel

from .products import NormalizedProduct


class ShopifyPublishRequest(BaseModel):
    part_number: str
    batch_id: Optional[str] = None  # If provided, uses existing batch instead of creating new one


class ShopifyUpdateRequest(NormalizedProduct):
    pass


class ShopifyPublishResponse(BaseModel):
    success: bool
    shopifyProductId: Optional[str] = None
    batch_id: Optional[str] = None
    message: Optional[str] = None


class ShopifyCheckResponse(BaseModel):
    shopifyProductId: Optional[str] = None
