import logging
import os
import platform
import subprocess
import sys
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.clients.boeing_client import BoeingClient
from app.clients.shopify_client import ShopifyClient
from app.core.config import settings
from app.db.supabase_store import SupabaseStore
from app.routes.auth import router as auth_router
from app.routes.boeing import build_boeing_router
from app.routes.shopify import build_shopify_router
from app.routes.zap import build_zap_router
from app.routes.bulk import router as bulk_router
from app.routes.products import build_products_router
from app.routes.multi_part_search import multi_part_search_router
from app.routes.sync import router as sync_router
from app.services.boeing_service import BoeingService
from app.services.shopify_service import ShopifyService
from app.services.zap_service import ZapService

logger = logging.getLogger(__name__)

# Track Celery subprocesses for cleanup
_celery_processes: List[subprocess.Popen] = []


def _start_celery_worker() -> Optional[subprocess.Popen]:
    """Start Celery worker as a subprocess."""
    is_windows = platform.system() == "Windows"
    pool_type = "solo" if is_windows else "prefork"

    # Get the backend directory (parent of app/)
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    cmd = [
        sys.executable, "-m", "celery",
        "-A", "celery_app",
        "worker",
        f"--pool={pool_type}",
        "-Q", "extraction,normalization,publishing,default,sync_boeing,sync_shopify",
        "-l", "info",
        "--concurrency=2",
    ]

    try:
        # Use CREATE_NEW_PROCESS_GROUP on Windows to allow proper termination
        kwargs = {"cwd": backend_dir}
        if is_windows:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            **kwargs
        )
        logger.info(f"Celery worker started (PID: {process.pid})")
        return process
    except Exception as e:
        logger.error(f"Failed to start Celery worker: {e}")
        return None


def _start_celery_beat() -> Optional[subprocess.Popen]:
    """Start Celery Beat scheduler as a subprocess."""
    is_windows = platform.system() == "Windows"

    # Get the backend directory
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    cmd = [
        sys.executable, "-m", "celery",
        "-A", "celery_app",
        "beat",
        "-l", "info",
    ]

    try:
        kwargs = {"cwd": backend_dir}
        if is_windows:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            **kwargs
        )
        logger.info(f"Celery Beat started (PID: {process.pid})")
        return process
    except Exception as e:
        logger.error(f"Failed to start Celery Beat: {e}")
        return None


