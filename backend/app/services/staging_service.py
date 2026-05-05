"""
Staging service — single-row product_staging upsert and status updates.

Serves the SPA edit flow that was previously hitting Supabase directly with
the anon key. The Celery-driven batch normalization path continues to use
StagingStore.upsert_product_staging via NormalizationService.
Version: 1.0.0
"""
from datetime import datetime, timezone
from typing import Any, Dict

from app.db.staging_store import StagingStore


class StagingService:
    """Service-layer entry point for SPA-driven product_staging writes."""

    def __init__(self, staging_store: StagingStore) -> None:
        self._staging_store = staging_store

    async def upsert_product(
        self,
        product_id: str,
        payload: Dict[str, Any],
        user_id: str,
    ) -> Dict[str, Any]:
        """
        Row-level upsert into product_staging keyed on `id`.

        Server-controlled fields (id, user_id, updated_at) are written
        last so a request body cannot spoof tenancy.
        """
        record = {
            **payload,
            "id": product_id,
            "user_id": user_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        return await self._staging_store.upsert_by_id(record)

    async def update_status(
        self,
        product_id: str,
        status: str,
        user_id: str,
    ) -> None:
        """Update the lifecycle status of a single product_staging row."""
        await self._staging_store.update_product_staging_status(
            part_number=product_id,
            status=status,
            user_id=user_id,
        )
