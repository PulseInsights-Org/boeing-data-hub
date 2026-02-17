"""
Celery task exports — all tasks registered from submodules.

Celery tasks package.
Exports all tasks for convenient imports.
Version: 1.0.0
"""
from app.celery_app.tasks.extraction import process_bulk_search, extract_chunk
from app.celery_app.tasks.normalization import normalize_chunk
from app.celery_app.tasks.publishing import publish_batch, publish_product
from app.celery_app.tasks.batch import check_batch_completion, cancel_batch, cleanup_stale_batches

__all__ = [
    "process_bulk_search",
    "extract_chunk",
    "normalize_chunk",
    "publish_batch",
    "publish_product",
    "check_batch_completion",
    "cancel_batch",
    "cleanup_stale_batches",
]
