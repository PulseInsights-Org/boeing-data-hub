"""
Extraction tasks — Boeing product search and data extraction.

Extraction tasks for fetching data from Boeing API.

Tasks:
- process_bulk_search: Orchestrates bulk search by splitting into chunks
- extract_chunk: Extracts a single chunk of part numbers from Boeing API
Version: 1.0.0
"""
import logging
from typing import List, Dict, Any

from app.celery_app.celery_config import celery_app
from app.celery_app.tasks.base import (
    BaseTask,
    run_async,
    get_boeing_client,
    get_raw_data_store,
    get_batch_store,
)
from app.celery_app.tasks.normalization import normalize_chunk
from app.celery_app.tasks.batch import check_batch_completion, reconcile_batch
from app.core.config import settings
from app.core.exceptions import RetryableError, NonRetryableError

logger = logging.getLogger(__name__)

BOEING_BATCH_SIZE = settings.boeing_batch_size


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.extraction.process_bulk_search",
    max_retries=0  # Orchestrator doesn't retry - child tasks do
)
def process_bulk_search(self, batch_id: str, part_numbers: List[str], user_id: str = "system"):
    """
    Process a bulk search request.

    Splits part numbers into chunks and queues extraction tasks.
    """
    logger.info(f"Starting bulk search for batch {batch_id} with {len(part_numbers)} parts (chunk size: {BOEING_BATCH_SIZE}) for user {user_id}")

    batch_store = get_batch_store()

    try:
        batch_store.update_status(batch_id, "processing")

        chunks = [
            part_numbers[i:i + BOEING_BATCH_SIZE]
            for i in range(0, len(part_numbers), BOEING_BATCH_SIZE)
        ]

        logger.info(f"Split into {len(chunks)} chunks of up to {BOEING_BATCH_SIZE} parts each")

        for i, chunk in enumerate(chunks):
            extract_chunk.delay(batch_id, chunk, chunk_index=i, total_chunks=len(chunks), user_id=user_id)

        logger.info(f"Queued {len(chunks)} extraction tasks for batch {batch_id}")

        # Schedule deferred reconciliation as a safety net.
        # If any chunk silently fails (task lost, worker crash, etc.),
        # this will detect the missing parts and record them as failed.
        # Delay = 5 min per chunk (min 5 min, max 30 min).
        reconcile_delay = min(max(len(chunks) * 5 * 60, 300), 1800)
        reconcile_batch.apply_async(args=[batch_id], countdown=reconcile_delay)
        logger.info(f"Scheduled reconciliation for batch {batch_id} in {reconcile_delay}s")

        return {
            "batch_id": batch_id,
            "total_parts": len(part_numbers),
            "chunks_queued": len(chunks),
            "chunk_size": BOEING_BATCH_SIZE
        }

    except Exception as e:
        logger.error(f"Bulk search failed for batch {batch_id}: {e}")
        batch_store.update_status(batch_id, "failed", str(e))
        raise


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.extraction.extract_chunk",
    autoretry_for=(RetryableError, ConnectionError, TimeoutError),
    dont_autoretry_for=(NonRetryableError,),
    retry_backoff=True,
    max_retries=3,
)
def extract_chunk(
    self,
    batch_id: str,
    part_numbers: List[str],
    chunk_index: int = 0,
    total_chunks: int = 1,
    user_id: str = "system"
):
    """Extract a chunk of part numbers from Boeing API."""
    logger.info(
        f"Extracting chunk {chunk_index + 1}/{total_chunks} "
        f"({len(part_numbers)} parts) for batch {batch_id} (user: {user_id})"
    )

    try:
        boeing_client = get_boeing_client()
        raw_data_store = get_raw_data_store()
        batch_store = get_batch_store()

        # Call Boeing API with batch of part numbers
        raw_response = run_async(
            boeing_client.fetch_price_availability_batch(part_numbers)
        )

        # Store raw data for audit trail
        run_async(
            raw_data_store.insert_boeing_raw_data(
                search_query=",".join(part_numbers),
                raw_payload=raw_response,
                user_id=user_id
            )
        )

        # Chain to normalization
        # Note: extracted/normalized/published counts are updated by the
        # trg_update_batch_stats trigger on product_staging automatically.
        normalize_chunk.delay(batch_id, part_numbers, raw_response, user_id)

        logger.info(f"Extraction complete for chunk {chunk_index + 1}/{total_chunks}")

        return {
            "batch_id": batch_id,
            "chunk_index": chunk_index,
            "parts_extracted": len(part_numbers)
        }

    except Exception as e:
        logger.error(f"Extraction failed for chunk {chunk_index + 1}: {e}")

        # Determine if Celery will retry this task
        is_retryable = isinstance(e, (RetryableError, ConnectionError, TimeoutError))
        is_last_attempt = self.request.retries >= self.max_retries

        if not is_retryable or is_last_attempt:
            # Non-retryable error OR final retry exhausted — record failures
            # so these parts are not silently lost
            try:
                _batch_store = get_batch_store()
                for pn in part_numbers:
                    _batch_store.record_failure(batch_id, pn, f"Extraction failed: {e}", stage="extraction")
                check_batch_completion.delay(batch_id)
            except Exception as inner:
                logger.critical(
                    f"CANNOT record failures for batch {batch_id} "
                    f"({len(part_numbers)} parts PERMANENTLY LOST): {inner}"
                )

        raise
