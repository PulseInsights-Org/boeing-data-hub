"""
Sync Schedule Database Store.

Provides database operations for the product sync scheduler:
- CRUD operations for sync schedules
- Slot distribution queries
- Status tracking and updates
- Retry management

Uses Supabase/PostgreSQL for persistence with the product_sync_schedule table.

Configuration (via settings singleton):
    sync_max_failures: Consecutive failures before marking inactive (default: 5)
    sync_batch_size: Products per Boeing API call (default: 10)
    sync_mode: "production" or "testing"
"""

import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from supabase import Client

from app.core.config import settings
from app.clients.supabase_client import SupabaseClient

logger = logging.getLogger("sync_store")

# Configuration from settings singleton
MAX_CONSECUTIVE_FAILURES = settings.sync_max_failures
MAX_SKUS_PER_SLOT = settings.sync_batch_size
SYNC_MODE = settings.sync_mode
MAX_BUCKETS = settings.sync_max_buckets

# Log sync mode at module load
if SYNC_MODE == "testing":
    logger.info(f"[SYNC_STORE] 🧪 TESTING MODE: Using {MAX_BUCKETS} minute buckets (0-{MAX_BUCKETS - 1})")
else:
    logger.info(f"[SYNC_STORE] 🚀 PRODUCTION MODE: Using {MAX_BUCKETS} hour buckets (0-23)")


