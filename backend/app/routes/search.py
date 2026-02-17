"""
Search routes — multi-part number search across Boeing catalog.

Search pipeline routes.

Provides:
- POST /search/multi-part – multi-SKU search against Shopify
Version: 1.0.0
"""
from fastapi import APIRouter

from app.schemas.search import MultiPartSearchRequest, MultiPartSearchResponse
from app.services.search_service import SearchService

router = APIRouter(prefix="/search", tags=["search"])

_service = SearchService()


@router.post(
    "/multi-part",
    response_model=MultiPartSearchResponse,
    summary="Search multiple SKUs in Shopify",
)
async def multi_part_search(request: MultiPartSearchRequest) -> MultiPartSearchResponse:
    """Search for multiple SKUs in Shopify store."""
    result = await _service.search_multiple_skus(request.part_numbers)
    return MultiPartSearchResponse(**result)
