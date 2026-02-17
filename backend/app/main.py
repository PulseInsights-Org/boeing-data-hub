"""
FastAPI application entry point — mounts routes and middleware.

Boeing Data Hub - FastAPI Application.

Mounts:
- /api/v1/*  – versioned API routes
- /health    – health check (no prefix)
- /api/*     – legacy backward-compat routes (remove after frontend migration)
Version: 1.0.0
"""
import asyncio
import logging
import os
import platform
import signal
import subprocess
import sys
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI

from app.core.config import settings
from app.core.middleware import apply_cors
from app.routes import v1_router, health_router, legacy_router
from app.utils.rate_limiter import get_boeing_rate_limiter
from app.db.sync_store import get_sync_store

logger = logging.getLogger(__name__)

# Track Celery subprocesses for cleanup
_celery_processes: List[subprocess.Popen] = []


def _start_celery_worker() -> Optional[subprocess.Popen]:
    """
    Start Celery worker as a subprocess.

    WARNING: If you also run manual workers in separate terminals,
    set AUTO_START_CELERY=false to prevent this duplicate worker
    from competing for tasks. A duplicate worker whose logs are
    piped (not visible) can silently consume and lose tasks.
    """
    is_windows = platform.system() == "Windows"
    pool_type = "solo" if is_windows else "prefork"

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    cmd = [
        sys.executable, "-m", "celery",
        "-A", "app.celery_app",
        "worker",
        f"--pool={pool_type}",
        "-n", "autostart@%h",
        "-Q", "extraction,normalization,publishing,default,sync_boeing,sync_shopify",
        "-l", "info",
        "--concurrency=2",
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
        logger.info(f"Celery worker 'autostart' started (PID: {process.pid})")
        return process
    except Exception as e:
        logger.error(f"Failed to start Celery worker: {e}")
        return None


def _start_celery_beat() -> Optional[subprocess.Popen]:
    """Start Celery Beat scheduler as a subprocess."""
    is_windows = platform.system() == "Windows"

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    cmd = [
        sys.executable, "-m", "celery",
        "-A", "app.celery_app",
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
    for process in _celery_processes:
        if process and process.poll() is None:
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

    On startup: Start Celery worker/beat, initialize rate limiter, log sync status.
    On shutdown: Stop Celery subprocesses.
    """
    global _celery_processes

    logger.info("=== Boeing Data Hub Starting ===")

    auto_start_celery = settings.auto_start_celery

    if auto_start_celery:
        logger.warning(
            "AUTO_START_CELERY=true — starting embedded worker 'autostart@%h'. "
            "If you also run manual workers in separate terminals, set "
            "AUTO_START_CELERY=false to avoid duplicate task consumption."
        )
        worker_process = _start_celery_worker()
        if worker_process:
            _celery_processes.append(worker_process)

        await asyncio.sleep(2)

        beat_process = _start_celery_beat()
        if beat_process:
            _celery_processes.append(beat_process)

        logger.info(f"Started {len(_celery_processes)} Celery processes")
    else:
        logger.info("Celery auto-start disabled (AUTO_START_CELERY=false)")

    # Initialize rate limiter
    try:
        rate_limiter = get_boeing_rate_limiter()
        status = rate_limiter.get_status()
        logger.info(
            f"Rate limiter initialized: {status['available_tokens']}/{status['capacity']} tokens"
        )
    except Exception as e:
        logger.warning(f"Rate limiter initialization failed (Redis may be unavailable): {e}")

    # Log sync scheduler info
    try:
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

    logger.info("=== Boeing Data Hub Shutting Down ===")

    if _celery_processes:
        _stop_celery_processes()

    logger.info("Shutdown complete")


app = FastAPI(title="Boeing Data Hub Backend", lifespan=lifespan)
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

# Apply middleware
apply_cors(app)

# Mount routers
app.include_router(v1_router)       # /api/v1/*
app.include_router(legacy_router)   # /api/* (backward compat)
app.include_router(health_router)   # /health