class SyncStore:
    """Database operations for product sync scheduling."""

    def __init__(self, supabase_client: Optional[SupabaseClient] = None):
        """
        Initialize the sync store.

        Args:
            supabase_client: Optional SupabaseClient instance (will create default if not provided)
        """
        self._supabase_client = supabase_client

    @property
    def client(self) -> Client:
        """Get or create Supabase client."""
        if self._supabase_client is None:
            self._supabase_client = SupabaseClient(settings)
        return self._supabase_client.client

    def get_total_active_products(self) -> int:
        """
        Get total count of active products in sync schedule.

        Returns:
            Count of active products
        """
        try:
            result = self.client.table("product_sync_schedule") \
                .select("id", count="exact") \
                .eq("is_active", True) \
                .execute()
            return result.count or 0
        except Exception as e:
            logger.error(f"Error getting total active products: {e}")
            return 0

    def get_slot_counts(self) -> Dict[int, int]:
        """
        Get product count per hour slot for active products.

        Returns:
            Dict mapping hour_bucket (0-23) to product count
        """
        try:
            # Query all active products grouped by hour_bucket
            result = self.client.table("product_sync_schedule") \
                .select("hour_bucket") \
                .eq("is_active", True) \
                .execute()

            # Count products per slot
            slot_counts: Dict[int, int] = {}
            for row in result.data:
                slot = row["hour_bucket"]
                slot_counts[slot] = slot_counts.get(slot, 0) + 1

            return slot_counts
        except Exception as e:
            logger.error(f"Error getting slot counts: {e}")
            return {}

    def get_least_loaded_slot(self) -> int:
        """
        Calculate the optimal slot for a new product.

        Uses least-loaded packing algorithm:
        1. Get current total and slot distribution
        2. Calculate minimum slots needed
        3. Return least loaded among active slots

        SYNC MODE:
        - production: Uses hour buckets (0-23)
        - testing: Uses minute buckets (0-5 for 10-min intervals)

        Returns:
            Optimal bucket (0-23 for production, 0-5 for testing)
        """
        slot_counts = self.get_slot_counts()
        total_products = sum(slot_counts.values()) + 1  # +1 for new product

        # Calculate minimum slots needed (respects MAX_BUCKETS based on mode)
        min_slots = min(MAX_BUCKETS, max(1, math.ceil(total_products / MAX_SKUS_PER_SLOT)))

        # Find least loaded slot among the active range
        min_count = float('inf')
        best_slot = 0

        for slot in range(min_slots):
            count = slot_counts.get(slot, 0)
            if count < min_count:
                min_count = count
                best_slot = slot

        logger.debug(
            f"Least-loaded: total={total_products}, min_slots={min_slots}, "
            f"selected={best_slot} (count={min_count})"
        )

        return best_slot

    def create_sync_schedule(
        self,
        sku: str,
        user_id: str,
        initial_price: Optional[float] = None,
        initial_quantity: Optional[int] = None,
        hour_bucket: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Create a new sync schedule for a product.

        Uses least-loaded algorithm if hour_bucket not specified.

        Args:
            sku: Product SKU/part number
            user_id: User ID who owns the product
            initial_price: Initial price from Boeing
            initial_quantity: Initial quantity from Boeing
            hour_bucket: Optional specific slot (uses least-loaded if None)

        Returns:
            Created sync schedule record
        """
        # Use least-loaded algorithm if no specific slot
        if hour_bucket is None:
            hour_bucket = self.get_least_loaded_slot()

        try:
            data = {
                "sku": sku,
                "user_id": user_id,
                "hour_bucket": hour_bucket,
                "sync_status": "pending",
                "last_price": initial_price,
                "last_quantity": initial_quantity,
                "is_active": True,
                "consecutive_failures": 0,
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
        self,
        sku: str,
        user_id: str,
        initial_price: Optional[float] = None,
        initial_quantity: Optional[int] = None,
        shopify_product_id: Optional[str] = None,  # Kept for API compatibility, but not stored
        hour_bucket: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Create or update a sync schedule for a product.

        This is the preferred method for publishing tasks - creates schedule
        for new products or updates existing ones (e.g., when re-publishing).

        Note: shopify_product_id is NOT stored in sync schedule. The sync
        dispatcher looks it up from the product table when needed.

        Args:
            sku: Product SKU/part number (FULL SKU with variant suffix for Boeing API)
            user_id: User ID who owns the product
            initial_price: Initial price from Boeing
            initial_quantity: Initial quantity from Boeing
            shopify_product_id: Ignored (kept for API compatibility)
            hour_bucket: Optional specific slot (uses least-loaded if None for new records)

        Returns:
            Upserted sync schedule record
        """
        try:
            # Check if record already exists
            existing = self.client.table("product_sync_schedule") \
                .select("hour_bucket") \
                .eq("sku", sku) \
                .eq("user_id", user_id) \
                .execute()

            # Use existing slot for updates, or calculate new slot for inserts
            if existing.data:
                # Update existing - keep current slot
                slot = existing.data[0]["hour_bucket"]
            else:
                # New record - use provided slot or calculate
                slot = hour_bucket if hour_bucket is not None else self.get_least_loaded_slot()

            data = {
                "sku": sku,
                "user_id": user_id,
                "hour_bucket": slot,
                "sync_status": "pending",
                "last_price": initial_price,
                "last_quantity": initial_quantity,
                "is_active": True,
                "consecutive_failures": 0,
            }

            # Note: shopify_product_id is NOT stored here - sync dispatcher
            # looks it up from the product table when needed

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
        self,
        hour_bucket: int,
        status_filter: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all products scheduled for a specific hour.

        Args:
            hour_bucket: Hour slot (0-23)
            status_filter: Optional list of statuses to filter by

        Returns:
            List of product sync records
        """
        try:
            query = self.client.table("product_sync_schedule") \
                .select("*") \
                .eq("hour_bucket", hour_bucket) \
                .eq("is_active", True)

            if status_filter:
                query = query.in_("sync_status", status_filter)

            result = query.execute()
            return result.data or []

        except Exception as e:
            logger.error(f"Error getting products for hour {hour_bucket}: {e}")
            return []

    def get_products_by_skus(self, skus: List[str]) -> List[Dict[str, Any]]:
        """
        Get sync records for specific SKUs.

        Args:
            skus: List of SKUs to fetch

        Returns:
            List of matching sync records
        """
        if not skus:
            return []

        try:
            result = self.client.table("product_sync_schedule") \
                .select("*") \
                .in_("sku", skus) \
                .execute()
            return result.data or []

        except Exception as e:
            logger.error(f"Error getting products by SKUs: {e}")
            return []

    def mark_products_syncing(self, skus: List[str]) -> int:
        """
        Mark products as currently syncing.

        Args:
            skus: List of SKUs to mark

        Returns:
            Number of products updated
        """
        if not skus:
            return 0

        try:
            result = self.client.table("product_sync_schedule") \
                .update({"sync_status": "syncing"}) \
                .in_("sku", skus) \
                .execute()

            count = len(result.data) if result.data else 0
            logger.debug(f"Marked {count} products as syncing")
            return count

        except Exception as e:
            logger.error(f"Error marking products as syncing: {e}")
            return 0

    def update_sync_success(
        self,
        sku: str,
        new_hash: str,
        new_price: Optional[float],
        new_quantity: Optional[int],
        inventory_status: Optional[str] = None,
        locations: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """
        Update sync record after successful sync.

        Resets failure counter and updates tracking fields.

        Args:
            sku: Product SKU
            new_hash: Hash of new Boeing data
            new_price: New price from Boeing
            new_quantity: New quantity from Boeing
            inventory_status: "in_stock" or "out_of_stock"
            locations: Location quantities as JSONB list (e.g., [{"location": "Dallas", "quantity": 10}])

        Returns:
            True if update succeeded

        Raises:
            Exception: Re-raises database errors so caller can handle appropriately
        """
        now = datetime.now(timezone.utc).isoformat()

        update_data = {
            "sync_status": "success",
            "last_sync_at": now,
            "last_boeing_hash": new_hash,
            "last_price": new_price,
            "last_quantity": new_quantity,
            "consecutive_failures": 0,
            "last_error": None,
            "updated_at": now,
        }

        # Add new tracking fields if provided
        if inventory_status is not None:
            update_data["last_inventory_status"] = inventory_status

        # Store locations as JSONB (matches database column: last_locations jsonb)
        if locations is not None:
            update_data["last_locations"] = locations

        try:
            result = self.client.table("product_sync_schedule") \
                .update(update_data) \
                .eq("sku", sku) \
                .execute()

            success = bool(result.data)
            if success:
                logger.info(f"Sync success recorded for {sku}: hash={new_hash}, price={new_price}, qty={new_quantity}")
            else:
                logger.warning(f"No rows updated for SKU {sku} - record may not exist in sync schedule")
            return success

        except Exception as e:
            logger.error(f"CRITICAL: Failed to update sync success for {sku}: {e}")
            # Re-raise so caller knows the DB update failed
            raise

    def update_sync_failure(
        self,
        sku: str,
        error_message: str
    ) -> Dict[str, Any]:
        """
        Update sync record after failed sync.

        Increments failure counter. If max failures reached,
        marks product as inactive.

        Args:
            sku: Product SKU
            error_message: Error description

        Returns:
            Dict with is_active status and failure count
        """
        try:
            # First get current failure count
            current = self.client.table("product_sync_schedule") \
                .select("consecutive_failures") \
                .eq("sku", sku) \
                .execute()

            current_failures = 0
            if current.data:
                current_failures = current.data[0].get("consecutive_failures", 0)

            new_failures = current_failures + 1
            is_active = new_failures < MAX_CONSECUTIVE_FAILURES

            now = datetime.now(timezone.utc).isoformat()

            update_data = {
                "sync_status": "failed",
                "consecutive_failures": new_failures,
                "last_error": error_message[:500],  # Truncate long errors
                "is_active": is_active,
                "updated_at": now,
            }

            self.client.table("product_sync_schedule") \
                .update(update_data) \
                .eq("sku", sku) \
                .execute()

            if not is_active:
                logger.warning(
                    f"Product {sku} marked inactive after {new_failures} failures"
                )

            return {
                "sku": sku,
                "consecutive_failures": new_failures,
                "is_active": is_active,
            }

        except Exception as e:
            logger.error(f"Error updating sync failure for {sku}: {e}")
            return {"sku": sku, "error": str(e)}

    def get_failed_products_for_retry(
        self,
        max_failures: int = MAX_CONSECUTIVE_FAILURES,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get failed products that are ready for retry.

        Products are eligible for retry if:
        - Status is 'failed'
        - Still active (not exceeded max failures)
        - Enough time has passed since last attempt (based on backoff)

        Args:
            max_failures: Maximum failures before excluding
            limit: Maximum products to return

        Returns:
            List of products ready for retry
        """
        try:
            # Get all failed but still active products
            result = self.client.table("product_sync_schedule") \
                .select("*") \
                .eq("sync_status", "failed") \
                .eq("is_active", True) \
                .lt("consecutive_failures", max_failures) \
                .limit(limit) \
                .execute()

            return result.data or []

        except Exception as e:
            logger.error(f"Error getting failed products for retry: {e}")
            return []

    def get_stuck_products(
        self,
        stuck_threshold_minutes: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Get products stuck in 'syncing' state.

        Products stuck in 'syncing' for too long may indicate
        a crashed worker. These need to be reset to 'pending'.

        Args:
            stuck_threshold_minutes: Minutes before considering stuck

        Returns:
            List of stuck products
        """
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
        """
        Reset stuck products back to pending status.

        Args:
            stuck_threshold_minutes: Minutes threshold

        Returns:
            Number of products reset
        """
        try:
            threshold = datetime.now(timezone.utc) - timedelta(minutes=stuck_threshold_minutes)
            now = datetime.now(timezone.utc).isoformat()

            result = self.client.table("product_sync_schedule") \
                .update({
                    "sync_status": "pending",
                    "updated_at": now,
                }) \
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
        """
        Get comprehensive slot distribution summary.

        Returns analysis of slots including:
        - Active, filling, dormant slot counts
        - Products per slot
        - Efficiency metrics

        SYNC MODE:
        - production: Analyzes hour buckets (0-23)
        - testing: Analyzes minute buckets (0-5)

        Returns:
            Dict with distribution analysis
        """
        slot_counts = self.get_slot_counts()
        total = sum(slot_counts.values())

        dormant = []
        filling = []
        active = []

        # Use MAX_BUCKETS based on sync mode
        for bucket in range(MAX_BUCKETS):
            count = slot_counts.get(bucket, 0)
            if count == 0:
                dormant.append(bucket)
            elif count < MAX_SKUS_PER_SLOT:
                filling.append(bucket)
            else:
                active.append(bucket)

        # Calculate efficiency
        optimal_slots = max(1, math.ceil(total / MAX_SKUS_PER_SLOT)) if total > 0 else 0
        actual_slots = len(active) + len(filling)
        efficiency = (optimal_slots / actual_slots * 100) if actual_slots > 0 else 100

        return {
            "total_products": total,
            "active_slots": active,
            "filling_slots": filling,
            "dormant_slots": dormant,
            "active_count": len(active),
            "filling_count": len(filling),
            "dormant_count": len(dormant),
            "optimal_slots_needed": optimal_slots,
            "efficiency_percent": round(efficiency, 1),
            "slot_counts": slot_counts,
        }

    def get_sync_status_summary(self) -> Dict[str, Any]:
        """
        Get overall sync system status.

        Returns summary including:
        - Product counts by status
        - Recent failures
        - Sync health metrics

        Returns:
            Dict with status summary
        """
        try:
            # Get counts by status
            all_records = self.client.table("product_sync_schedule") \
                .select("sync_status, is_active, consecutive_failures") \
                .execute()

            status_counts = {
                "pending": 0,
                "syncing": 0,
                "success": 0,
                "failed": 0,
            }
            active_count = 0
            inactive_count = 0
            high_failure_count = 0

            for record in all_records.data or []:
                status = record.get("sync_status", "pending")
                status_counts[status] = status_counts.get(status, 0) + 1

                if record.get("is_active"):
                    active_count += 1
                else:
                    inactive_count += 1

                if record.get("consecutive_failures", 0) >= 3:
                    high_failure_count += 1

            total = sum(status_counts.values())
            success_rate = (
                status_counts["success"] / total * 100
            ) if total > 0 else 0

            return {
                "total_products": total,
                "active_products": active_count,
                "inactive_products": inactive_count,
                "status_counts": status_counts,
                "high_failure_count": high_failure_count,
                "success_rate_percent": round(success_rate, 1),
            }

        except Exception as e:
            logger.error(f"Error getting sync status summary: {e}")
            return {"error": str(e)}

    def reactivate_product(self, sku: str) -> bool:
        """
        Reactivate a product that was marked inactive.

        Resets failure counter and status.

        Args:
            sku: Product SKU to reactivate

        Returns:
            True if successful
        """
        try:
            now = datetime.now(timezone.utc).isoformat()

            result = self.client.table("product_sync_schedule") \
                .update({
                    "is_active": True,
                    "sync_status": "pending",
                    "consecutive_failures": 0,
                    "last_error": None,
                    "updated_at": now,
                }) \
                .eq("sku", sku) \
                .execute()

            success = bool(result.data)
            if success:
                logger.info(f"Reactivated product {sku}")
            return success

        except Exception as e:
            logger.error(f"Error reactivating product {sku}: {e}")
            return False

    def delete_sync_schedule(self, sku: str) -> bool:
        """
        Delete a sync schedule (when product is unpublished).

        Args:
            sku: Product SKU

        Returns:
            True if deleted
        """
        try:
            result = self.client.table("product_sync_schedule") \
                .delete() \
                .eq("sku", sku) \
                .execute()

            deleted = bool(result.data)
            if deleted:
                logger.info(f"Deleted sync schedule for {sku}")
            return deleted

        except Exception as e:
            logger.error(f"Error deleting sync schedule for {sku}: {e}")
            return False


# Singleton instance
_sync_store: Optional[SyncStore] = None


def get_sync_store() -> SyncStore:
    """
    Get or create the singleton SyncStore instance.

    Returns:
        SyncStore instance
    """
    global _sync_store

    if _sync_store is None:
        _sync_store = SyncStore()

    return _sync_store


def reset_sync_store() -> None:
    """Reset the singleton instance (for testing)."""
    global _sync_store
    _sync_store = None
