"""
Extraction schemas — Boeing search and extraction models.

Extraction pipeline schemas.

Defines response models for Boeing data extraction.
Version: 1.0.0
"""
from typing import List

from pydantic import BaseModel

from .products import NormalizedProduct


class ExtractionSearchResponse(BaseModel):
    products: List[NormalizedProduct]


# Backward-compat alias
BoeingSearchResponse = ExtractionSearchResponse
