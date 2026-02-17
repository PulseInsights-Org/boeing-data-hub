"""
Legacy routes — backward-compatible redirects for old API paths.

Legacy backward-compatibility routes.

Maps old /api/* paths to new /api/v1/* endpoint handlers so existing
clients (and the frontend during migration) continue to work.

Remove this file once the frontend is fully migrated to /api/v1/*.
Version: 1.0.0
"""
from fastapi import APIRouter

# Import endpoint handlers from new route modules
from app.routes.extraction import extraction_search, bulk_search
from app.routes.publishing import (
    publish_product, bulk_publish, update_product,
    check_sku, setup_metafields,
)
from app.routes.batches import list_batches, get_batch_status, cancel_batch_endpoint
from app.routes.products import (
    get_published_products, get_published_product,
    get_staging_products, get_raw_boeing_data,
)
from app.routes.sync import (
    get_sync_dashboard, get_sync_products, get_sync_history,
    get_failed_products, get_hourly_stats, get_product_sync_status,
    reactivate_product, trigger_immediate_sync,
)
from app.routes.search import multi_part_search
from app.routes.auth import get_me, logout

legacy_router = APIRouter(tags=["legacy (deprecated)"])

# --- Boeing / Extraction ---
legacy_router.add_api_route(
    "/api/boeing/product-search", extraction_search, methods=["GET"],
)
legacy_router.add_api_route(
    "/api/bulk-search", bulk_search, methods=["POST"],
)

# --- Shopify / Publishing ---
legacy_router.add_api_route(
    "/api/shopify/publish", publish_product, methods=["POST"],
)
legacy_router.add_api_route(
    "/api/shopify/products/{shopify_product_id}", update_product, methods=["PUT"],
)
legacy_router.add_api_route(
    "/api/shopify/check", check_sku, methods=["GET"],
)
legacy_router.add_api_route(
    "/api/shopify/metafields/setup", setup_metafields, methods=["POST"],
)
legacy_router.add_api_route(
    "/api/bulk-publish", bulk_publish, methods=["POST"],
)

# --- Shopify / Search ---
legacy_router.add_api_route(
    "/api/shopify/multi-part-search", multi_part_search, methods=["POST"],
)

# --- Batches ---
legacy_router.add_api_route(
    "/api/batches", list_batches, methods=["GET"],
)
legacy_router.add_api_route(
    "/api/batches/{batch_id}", get_batch_status, methods=["GET"],
)
legacy_router.add_api_route(
    "/api/batches/{batch_id}", cancel_batch_endpoint, methods=["DELETE"],
)

# --- Products ---
legacy_router.add_api_route(
    "/api/products/published", get_published_products, methods=["GET"],
)
legacy_router.add_api_route(
    "/api/products/published/{product_id}", get_published_product, methods=["GET"],
)
legacy_router.add_api_route(
    "/api/products/staging", get_staging_products, methods=["GET"],
)
legacy_router.add_api_route(
    "/api/products/raw-data/{part_number}", get_raw_boeing_data, methods=["GET"],
)

# --- Sync ---
legacy_router.add_api_route(
    "/api/sync/dashboard", get_sync_dashboard, methods=["GET"],
)
legacy_router.add_api_route(
    "/api/sync/products", get_sync_products, methods=["GET"],
)
legacy_router.add_api_route(
    "/api/sync/history", get_sync_history, methods=["GET"],
)
legacy_router.add_api_route(
    "/api/sync/failures", get_failed_products, methods=["GET"],
)
legacy_router.add_api_route(
    "/api/sync/hourly-stats", get_hourly_stats, methods=["GET"],
)
legacy_router.add_api_route(
    "/api/sync/product/{sku}", get_product_sync_status, methods=["GET"],
)
legacy_router.add_api_route(
    "/api/sync/product/{sku}/reactivate", reactivate_product, methods=["POST"],
)
legacy_router.add_api_route(
    "/api/sync/trigger/{sku}", trigger_immediate_sync, methods=["POST"],
)

# --- Auth ---
legacy_router.add_api_route(
    "/api/auth/me", get_me, methods=["GET"],
)
legacy_router.add_api_route(
    "/api/auth/logout", logout, methods=["POST"],
)
