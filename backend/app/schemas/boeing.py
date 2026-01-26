from typing import List

from pydantic import BaseModel

from .products import NormalizedProduct


class BoeingSearchResponse(BaseModel):
    products: List[NormalizedProduct]
