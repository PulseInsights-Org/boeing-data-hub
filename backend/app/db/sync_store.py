"""
Sync store — product sync schedule CRUD and status management.

Sync Schedule Database Store – CRUD operations for product_sync_schedule.
Version: 1.0.0
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from supabase import Client

from app.core.config import settings
from app.clients.supabase_client import SupabaseClient
from app.db.sync_analytics import SyncAnalytics

logger = logging.getLogger("sync_store")

MAX_CONSECUTIVE_FAILURES = settings.sync_max_failures
MAX_SKUS_PER_SLOT = settings.sync_batch_size
SYNC_MODE = settings.sync_mode
MAX_BUCKETS = settings.sync_max_buckets


class SyncStore:
    """CRUD operations for product sync scheduling."""

    def __init__(self, supabase_client: Optional[SupabaseClient] = None):
        self._supabase_client = supabase_client

    @property
    def client(self) -> Client:
        """Get or create Supabase client."""
        if self._supabase_client is None:
            self._supabase_client = SupabaseClient(settings)
        return self._supabase_client.client

    def get_total_active_products(self) -> int:
        """Count of active products in sync schedule."""
        try:
            result = self.client.table("product_sync_schedule") \
                .select("id", count="exact") \
                .eq("is_active", True) \
                .execute()
            return result.count or 0
        except Exception as e:
            logger.error(f"Error getting total active products: {e}")
            return 0

    def get_syncing_count(self) -> int:
        """Count products currently in 'syncing' state."""
        try:
            result = self.client.table("product_sync_schedule") \
                .select("id", count="exact") \
                .eq("sync_status", "syncing") \
                .eq("is_active", True) \
                .execute()
            return result.count or 0
        except Exception as e:
            logger.error(f"Error getting syncing count: {e}")
            return 0

    def get_slot_counts(self) -> Dict[int, int]:
        """Product count per hour slot for active products."""
        try:
            result = self.client.table("product_sync_schedule") \
                .select("hour_bucket") \
                .eq("is_active", True) \
                .execute()

            slot_counts: Dict[int, int] = {}
            for row in result.data:
                slot = row["hour_bucket"]
                slot_counts[slot] = slot_counts.get(slot, 0) + 1
            return slot_counts
        except Exception as e:
            logger.error(f"Error getting slot counts: {e}")
            return {}

    def get_least_loaded_slot(self) -> int:
        """Calculate optimal slot for a new product.

        Uses the multiples-of-batch-size algorithm from sync_helpers so
        that buckets fill in clean groups of 10 before spilling to the next.
        Falls back to the legacy even-distribution logic if the import fails.
        """
        from app.utils.slot_manager import get_optimal_slot

        slot_counts = self.get_slot_counts()
        return get_optimal_slot(slot_counts)

    def create_sync_schedule(
        self, sku: str, user_id: str,
        initial_price: Optional[float] = None,
        initial_quantity: Optional[int] = None,
        hour_bucket: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create a new sync schedule for a product."""
        if hour_bucket is None:
            hour_bucket = self.get_least_loaded_slot()

        try:
            data = {
                "sku": sku, "user_id": user_id,
                "hour_bucket": hour_bucket, "sync_status": "pending",
                "last_price": initial_price, "last_quantity": initial_quantity,
                "is_active": True, "consecutive_failures": 0,
            }
            result = self.client.table("product_sync_schedule") \
                .upsert(data, on_conflict="user_id,sku") \
                .execute()
            logger.info(f"Created sync schedule: SKU={sku}, slot={hour_bucket}")
            return result.data[0] if result.data else data
        except Exception as e:
            logger.error(f"Error creating sync schedule for {sku}: {e}")
            raise

    def upsert_sync_schedule(
        self, sku: str, user_id: str,
        initial_price: Optional[float] = None,
        initial_quantity: Optional[int] = None,
        shopify_product_id: Optional[str] = None,
        hour_bucket: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create or update a sync schedule (idempotent)."""
        try:
            existing = self.client.table("product_sync_schedule") \
                .select("hour_bucket") \
                .eq("sku", sku).eq("user_id", user_id) \
                .execute()

            if existing.data:
                slot = existing.data[0]["hour_bucket"]
            else:
                slot = hour_bucket if hour_bucket is not None else self.get_least_loaded_slot()

            data = {
                "sku": sku, "user_id": user_id,
                "hour_bucket": slot, "sync_status": "pending",
                "last_price": initial_price, "last_quantity": initial_quantity,
                "is_active": True, "consecutive_failures": 0,
            }
            result = self.client.table("product_sync_schedule") \
                .upsert(data, on_conflict="user_id,sku") \
                .execute()

            action = "Updated" if existing.data else "Created"
            logger.info(f"{action} sync schedule: SKU={sku}, slot={slot}")
            return result.data[0] if result.data else data
        except Exception as e:
            logger.error(f"Error upserting sync schedule for {sku}: {e}")
            raise

    def get_products_for_hour(
        self, hour_bucket: int, status_filter: Optional[List[str]] = None,
        window_start: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Get all active products scheduled for a specific hour.

        Args:
            hour_bucket: The bucket number to query.
            status_filter: Optional list of sync_status values to include.
            window_start: If provided, exclude products whose last_sync_at
                          is >= this timestamp (already synced in current window).
                          Uses the existing idx_sync_hourly_dispatch index.
        """
        try:
            query = self.client.table("product_sync_schedule") \
                .select("*") \
                .eq("hour_bucket", hour_bucket) \
                .eq("is_active", True)
            if status_filter:
                query = query.in_("sync_status", status_filter)
            if window_start:
                window_iso = window_start.isoformat()
                query = query.or_(f"last_sync_at.is.null,last_sync_at.lt.{window_iso}")
            return query.execute().data or []
        except Exception as e:
            logger.error(f"Error getting products for hour {hour_bucket}: {e}")
            return []

    def get_products_by_skus(self, skus: List[str]) -> List[Dict[str, Any]]:
        """Get sync records for specific SKUs."""
        if not skus:
            return []
        try:
            result = self.client.table("product_sync_schedule") \
                .select("*").in_("sku", skus).execute()
            return result.data or []
        except Exception as e:
            logger.error(f"Error getting products by SKUs: {e}")
            return []

    def mark_products_syncing(self, skus: List[str]) -> int:
        """Mark products as currently syncing."""
        if not skus:
            return 0
        try:
            result = self.client.table("product_sync_schedule") \
                .update({"sync_status": "syncing"}) \
                .in_("sku", skus).execute()
            return len(result.data) if result.data else 0
        except Exception as e:
            logger.error(f"Error marking products as syncing: {e}")
            return 0

    def update_sync_success(
        self, sku: str, new_hash: str,
        new_price: Optional[float], new_quantity: Optional[int],
        inventory_status: Optional[str] = None,
        locations: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """Update sync record after successful sync."""
        now = datetime.now(timezone.utc).isoformat()
        update_data = {
            "sync_status": "success", "last_sync_at": now,
            "last_boeing_hash": new_hash, "last_price": new_price,
            "last_quantity": new_quantity, "consecutive_failures": 0,
            "last_error": None, "updated_at": now,
        }
        if inventory_status is not None:
            update_data["last_inventory_status"] = inventory_status
        if locations is not None:
            update_data["last_locations"] = locations

        try:
            result = self.client.table("product_sync_schedule") \
                .update(update_data).eq("sku", sku).execute()
            success = bool(result.data)
            if success:
                logger.info(f"Sync success: {sku} hash={new_hash} price={new_price} qty={new_quantity}")
            else:
                logger.warning(f"No rows updated for SKU {sku}")
            return success
        except Exception as e:
            logger.error(f"CRITICAL: Failed to update sync success for {sku}: {e}")
            raise

    def update_sync_failure(self, sku: str, error_message: str) -> Dict[str, Any]:
        """Update sync record after failed sync. Deactivates after max failures."""
        try:
            current = self.client.table("product_sync_schedule") \
                .select("consecutive_failures").eq("sku", sku).execute()
            current_failures = current.data[0].get("consecutive_failures", 0) if current.data else 0
            new_failures = current_failures + 1
            is_active = new_failures < MAX_CONSECUTIVE_FAILURES
            now = datetime.now(timezone.utc).isoformat()

            self.client.table("product_sync_schedule") \
                .update({
                    "sync_status": "failed", "consecutive_failures": new_failures,
                    "last_error": error_message[:500], "is_active": is_active,
                    "updated_at": now,
                }).eq("sku", sku).execute()

            if not is_active:
                logger.warning(f"Product {sku} marked inactive after {new_failures} failures")
            return {"sku": sku, "consecutive_failures": new_failures, "is_active": is_active}
        except Exception as e:
            logger.error(f"Error updating sync failure for {sku}: {e}")
            return {"sku": sku, "error": str(e)}

    def get_failed_products_for_retry(
        self, max_failures: int = MAX_CONSECUTIVE_FAILURES, limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get failed but still active products eligible for retry."""
        try:
            result = self.client.table("product_sync_schedule") \
                .select("*") \
                .eq("sync_status", "failed").eq("is_active", True) \
                .lt("consecutive_failures", max_failures) \
                .limit(limit).execute()
            return result.data or []
        except Exception as e:
            logger.error(f"Error getting failed products for retry: {e}")
            return []

    def get_stuck_products(self, stuck_threshold_minutes: int = 30) -> List[Dict[str, Any]]:
        """Get products stuck in 'syncing' state beyond threshold."""
        try:
            threshold = datetime.now(timezone.utc) - timedelta(minutes=stuck_threshold_minutes)
            result = self.client.table("product_sync_schedule") \
                .select("*") \
                .eq("sync_status", "syncing") \
                .lt("updated_at", threshold.isoformat()) \
                .execute()
            return result.data or []
        except Exception as e:
            logger.error(f"Error getting stuck products: {e}")
            return []

    def reset_stuck_products(self, stuck_threshold_minutes: int = 30) -> int:
        """Reset stuck products back to pending status."""
        try:
            threshold = datetime.now(timezone.utc) - timedelta(minutes=stuck_threshold_minutes)
            now = datetime.now(timezone.utc).isoformat()
            result = self.client.table("product_sync_schedule") \
                .update({"sync_status": "pending", "updated_at": now}) \
                .eq("sync_status", "syncing") \
                .lt("updated_at", threshold.isoformat()) \
                .execute()
            count = len(result.data) if result.data else 0
            if count > 0:
                logger.info(f"Reset {count} stuck products to pending")
            return count
        except Exception as e:
            logger.error(f"Error resetting stuck products: {e}")
            return 0

    def get_slot_distribution_summary(self) -> Dict[str, Any]:
        """Delegate to SyncAnalytics for dashboard queries."""
        return SyncAnalytics(self._supabase_client).get_slot_distribution_summary()

    def get_sync_status_summary(self) -> Dict[str, Any]:
        """Delegate to SyncAnalytics for dashboard queries."""
        return SyncAnalytics(self._supabase_client).get_sync_status_summary()

    def reactivate_product(self, sku: str) -> bool:
        """Reactivate a product that was marked inactive due to failures."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            result = self.client.table("product_sync_schedule") \
                .update({
                    "is_active": True, "sync_status": "pending",
                    "consecutive_failures": 0, "last_error": None,
                    "updated_at": now,
                }).eq("sku", sku).execute()
            success = bool(result.data)
            if success:
                logger.info(f"Reactivated product {sku}")
            return success
        except Exception as e:
            logger.error(f"Error reactivating product {sku}: {e}")
            return False

    def delete_sync_schedule(self, sku: str) -> bool:
        """Delete a sync schedule (when product is unpublished)."""
        try:
            result = self.client.table("product_sync_schedule") \
                .delete().eq("sku", sku).execute()
            deleted = bool(result.data)
            if deleted:
                logger.info(f"Deleted sync schedule for {sku}")
            return deleted
        except Exception as e:
            logger.error(f"Error deleting sync schedule for {sku}: {e}")
            return False


_sync_store: Optional[SyncStore] = None


def get_sync_store() -> SyncStore:
    """Get or create the singleton SyncStore instance."""
    global _sync_store
    if _sync_store is None:
        _sync_store = SyncStore()
    return _sync_store


def reset_sync_store() -> None:
    """Reset the singleton instance (for testing)."""
    global _sync_store
    _sync_store = None
