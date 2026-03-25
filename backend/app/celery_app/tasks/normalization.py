"""
Normalization tasks — transforms raw Boeing data to staging format.

Tasks:
- normalize_chunk: Normalizes a chunk of products from raw Boeing data

Business logic lives in NormalizationService.
Version: 1.1.0
"""
import logging
from typing import List, Dict, Any

from app.celery_app.celery_config import celery_app
from app.celery_app.tasks.base import BaseTask, run_async, get_normalization_service
from app.celery_app.tasks.batch import check_batch_completion
from app.core.exceptions import RetryableError, NonRetryableError

logger = logging.getLogger(__name__)

@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.normalization.normalize_chunk",
    autoretry_for=(RetryableError, ConnectionError, TimeoutError),
    dont_autoretry_for=(NonRetryableError,),
    retry_backoff=True,
    max_retries=3
)
def normalize_chunk(
    self,
    batch_id: str,
    part_numbers: List[str],
    raw_response: Dict[str, Any],
    user_id: str = "system",
):
    """Normalize a chunk of products from raw Boeing data.

    Delegates all normalization and location-blocking logic to
    NormalizationService, then triggers a batch completion check.
    """
    logger.info(
        f"Normalizing {len(part_numbers)} parts for batch {batch_id} (user: {user_id})"
    )

    try:
        svc = get_normalization_service()
        result = run_async(
            svc.normalize_chunk(batch_id, part_numbers, raw_response, user_id)
        )
        check_batch_completion.delay(batch_id)
        return result

    except Exception as e:
        # SAFETY NET: If the service crashes entirely, record ALL parts as
        # failed so they are not silently lost.
        logger.error(
            f"normalize_chunk CRASHED for batch {batch_id} "
            f"({len(part_numbers)} parts): {e}"
        )

        is_retryable = isinstance(e, (RetryableError, ConnectionError, TimeoutError))
        is_last_attempt = self.request.retries >= self.max_retries

        if not is_retryable or is_last_attempt:
            try:
                from app.celery_app.tasks.base import get_batch_store
                _batch_store = get_batch_store()
                for pn in part_numbers:
                    _batch_store.record_failure(
                        batch_id, pn,
                        f"Normalization task crash: {e}",
                        stage="normalization",
                    )
                check_batch_completion.delay(batch_id)
            except Exception as inner:
                logger.critical(
                    f"CANNOT record failures for batch {batch_id} "
                    f"({len(part_numbers)} parts PERMANENTLY LOST): {inner}"
                )
        raise
