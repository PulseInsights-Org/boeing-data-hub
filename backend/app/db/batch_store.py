"""
Batch store for tracking bulk operation progress.

This module provides CRUD operations for the batches table.
All methods are synchronous to simplify Celery task code.
"""
import uuid
import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class BatchStore:
    """
    Store for managing batch operations.

    Provides methods for:
    - Creating new batches
    - Updating progress counters
    - Recording failures
    - Querying batch status
    """

    def __init__(self, settings):
        """
        Initialize BatchStore with Supabase client.

        Args:
            settings: Application settings containing Supabase credentials
        """
        from supabase import create_client
        self.client = create_client(settings.supabase_url, settings.supabase_key)
        self.table = "batches"

    def create_batch(
        self,
        batch_type: str,
        total_items: int,
        idempotency_key: Optional[str] = None,
        celery_task_id: Optional[str] = None,
        user_id: str = "system",
        part_numbers: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Create a new batch record.

        Args:
            batch_type: "search" or "publish"
            total_items: Total number of items to process
            idempotency_key: Optional client-provided key for duplicate prevention
            celery_task_id: Optional Celery task ID for the orchestrator
            user_id: User ID who initiated the batch
            part_numbers: List of part numbers being processed

        Returns:
            dict: Created batch record
        """
        batch_id = str(uuid.uuid4())

        data = {
            "id": batch_id,
            "batch_type": batch_type,
            "status": "pending",
            "total_items": total_items,
            "extracted_count": 0,
            "normalized_count": 0,
            "published_count": 0,
            "failed_count": 0,
            "failed_items": [],
            "failed_part_numbers": [],
            "part_numbers": part_numbers or [],
            "celery_task_id": celery_task_id,
            "idempotency_key": idempotency_key,
            "user_id": user_id,
        }

        result = self.client.table(self.table).insert(data).execute()
        logger.info(f"Created batch {batch_id} (type: {batch_type}, items: {total_items}, user: {user_id})")

        return result.data[0] if result.data else data

    def get_batch(self, batch_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a batch by ID.

        Args:
            batch_id: Batch identifier

        Returns:
            dict or None: Batch record if found
        """
        result = self.client.table(self.table)\
            .select("*")\
            .eq("id", batch_id)\
            .execute()

        return result.data[0] if result.data else None

    def get_batch_by_idempotency_key(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """
        Look up batch by idempotency key.

        Used to prevent duplicate batch creation on client retries.

        Args:
            idempotency_key: Client-provided idempotency key

        Returns:
            dict or None: Existing batch if found
        """
        result = self.client.table(self.table)\
            .select("*")\
            .eq("idempotency_key", idempotency_key)\
            .execute()

        return result.data[0] if result.data else None

    def get_active_batches(self) -> List[Dict[str, Any]]:
        """
        Get all active (pending/processing) batches.

        Returns:
            list: List of active batch records
        """
        result = self.client.table(self.table)\
            .select("*")\
            .in_("status", ["pending", "processing"])\
            .order("created_at", desc=True)\
            .execute()

        return result.data or []

    def update_status(
        self,
        batch_id: str,
        status: str,
        error_message: Optional[str] = None
    ) -> None:
        """
        Update batch status.

        Args:
            batch_id: Batch identifier
            status: New status (pending, processing, completed, failed, cancelled)
            error_message: Optional error message for failed status
        """
        update_data: Dict[str, Any] = {"status": status}

        if error_message:
            update_data["error_message"] = error_message

        # Add completed_at timestamp when status is completed or failed
        if status in ("completed", "failed"):
            from datetime import datetime, timezone
            update_data["completed_at"] = datetime.now(timezone.utc).isoformat()

        self.client.table(self.table)\
            .update(update_data)\
            .eq("id", batch_id)\
            .execute()

        logger.info(f"Updated batch {batch_id} status to {status}")

    def update_batch_type(
        self,
        batch_id: str,
        batch_type: str,
        new_total_items: Optional[int] = None,
        publish_part_numbers: Optional[List[str]] = None
    ) -> None:
        """
        Update the batch type to reflect current pipeline stage.

        Args:
            batch_id: Batch identifier
            batch_type: New batch type (e.g., 'search', 'normalized', 'publishing')
            new_total_items: Optional new total items count (for when publishing fewer items than searched)
            publish_part_numbers: Optional list of part numbers to publish (subset of original part_numbers)
        """
        update_data: Dict[str, Any] = {"batch_type": batch_type}

        if new_total_items is not None:
            update_data["total_items"] = new_total_items

        if publish_part_numbers is not None:
            update_data["publish_part_numbers"] = publish_part_numbers

        self.client.table(self.table).update(update_data).eq("id", batch_id).execute()

        log_msg = f"Updated batch {batch_id} type to {batch_type}"
        if new_total_items is not None:
            log_msg += f", total_items to {new_total_items}"
        if publish_part_numbers is not None:
            log_msg += f", publish_part_numbers count: {len(publish_part_numbers)}"
        logger.info(log_msg)

    def _increment_counter(self, batch_id: str, column: str, count: int = 1) -> None:
        """
        Increment a counter column.

        Note: This uses read-then-update which is not perfectly atomic,
        but is sufficient for progress tracking purposes.

        Args:
            batch_id: Batch identifier
            column: Column name to increment
            count: Number to increment by (default: 1)
        """
        # Get current value
        result = self.client.table(self.table)\
            .select(column)\
            .eq("id", batch_id)\
            .execute()

        if not result.data:
            logger.warning(f"Batch {batch_id} not found for counter increment")
            return

        current_value = result.data[0].get(column, 0) or 0
        new_value = current_value + count

        # Update with new value
        self.client.table(self.table)\
            .update({column: new_value})\
            .eq("id", batch_id)\
            .execute()

    def increment_extracted(self, batch_id: str, count: int = 1) -> None:
        """
        Increment the extracted_count counter.

        Args:
            batch_id: Batch identifier
            count: Number to increment by (default: 1)
        """
        self._increment_counter(batch_id, "extracted_count", count)

    def increment_normalized(self, batch_id: str, count: int = 1) -> None:
        """
        Increment the normalized_count counter.

        Args:
            batch_id: Batch identifier
            count: Number to increment by (default: 1)
        """
        self._increment_counter(batch_id, "normalized_count", count)

    def increment_published(self, batch_id: str, count: int = 1) -> None:
        """
        Increment the published_count counter.

        Args:
            batch_id: Batch identifier
            count: Number to increment by (default: 1)
        """
        self._increment_counter(batch_id, "published_count", count)

    def record_failure(
        self,
        batch_id: str,
        part_number: str,
        error: str
    ) -> None:
        """
        Record a failed item.

        Appends to failed_items JSONB array, failed_part_numbers TEXT[] array,
        and increments failed_count.

        Args:
            batch_id: Batch identifier
            part_number: Part number that failed
            error: Error message describing the failure
        """
        # Get current batch data
        result = self.client.table(self.table)\
            .select("failed_items, failed_part_numbers, failed_count")\
            .eq("id", batch_id)\
            .execute()

        if not result.data:
            logger.warning(f"Batch {batch_id} not found for recording failure")
            return

        batch = result.data[0]
        failed_items = batch.get("failed_items") or []
        failed_part_numbers = batch.get("failed_part_numbers") or []
        failed_count = batch.get("failed_count") or 0

        # Append new failure
        failed_items.append({
            "part_number": part_number,
            "error": error
        })

        # Also track just the part number for easy querying
        if part_number not in failed_part_numbers:
            failed_part_numbers.append(part_number)

        # Update batch
        self.client.table(self.table)\
            .update({
                "failed_items": failed_items,
                "failed_part_numbers": failed_part_numbers,
                "failed_count": failed_count + 1
            })\
            .eq("id", batch_id)\
            .execute()

        logger.warning(f"Recorded failure for {part_number} in batch {batch_id}: {error}")

    def list_batches(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> tuple[List[Dict[str, Any]], int]:
        """
        List batches with pagination.

        Args:
            limit: Maximum number of batches to return
            offset: Number of batches to skip
            status: Optional status filter
            user_id: Optional user ID filter (for user-specific data)

        Returns:
            tuple: (list of batches, total count)
        """
        query = self.client.table(self.table).select("*", count="exact")

        if status:
            query = query.eq("status", status)

        if user_id:
            query = query.eq("user_id", user_id)

        result = query\
            .order("created_at", desc=True)\
            .range(offset, offset + limit - 1)\
            .execute()

        return result.data or [], result.count or 0

    def get_batch_by_user(self, batch_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a batch by ID, verifying user ownership.

        Args:
            batch_id: Batch identifier
            user_id: User ID to verify ownership

        Returns:
            dict or None: Batch record if found and owned by user
        """
        result = self.client.table(self.table)\
            .select("*")\
            .eq("id", batch_id)\
            .eq("user_id", user_id)\
            .execute()

        return result.data[0] if result.data else None
