"""
Batch service — batch creation, status tracking, and completion.

Batch service – bulk search/publish orchestration + staging/raw-data access.

Replaces: inline logic in routes/bulk.py
Version: 1.0.0
"""
import logging
from typing import Any, Dict, List, Optional

from supabase import create_client

from app.db.batch_store import BatchStore
from app.celery_app.tasks.extraction import process_bulk_search
from app.celery_app.tasks.publishing import publish_batch
from app.celery_app.tasks.batch import cancel_batch as cancel_batch_task
from app.core.config import settings

logger = logging.getLogger(__name__)


def calculate_progress(batch: Dict[str, Any]) -> float:
    """Calculate progress percentage based on batch type.

    Extract phase: (extracted + failed) / total — every part is either in
                   product_staging (extracted) or recorded as failed.
    Normalize:     100% — phase is complete by definition (batch_type only
                   transitions to 'normalize' when all parts are accounted for).
    Publish:       (published + failed) / total — each publish task either
                   succeeds or records a failure.
    """
    batch_type = batch["batch_type"]

    if batch_type == "extract":
        total = batch["total_items"]
        if total == 0:
            return 0.0
        completed = batch.get("extracted_count", 0) + batch["failed_count"]
        return round(min((completed / total) * 100, 100), 2)

    elif batch_type == "normalize":
        return 100.0

    else:  # publish
        publish_part_numbers = batch.get("publish_part_numbers") or []
        total = len(publish_part_numbers) if publish_part_numbers else batch["total_items"]
        if total == 0:
            return 0.0
        completed = batch["published_count"] + batch["failed_count"]
        return round(min((completed / total) * 100, 100), 2)


