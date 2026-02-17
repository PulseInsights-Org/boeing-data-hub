"""
Lazy DI container — singleton access to clients, stores, and services.

Lazy dependency-injection container.

Provides singleton access to all clients, stores, and services.
Works in both FastAPI (async) and Celery (sync) contexts.
Import individual getters to avoid circular imports.
Version: 1.0.0
"""

from functools import lru_cache

from app.core.config import settings
from app.clients.supabase_client import SupabaseClient
from app.clients.shopify_client import ShopifyClient
from app.clients.boeing_client import BoeingClient
from app.db.raw_data_store import RawDataStore
from app.db.staging_store import StagingStore
from app.db.product_store import ProductStore
from app.db.image_store import ImageStore
from app.db.batch_store import BatchStore
from app.db.sync_store import SyncStore
from app.db.sync_analytics import SyncAnalytics
from app.services.shopify_inventory_service import ShopifyInventoryService
from app.services.shopify_orchestrator import ShopifyOrchestrator
from app.clients.gemini_client import GeminiClient
from app.clients.resend_client import ResendClient
from app.services.extraction_service import ExtractionService
from app.services.publishing_service import PublishingService
from app.services.report_service import ReportService
from app.db.report_store import ReportStore


# -- Clients ---------------------------------------------------------------

@lru_cache(maxsize=1)
def get_supabase_client():
    return SupabaseClient(settings)


@lru_cache(maxsize=1)
def get_shopify_client():
    return ShopifyClient(settings)


@lru_cache(maxsize=1)
def get_boeing_client():
    return BoeingClient(settings)


# -- DB Stores -------------------------------------------------------------

@lru_cache(maxsize=1)
def get_raw_data_store():
    return RawDataStore(get_supabase_client())


@lru_cache(maxsize=1)
def get_staging_store():
    return StagingStore(get_supabase_client())


@lru_cache(maxsize=1)
def get_product_store():
    return ProductStore(get_supabase_client())


@lru_cache(maxsize=1)
def get_image_store():
    return ImageStore(get_supabase_client())


@lru_cache(maxsize=1)
def get_batch_store():
    return BatchStore(settings)


@lru_cache(maxsize=1)
def get_sync_store():
    return SyncStore()


@lru_cache(maxsize=1)
def get_sync_analytics():
    return SyncAnalytics()


# -- Shopify Services ------------------------------------------------------

@lru_cache(maxsize=1)
def get_shopify_inventory():
    return ShopifyInventoryService(client=get_shopify_client(), settings=settings)


@lru_cache(maxsize=1)
def get_shopify_orchestrator():
    return ShopifyOrchestrator(client=get_shopify_client(), inventory=get_shopify_inventory())


# -- Pipeline Services -----------------------------------------------------

@lru_cache(maxsize=1)
def get_extraction_service():
    return ExtractionService(
        client=get_boeing_client(),
        raw_store=get_raw_data_store(),
        staging_store=get_staging_store(),
    )


@lru_cache(maxsize=1)
def get_publishing_service():
    return PublishingService(
        shopify=get_shopify_orchestrator(),
        staging_store=get_staging_store(),
        product_store=get_product_store(),
        image_store=get_image_store(),
    )


# -- Report Services -------------------------------------------------------

@lru_cache(maxsize=1)
def get_gemini_client():
    return GeminiClient(
        api_key=settings.gemini_api_key or "",
        model=settings.gemini_model,
    )


@lru_cache(maxsize=1)
def get_resend_client():
    return ResendClient(
        api_key=settings.resend_api_key or "",
        from_address=settings.resend_from_address,
    )


@lru_cache(maxsize=1)
def get_report_store():
    return ReportStore()


@lru_cache(maxsize=1)
def get_report_service():
    return ReportService(
        gemini=get_gemini_client(),
        resend_client=get_resend_client(),
        report_store=get_report_store(),
        supabase_client=get_supabase_client(),
        settings=settings,
    )
