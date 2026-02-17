"""
Base task class — common retry logic, async helpers, and lazy DI.

Base task class with common functionality.

Provides:
- Automatic dependency injection (using split stores)
- Standardized error handling
- Logging configuration
- Retry logic
Version: 1.0.0
"""
import asyncio
import logging
from celery import Task

logger = logging.getLogger(__name__)


class BaseTask(Task):
    """Base task with common functionality for all workers."""

    # Don't create abstract tasks
    abstract = True

    # Default retry settings.
    # NOTE: Do NOT set autoretry_for here. Each task must explicitly declare
    # which exceptions trigger autoretry. Setting autoretry_for=(Exception,)
    # on the base class causes ALL exceptions to be retried silently, which
    # can mask failures and prevent proper failure recording in task-specific
    # except handlers.
    retry_backoff = True
    retry_backoff_max = 300  # 5 minutes max backoff
    retry_jitter = True
    max_retries = 3

    # Track task state
    track_started = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Called when task fails after all retries exhausted."""
        logger.error(f"Task {self.name}[{task_id}] failed: {exc}")

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        """Called when task is being retried."""
        logger.warning(f"Task {self.name}[{task_id}] retrying (attempt {self.request.retries}): {exc}")

    def on_success(self, retval, task_id, args, kwargs):
        """Called when task succeeds."""
        logger.info(f"Task {self.name}[{task_id}] succeeded")


# ============================================
# Async Helper
# ============================================
def run_async(coro):
    """
    Run async function in sync context.

    Use this to call async methods from Celery tasks.
    Each call creates a new event loop to avoid conflicts.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ============================================
# Dependency helpers (lazy loading, worker-local)
# ============================================
_dependencies = None


def get_dependencies():
    """
    Lazy load dependencies.

    Called after fork so each worker gets own instances.
    This prevents connection sharing issues between workers.
    Uses the new split store architecture.
    """
    global _dependencies
    if _dependencies is None:
        # Lazy imports: circular dependency avoidance
        from app.core.config import settings
        from app.clients.boeing_client import BoeingClient
        from app.clients.shopify_client import ShopifyClient
        from app.clients.supabase_client import SupabaseClient
        from app.db.batch_store import BatchStore
        from app.db.raw_data_store import RawDataStore
        from app.db.staging_store import StagingStore
        from app.db.product_store import ProductStore
        from app.db.image_store import ImageStore
        from app.services.shopify_inventory_service import ShopifyInventoryService
        from app.services.shopify_orchestrator import ShopifyOrchestrator

        shopify_client = ShopifyClient(settings)
        shopify_inventory = ShopifyInventoryService(client=shopify_client, settings=settings)
        shopify_orchestrator = ShopifyOrchestrator(client=shopify_client, inventory=shopify_inventory)
        supabase_client = SupabaseClient(settings)

        _dependencies = {
            "settings": settings,
            "boeing_client": BoeingClient(settings),
            "shopify_client": shopify_client,
            "shopify_inventory": shopify_inventory,
            "shopify_orchestrator": shopify_orchestrator,
            "batch_store": BatchStore(settings),
            "raw_data_store": RawDataStore(supabase_client),
            "staging_store": StagingStore(supabase_client),
            "product_store": ProductStore(supabase_client),
            "image_store": ImageStore(supabase_client),
        }
    return _dependencies


def get_boeing_client():
    """Get Boeing API client instance."""
    return get_dependencies()["boeing_client"]


def get_shopify_client():
    """Get Shopify HTTP client instance."""
    return get_dependencies()["shopify_client"]


def get_shopify_inventory():
    """Get Shopify inventory service instance."""
    return get_dependencies()["shopify_inventory"]


def get_shopify_orchestrator():
    """Get Shopify orchestrator instance."""
    return get_dependencies()["shopify_orchestrator"]


def get_batch_store():
    """Get batch store instance."""
    return get_dependencies()["batch_store"]


def get_raw_data_store():
    """Get raw data store instance."""
    return get_dependencies()["raw_data_store"]


def get_staging_store():
    """Get staging store instance."""
    return get_dependencies()["staging_store"]


def get_product_store():
    """Get product store instance."""
    return get_dependencies()["product_store"]


def get_image_store():
    """Get image store instance."""
    return get_dependencies()["image_store"]


def get_settings():
    """Get settings instance."""
    return get_dependencies()["settings"]