class BatchService:
    def __init__(self, batch_store: BatchStore) -> None:
        self._store = batch_store

    # ------------------------------------------------------------------
    # Bulk search
    # ------------------------------------------------------------------
    def start_bulk_search(
        self,
        part_numbers: List[str],
        user_id: str,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a batch and queue the bulk-search Celery task."""
        if idempotency_key:
            existing = self._store.get_batch_by_idempotency_key(idempotency_key)
            if existing:
                return {
                    "batch_id": existing["id"],
                    "total_items": existing["total_items"],
                    "status": existing["status"],
                    "message": "Returning existing batch (idempotent request)",
                    "idempotency_key": idempotency_key,
                    "is_existing": True,
                }

        batch = self._store.create_batch(
            batch_type="extract",
            total_items=len(part_numbers),
            idempotency_key=idempotency_key,
            user_id=user_id,
            part_numbers=part_numbers,
        )

        task = process_bulk_search.delay(batch["id"], part_numbers, user_id)

        self._store.client.table("batches").update(
            {"celery_task_id": task.id}
        ).eq("id", batch["id"]).execute()

        logger.info(
            f"Started bulk search batch {batch['id']} with "
            f"{len(part_numbers)} parts for user {user_id}"
        )

        return {
            "batch_id": batch["id"],
            "total_items": len(part_numbers),
            "status": "processing",
            "message": f"Bulk search started. Processing {len(part_numbers)} part numbers.",
            "idempotency_key": idempotency_key,
            "is_existing": False,
        }

    # ------------------------------------------------------------------
    # Bulk publish
    # ------------------------------------------------------------------
    def start_bulk_publish(
        self,
        part_numbers: List[str],
        user_id: str,
        batch_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create or reuse a batch and queue the bulk-publish Celery task."""
        if idempotency_key:
            existing = self._store.get_batch_by_idempotency_key(idempotency_key)
            if existing:
                return {
                    "batch_id": existing["id"],
                    "total_items": existing["total_items"],
                    "status": existing["status"],
                    "message": "Returning existing batch (idempotent request)",
                    "idempotency_key": idempotency_key,
                    "is_existing": True,
                }

        if batch_id:
            batch = self._store.get_batch_by_user(batch_id, user_id)
            if not batch:
                return {"error": "Batch not found", "status_code": 404}

            if batch["batch_type"] not in ("normalize", "extract"):
                if batch["batch_type"] == "publish" and batch["status"] == "processing":
                    return {"error": "Publishing already in progress", "status_code": 400}

            publish_count = len(part_numbers)
            self._store.update_batch_type(
                batch_id, "publish",
                new_total_items=publish_count,
                publish_part_numbers=part_numbers,
            )
            self._store.update_status(batch_id, "processing")

            # Reset publish-phase counters only; preserve normalization-phase
            # skipped_count / skipped_part_numbers (set by record_skipped during
            # the normalize stage).
            self._store.client.table("batches").update({
                "published_count": 0, "failed_count": 0, "failed_items": [],
            }).eq("id", batch_id).execute()

            task = publish_batch.delay(batch_id, part_numbers, user_id)
            self._store.client.table("batches").update(
                {"celery_task_id": task.id}
            ).eq("id", batch_id).execute()

            return {
                "batch_id": batch_id,
                "total_items": publish_count,
                "status": "processing",
                "message": f"Publishing started. Processing {publish_count} products.",
                "idempotency_key": idempotency_key,
                "is_existing": False,
            }

        # Standalone publish (no existing batch)
        batch = self._store.create_batch(
            batch_type="publish",
            total_items=len(part_numbers),
            idempotency_key=idempotency_key,
            user_id=user_id,
            part_numbers=part_numbers,
        )

        task = publish_batch.delay(batch["id"], part_numbers, user_id)
        self._store.client.table("batches").update(
            {"celery_task_id": task.id}
        ).eq("id", batch["id"]).execute()

        return {
            "batch_id": batch["id"],
            "total_items": len(part_numbers),
            "status": "processing",
            "message": f"Bulk publish started. Publishing {len(part_numbers)} products.",
            "idempotency_key": idempotency_key,
            "is_existing": False,
        }

    # ------------------------------------------------------------------
    # Batch CRUD
    # ------------------------------------------------------------------
    def list_batches(
        self, user_id: str, limit: int = 50, offset: int = 0, status: Optional[str] = None
    ) -> tuple:
        """Return (list[batch], total_count)."""
        return self._store.list_batches(
            limit=limit, offset=offset, status=status, user_id=user_id
        )

    def get_batch(self, batch_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        return self._store.get_batch_by_user(batch_id, user_id)

    def cancel_batch(self, batch_id: str, user_id: str) -> Dict[str, Any]:
        batch = self._store.get_batch_by_user(batch_id, user_id)
        if not batch:
            return {"error": "Batch not found", "status_code": 404}
        if batch["status"] in ("completed", "failed", "cancelled"):
            return {
                "error": f"Cannot cancel batch with status: {batch['status']}",
                "status_code": 400,
            }

        cancel_batch_task.delay(batch_id)
        return {"message": "Batch cancellation initiated", "batch_id": batch_id}

    # ------------------------------------------------------------------
    # Staging / raw-data queries
    # ------------------------------------------------------------------
    async def get_staging_products(
        self,
        user_id: str,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None,
        batch_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get products from product_staging for the current user."""
        client = create_client(settings.supabase_url, settings.supabase_key)
        query = client.table("product_staging").select("*", count="exact")
        query = query.eq("user_id", user_id)

        if status:
            query = query.eq("status", status)
        if batch_id:
            query = query.eq("batch_id", batch_id)

        result = (
            query.order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
        products = result.data or []
        total = result.count or 0

        return {"products": products, "total": total, "limit": limit, "offset": offset}

    async def get_raw_boeing_data(
        self, part_number: str, user_id: str
    ) -> Dict[str, Any]:
        """Get raw Boeing API data for a specific part number."""
        client = create_client(settings.supabase_url, settings.supabase_key)

        def strip_suffix(pn: str) -> str:
            return pn.split("=")[0] if pn else ""

        search_pn_stripped = strip_suffix(part_number)

        # Fetch recent records metadata
        result = (
            client.table("boeing_raw_data")
            .select("id, search_query, created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )

        if not result.data:
            return {"raw_data": None, "message": "No raw data found for this part number"}

        # Find matching record
        matching_id = None
        matching_query = None
        matching_created = None

        for record in result.data:
            sq = record.get("search_query", "")
            parts = [p.strip() for p in sq.split(",")]
            parts_stripped = [strip_suffix(p) for p in parts]

            if part_number in parts or search_pn_stripped in parts_stripped:
                matching_id = record["id"]
                matching_query = sq
                matching_created = record.get("created_at")
                break

        if not matching_id:
            return {"raw_data": None, "message": "No raw data found for this part number"}

        # Fetch full payload
        full = (
            client.table("boeing_raw_data")
            .select("raw_payload")
            .eq("id", matching_id)
            .single()
            .execute()
        )

        if not full.data:
            return {"raw_data": None, "message": "Failed to fetch raw payload"}

        raw_payload = full.data.get("raw_payload", {})
        line_items = raw_payload.get("lineItems", [])

        matching_item = None
        for item in line_items:
            aviall_pn = item.get("aviallPartNumber") or ""
            if aviall_pn == part_number or strip_suffix(aviall_pn) == search_pn_stripped:
                matching_item = item
                break

        if matching_item:
            return {
                "raw_data": {**matching_item, "currency": raw_payload.get("currency")},
                "search_query": matching_query,
                "fetched_at": matching_created,
            }

        return {
            "raw_data": raw_payload,
            "search_query": matching_query,
            "fetched_at": matching_created,
        }
