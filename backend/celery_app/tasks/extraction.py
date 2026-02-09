"""
Extraction tasks for fetching data from Boeing API.

Tasks:
- process_bulk_search: Orchestrates bulk search by splitting into chunks
- extract_chunk: Extracts a single chunk of part numbers from Boeing API
"""
import logging
import os
from typing import List, Dict, Any

from celery_app.celery_config import celery_app
from celery_app.tasks.base import (
    BaseTask,
    run_async,
    get_boeing_client,
    get_supabase_store,
    get_batch_store
)
from app.core.exceptions import RetryableError, NonRetryableError

logger = logging.getLogger(__name__)

# Configurable chunk size - start with 10 for safety, tune via env var
BOEING_BATCH_SIZE = int(os.getenv("BOEING_BATCH_SIZE", "10"))


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="celery_app.tasks.extraction.process_bulk_search",
    max_retries=0  # Orchestrator doesn't retry - child tasks do
)
def process_bulk_search(self, batch_id: str, part_numbers: List[str], user_id: str = "system"):
    """
    Process a bulk search request.

    Splits part numbers into chunks and queues extraction tasks.
    This is the main orchestrator task for bulk searches.

    Args:
        batch_id: Batch identifier for tracking
        part_numbers: List of part numbers to search
        user_id: User ID who initiated the search

    Returns:
        dict: Summary of queued tasks
    """
    logger.info(f"Starting bulk search for batch {batch_id} with {len(part_numbers)} parts (chunk size: {BOEING_BATCH_SIZE}) for user {user_id}")

    batch_store = get_batch_store()

    try:
        # Update status to processing
        batch_store.update_status(batch_id, "processing")

        # Split into chunks using configurable size
        chunks = [
            part_numbers[i:i + BOEING_BATCH_SIZE]
            for i in range(0, len(part_numbers), BOEING_BATCH_SIZE)
        ]

        logger.info(f"Split into {len(chunks)} chunks of up to {BOEING_BATCH_SIZE} parts each")

        # Queue extraction for each chunk with user_id
        for i, chunk in enumerate(chunks):
            extract_chunk.delay(batch_id, chunk, chunk_index=i, total_chunks=len(chunks), user_id=user_id)

        logger.info(f"Queued {len(chunks)} extraction tasks for batch {batch_id}")

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
    name="celery_app.tasks.extraction.extract_chunk",
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
    """
    Extract a chunk of part numbers from Boeing API.

    Args:
        batch_id: Batch identifier
        part_numbers: Part numbers to extract (max BOEING_BATCH_SIZE)
        chunk_index: Current chunk index (for logging)
        total_chunks: Total number of chunks (for logging)
        user_id: User ID who initiated the search

    Returns:
        dict: Summary of extraction results
    """
    logger.info(
        f"Extracting chunk {chunk_index + 1}/{total_chunks} "
        f"({len(part_numbers)} parts) for batch {batch_id} (user: {user_id})"
    )

    boeing_client = get_boeing_client()
    supabase_store = get_supabase_store()
    batch_store = get_batch_store()

    try:
        # Call Boeing API with batch of part numbers
        raw_response = run_async(
            boeing_client.fetch_price_availability_batch(part_numbers)
        )

        # Store raw data for audit trail with user_id
        run_async(
            supabase_store.insert_boeing_raw_data(
                search_query=",".join(part_numbers),
                raw_payload=raw_response,
                user_id=user_id
            )
        )

        # Note: extracted_count is updated by database trigger on boeing_raw_data insert

        # Chain to normalization with user_id
        from celery_app.tasks.normalization import normalize_chunk
        normalize_chunk.delay(batch_id, part_numbers, raw_response, user_id)

        logger.info(f"Extraction complete for chunk {chunk_index + 1}/{total_chunks}")

        return {
            "batch_id": batch_id,
            "chunk_index": chunk_index,
            "parts_extracted": len(part_numbers)
        }

    except Exception as e:
        logger.error(f"Extraction failed for chunk {chunk_index + 1}: {e}")

        # If max retries exceeded, record failures for each part number
        if self.request.retries >= self.max_retries:
            for pn in part_numbers:
                batch_store.record_failure(batch_id, pn, f"Extraction failed: {e}")

            # Check if batch should be marked complete
            from celery_app.tasks.batch import check_batch_completion
            check_batch_completion.delay(batch_id)

        raise
