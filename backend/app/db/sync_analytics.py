"""
Sync analytics — dashboard queries for slot distribution and status.

Sync analytics – dashboard/status queries extracted from sync_store.py.
Version: 1.0.0
"""

import logging
import math
from typing import Any, Dict, Optional

from app.core.config import settings
from app.clients.supabase_client import SupabaseClient

logger = logging.getLogger("sync_analytics")

MAX_SKUS_PER_SLOT = settings.sync_batch_size
SYNC_MODE = settings.sync_mode
MAX_BUCKETS = settings.sync_max_buckets


class SyncAnalytics:
    """Read-only analytics queries for the sync dashboard."""

    def __init__(self, supabase_client: Optional[SupabaseClient] = None):
        self._supabase_client = supabase_client

    @property
    def client(self):
        if self._supabase_client is None:
            self._supabase_client = SupabaseClient(settings)
        return self._supabase_client.client

    def get_slot_distribution_summary(self) -> Dict[str, Any]:
        """Get comprehensive slot distribution summary with efficiency metrics."""
        try:
            result = self.client.table("product_sync_schedule") \
                .select("hour_bucket") \
                .eq("is_active", True) \
                .execute()

            slot_counts: Dict[int, int] = {}
            for row in result.data or []:
                slot = row["hour_bucket"]
                slot_counts[slot] = slot_counts.get(slot, 0) + 1
        except Exception as e:
            logger.error(f"Error getting slot counts: {e}")
            slot_counts = {}

        total = sum(slot_counts.values())

        dormant = []
        filling = []
        active = []

        for bucket in range(MAX_BUCKETS):
            count = slot_counts.get(bucket, 0)
            if count == 0:
                dormant.append(bucket)
            elif count < MAX_SKUS_PER_SLOT:
                filling.append(bucket)
            else:
                active.append(bucket)

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
        """Get overall sync system status with product counts by status."""
        try:
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
