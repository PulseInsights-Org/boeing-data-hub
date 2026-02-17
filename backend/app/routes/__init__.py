"""
Route aggregator — mounts all routers under /api/v1 prefix.

Route aggregation module.

Combines all pipeline routers under /api/v1 prefix.
Health and legacy routes are exported separately for main.py to mount at root.
Version: 1.0.0
"""
from fastapi import APIRouter

from app.routes.extraction import router as extraction_router
from app.routes.publishing import router as publishing_router
from app.routes.batches import router as batches_router
from app.routes.products import router as products_router
from app.routes.sync import router as sync_router
from app.routes.search import router as search_router
from app.routes.auth import router as auth_router
from app.routes.reports import router as reports_router
from app.routes.health import router as health_router
from app.routes.legacy import legacy_router

v1_router = APIRouter(prefix="/api/v1")

v1_router.include_router(extraction_router)
v1_router.include_router(publishing_router)
v1_router.include_router(batches_router)
v1_router.include_router(products_router)
v1_router.include_router(sync_router)
v1_router.include_router(search_router)
v1_router.include_router(auth_router)
v1_router.include_router(reports_router)

__all__ = ["v1_router", "health_router", "legacy_router"]