def _stop_celery_processes():
    """Stop all Celery subprocesses."""
    import signal

    for process in _celery_processes:
        if process and process.poll() is None:  # Still running
            try:
                logger.info(f"Stopping Celery process (PID: {process.pid})...")
                if platform.system() == "Windows":
                    process.terminate()
                else:
                    process.send_signal(signal.SIGTERM)
                process.wait(timeout=10)
                logger.info(f"Celery process {process.pid} stopped")
            except subprocess.TimeoutExpired:
                logger.warning(f"Force killing Celery process {process.pid}")
                process.kill()
            except Exception as e:
                logger.error(f"Error stopping Celery process: {e}")

    _celery_processes.clear()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan event handler.

    On startup:
    - Start Celery worker subprocess
    - Start Celery Beat scheduler subprocess
    - Initialize Boeing rate limiter
    - Verify Redis connection

    On shutdown:
    - Stop Celery subprocesses
    - Cleanup resources
    """
    global _celery_processes

    # Startup
    logger.info("=== Boeing Data Hub Starting ===")

    # Check if Celery auto-start is enabled (default: True)
    auto_start_celery = os.getenv("AUTO_START_CELERY", "true").lower() == "true"

    if auto_start_celery:
        # Start Celery worker
        worker_process = _start_celery_worker()
        if worker_process:
            _celery_processes.append(worker_process)

        # Small delay before starting beat
        import asyncio
        await asyncio.sleep(2)

        # Start Celery Beat
        beat_process = _start_celery_beat()
        if beat_process:
            _celery_processes.append(beat_process)

        logger.info(f"Started {len(_celery_processes)} Celery processes")
    else:
        logger.info("Celery auto-start disabled (AUTO_START_CELERY=false)")

    # Initialize rate limiter (verifies Redis connection)
    try:
        from app.utils.rate_limiter import get_boeing_rate_limiter
        rate_limiter = get_boeing_rate_limiter()
        status = rate_limiter.get_status()
        logger.info(
            f"Rate limiter initialized: {status['available_tokens']}/{status['capacity']} tokens"
        )
    except Exception as e:
        logger.warning(f"Rate limiter initialization failed (Redis may be unavailable): {e}")

    # Log sync scheduler info
    try:
        from app.db.sync_store import get_sync_store
        sync_store = get_sync_store()
        summary = sync_store.get_sync_status_summary()
        logger.info(
            f"Sync scheduler: {summary.get('total_products', 0)} products, "
            f"{summary.get('active_products', 0)} active"
        )
    except Exception as e:
        logger.warning(f"Could not get sync status: {e}")

    logger.info("=== Boeing Data Hub Ready ===")

    yield

    # Shutdown
    logger.info("=== Boeing Data Hub Shutting Down ===")

    # Stop Celery processes
    if _celery_processes:
        _stop_celery_processes()

    logger.info("Shutdown complete")


app = FastAPI(title="Boeing Data Hub Backend", lifespan=lifespan)
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

store = SupabaseStore(settings)
boeing_client = BoeingClient(settings)
shopify_client = ShopifyClient(settings)

boeing_service = BoeingService(boeing_client, store)
shopify_service = ShopifyService(shopify_client, store)
zap_service = ZapService(shopify_client, boeing_service, store)

app.include_router(auth_router)
app.include_router(build_boeing_router(boeing_service))
app.include_router(build_shopify_router(shopify_service))
app.include_router(build_zap_router(zap_service))
app.include_router(bulk_router)
app.include_router(build_products_router(store))
app.include_router(multi_part_search_router)
app.include_router(sync_router)

@app.get("/health")
async def health_check():
    """Basic health check endpoint."""
    return {"status": "healthy"}


@app.get("/sync/status")
async def sync_scheduler_status():
    """
    Get sync scheduler status including rate limiter and slot distribution.

    Returns overview of:
    - Celery process status
    - Rate limiter state (tokens available)
    - Product sync status counts
    - Slot distribution efficiency
    """
    from app.utils.rate_limiter import get_boeing_rate_limiter
    from app.db.sync_store import get_sync_store

    result = {}

    # Celery process status
    celery_status = []
    for i, proc in enumerate(_celery_processes):
        proc_type = "worker" if i == 0 else "beat"
        if proc and proc.poll() is None:
            celery_status.append({"type": proc_type, "pid": proc.pid, "status": "running"})
        elif proc:
            celery_status.append({"type": proc_type, "pid": proc.pid, "status": "stopped", "exit_code": proc.returncode})
    result["celery_processes"] = celery_status

    # Rate limiter status
    try:
        rate_limiter = get_boeing_rate_limiter()
        result["rate_limiter"] = rate_limiter.get_status()
    except Exception as e:
        result["rate_limiter"] = {"error": str(e)}

    # Sync status
    try:
        sync_store = get_sync_store()
        result["sync_status"] = sync_store.get_sync_status_summary()
        result["slot_distribution"] = sync_store.get_slot_distribution_summary()
    except Exception as e:
        result["sync_status"] = {"error": str(e)}

    return result


@app.post("/sync/trigger/{sku}")
async def trigger_immediate_sync(sku: str, user_id: str = "system"):
    """
    Trigger an immediate sync for a specific product.

    Bypasses the hourly scheduler for on-demand syncing.
    Still respects rate limits.
    """
    from celery_app.tasks.sync_dispatcher import sync_single_product_immediate

    sync_single_product_immediate.delay(sku, user_id)

    return {
        "status": "queued",
        "sku": sku,
        "message": "Sync job queued. Will process when rate limiter allows."
    }

