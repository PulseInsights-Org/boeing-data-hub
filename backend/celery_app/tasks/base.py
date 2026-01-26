"""
Base task class with common functionality.

Provides:
- Automatic dependency injection
- Standardized error handling
- Logging configuration
- Retry logic
"""
import asyncio
import logging
from celery import Task

logger = logging.getLogger(__name__)


class BaseTask(Task):
    """Base task with common functionality for all workers."""

    # Don't create abstract tasks
    abstract = True

    # Default retry settings
    autoretry_for = (Exception,)
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
# Dependency helpers (lazy loading)
# ============================================
_dependencies = None


def get_dependencies():
    """
    Lazy load dependencies.

    Called after fork so each worker gets own instances.
    This prevents connection sharing issues between workers.
    """
    global _dependencies
    if _dependencies is None:
        from app.core.config import settings
        from app.clients.boeing_client import BoeingClient
        from app.clients.shopify_client import ShopifyClient
        from app.db.supabase_store import SupabaseStore
        from app.db.batch_store import BatchStore

        _dependencies = {
            "settings": settings,
            "boeing_client": BoeingClient(settings),
            "shopify_client": ShopifyClient(settings),
            "supabase_store": SupabaseStore(settings),
            "batch_store": BatchStore(settings),
        }
    return _dependencies


def get_boeing_client():
    """Get Boeing API client instance."""
    return get_dependencies()["boeing_client"]


def get_shopify_client():
    """Get Shopify API client instance."""
    return get_dependencies()["shopify_client"]


def get_supabase_store():
    """Get Supabase store instance."""
    return get_dependencies()["supabase_store"]


def get_batch_store():
    """Get batch store instance."""
    return get_dependencies()["batch_store"]
