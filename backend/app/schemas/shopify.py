from typing import Optional

from pydantic import BaseModel

from .products import NormalizedProduct


class ShopifyPublishRequest(BaseModel):
    part_number: str


class ShopifyUpdateRequest(NormalizedProduct):
    pass


class ShopifyPublishResponse(BaseModel):
    success: bool
    shopifyProductId: Optional[str] = None


class ShopifyCheckResponse(BaseModel):
    shopifyProductId: Optional[str] = None
