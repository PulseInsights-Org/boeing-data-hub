"""
Sync routes — sync dashboard, schedule management, manual triggers.

Sync pipeline routes – Auto-Sync dashboard, history, failures, triggers.
Version: 1.0.0
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query, Depends
from supabase import create_client

from app.schemas.sync import (
    SyncStatusCounts, SlotInfo, SyncDashboardResponse,
    SyncProduct, SyncProductsResponse,
    SyncHistoryItem, SyncHistoryResponse,
    FailedProduct, FailedProductsResponse,
    HourlyStats, HourlyStatsResponse,
)
from app.core.auth import get_current_user
from app.core.config import settings
from app.db.sync_store import get_sync_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sync", tags=["sync"])


def _get_client():
    return create_client(settings.supabase_url, settings.supabase_key)


@router.get("/dashboard", response_model=SyncDashboardResponse)
async def get_sync_dashboard(current_user: dict = Depends(get_current_user)):
    """Get complete sync dashboard data."""
    sync_store = get_sync_store()

    try:
        status_summary = sync_store.get_sync_status_summary()
        slot_distribution = sync_store.get_slot_distribution_summary()
        slot_counts = slot_distribution.get("slot_counts", {})

        now = datetime.now(timezone.utc)
        if settings.sync_mode == "testing":
            current_hour = now.minute // 10
        else:
            current_hour = now.hour

        current_hour_products = slot_counts.get(current_hour, 0)

        max_buckets = settings.sync_max_buckets
        slot_info_list = []

        active_slots = slot_distribution.get("active_slots", [])
        filling_slots = slot_distribution.get("filling_slots", [])

        for hour in range(max_buckets):
            count = slot_counts.get(hour, 0)
            if hour in active_slots:
                status = "active"
            elif hour in filling_slots:
                status = "filling"
            else:
                status = "dormant"

            slot_info_list.append(SlotInfo(
                hour=hour,
                count=count,
                status=status
            ))

        status_counts_data = status_summary.get("status_counts", {})

        return SyncDashboardResponse(
            total_products=status_summary.get("total_products", 0),
            active_products=status_summary.get("active_products", 0),
            inactive_products=status_summary.get("inactive_products", 0),
            success_rate_percent=status_summary.get("success_rate_percent", 0),
            high_failure_count=status_summary.get("high_failure_count", 0),
            status_counts=SyncStatusCounts(
                pending=status_counts_data.get("pending", 0),
                syncing=status_counts_data.get("syncing", 0),
                success=status_counts_data.get("success", 0),
                failed=status_counts_data.get("failed", 0),
            ),
            current_hour=current_hour,
            current_hour_products=current_hour_products,
            sync_mode=settings.sync_mode,
            max_buckets=max_buckets,
            slot_distribution=slot_info_list,
            active_slots=slot_distribution.get("active_count", 0),
            filling_slots=slot_distribution.get("filling_count", 0),
            dormant_slots=slot_distribution.get("dormant_count", 0),
            efficiency_percent=slot_distribution.get("efficiency_percent", 0),
            last_updated=now.isoformat(),
        )

    except Exception as e:
        logger.error(f"Error getting sync dashboard: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/products", response_model=SyncProductsResponse)
async def get_sync_products(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, description="Filter by sync_status"),
    hour: Optional[int] = Query(None, ge=0, le=23, description="Filter by hour bucket"),
    active_only: bool = Query(True, description="Only show active products"),
    search: Optional[str] = Query(None, description="Search by SKU"),
    current_user: dict = Depends(get_current_user)
):
    """Get products in sync schedule with filtering and pagination."""
    user_id = current_user["user_id"]
    client = _get_client()

    try:
        query = client.table("product_sync_schedule").select("*", count="exact")
        query = query.eq("user_id", user_id)

        if active_only:
            query = query.eq("is_active", True)

        if status:
            query = query.eq("sync_status", status)

        if hour is not None:
            query = query.eq("hour_bucket", hour)

        if search:
            query = query.ilike("sku", f"%{search}%")

        result = query.order("last_sync_at", desc=True, nullsfirst=False) \
            .range(offset, offset + limit - 1) \
            .execute()

        products = []
        for p in result.data or []:
            products.append(SyncProduct(
                id=str(p["id"]),
                sku=p["sku"],
                user_id=p["user_id"],
                hour_bucket=p["hour_bucket"],
                sync_status=p["sync_status"],
                last_sync_at=p.get("last_sync_at"),
                consecutive_failures=p.get("consecutive_failures", 0),
                last_error=p.get("last_error"),
                last_price=p.get("last_price"),
                last_quantity=p.get("last_quantity"),
                last_inventory_status=p.get("last_inventory_status"),
                last_location_summary=p.get("last_location_summary"),
                is_active=p.get("is_active", True),
                created_at=p["created_at"],
                updated_at=p["updated_at"],
            ))

        return SyncProductsResponse(
            products=products,
            total=result.count or 0,
            limit=limit,
            offset=offset,
        )

    except Exception as e:
        logger.error(f"Error getting sync products: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history", response_model=SyncHistoryResponse)
async def get_sync_history(
    limit: int = Query(50, ge=1, le=200),
    hours_back: int = Query(24, ge=1, le=168, description="Hours to look back"),
    current_user: dict = Depends(get_current_user)
):
    """Get recent sync history (products synced in the last N hours)."""
    user_id = current_user["user_id"]
    client = _get_client()

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)

        result = client.table("product_sync_schedule") \
            .select("sku, sync_status, last_sync_at, last_price, last_quantity, last_inventory_status, last_error, hour_bucket", count="exact") \
            .eq("user_id", user_id) \
            .gte("last_sync_at", cutoff.isoformat()) \
            .order("last_sync_at", desc=True) \
            .limit(limit) \
            .execute()

        items = []
        for p in result.data or []:
            items.append(SyncHistoryItem(
                sku=p["sku"],
                sync_status=p["sync_status"],
                last_sync_at=p.get("last_sync_at"),
                last_price=p.get("last_price"),
                last_quantity=p.get("last_quantity"),
                last_inventory_status=p.get("last_inventory_status"),
                last_error=p.get("last_error"),
                hour_bucket=p["hour_bucket"],
            ))

        return SyncHistoryResponse(
            items=items,
            total=result.count or 0,
        )

    except Exception as e:
        logger.error(f"Error getting sync history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/failures", response_model=FailedProductsResponse)
async def get_failed_products(
    limit: int = Query(50, ge=1, le=200),
    include_inactive: bool = Query(False, description="Include deactivated products"),
    current_user: dict = Depends(get_current_user)
):
    """Get products with sync failures."""
    user_id = current_user["user_id"]
    client = _get_client()

    try:
        query = client.table("product_sync_schedule") \
            .select("sku, consecutive_failures, last_error, last_sync_at, hour_bucket, is_active", count="exact") \
            .eq("user_id", user_id) \
            .gt("consecutive_failures", 0)

        if not include_inactive:
            query = query.eq("is_active", True)

        result = query.order("consecutive_failures", desc=True) \
            .limit(limit) \
            .execute()

        products = []
        for p in result.data or []:
            products.append(FailedProduct(
                sku=p["sku"],
                consecutive_failures=p["consecutive_failures"],
                last_error=p.get("last_error"),
                last_sync_at=p.get("last_sync_at"),
                hour_bucket=p["hour_bucket"],
                is_active=p.get("is_active", True),
            ))

        return FailedProductsResponse(
            products=products,
            total=result.count or 0,
        )

    except Exception as e:
        logger.error(f"Error getting failed products: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/hourly-stats", response_model=HourlyStatsResponse)
async def get_hourly_stats(
    current_user: dict = Depends(get_current_user)
):
    """Get detailed stats per hour bucket."""
    user_id = current_user["user_id"]
    client = _get_client()

    try:
        result = client.table("product_sync_schedule") \
            .select("hour_bucket, sync_status, is_active") \
            .eq("user_id", user_id) \
            .eq("is_active", True) \
            .execute()

        max_buckets = settings.sync_max_buckets
        hourly_data = {h: {"total": 0, "pending": 0, "syncing": 0, "success": 0, "failed": 0} for h in range(max_buckets)}

        for p in result.data or []:
            hour = p["hour_bucket"]
            status = p["sync_status"]

            if hour in hourly_data:
                hourly_data[hour]["total"] += 1
                if status in hourly_data[hour]:
                    hourly_data[hour][status] += 1

        hours = []
        for h in range(max_buckets):
            data = hourly_data[h]
            hours.append(HourlyStats(
                hour=h,
                total=data["total"],
                pending=data["pending"],
                syncing=data["syncing"],
                success=data["success"],
                failed=data["failed"],
            ))

        now = datetime.now(timezone.utc)
        if settings.sync_mode == "testing":
            current_hour = now.minute // 10
        else:
            current_hour = now.hour

        return HourlyStatsResponse(
            hours=hours,
            current_hour=current_hour,
        )

    except Exception as e:
        logger.error(f"Error getting hourly stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/product/{sku}")
async def get_product_sync_status(
    sku: str,
    current_user: dict = Depends(get_current_user)
):
    """Get sync status for a specific product SKU."""
    user_id = current_user["user_id"]
    client = _get_client()

    try:
        result = client.table("product_sync_schedule") \
            .select("*") \
            .eq("user_id", user_id) \
            .eq("sku", sku) \
            .execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Product not found in sync schedule")

        p = result.data[0]

        return SyncProduct(
            id=str(p["id"]),
            sku=p["sku"],
            user_id=p["user_id"],
            hour_bucket=p["hour_bucket"],
            sync_status=p["sync_status"],
            last_sync_at=p.get("last_sync_at"),
            consecutive_failures=p.get("consecutive_failures", 0),
            last_error=p.get("last_error"),
            last_price=p.get("last_price"),
            last_quantity=p.get("last_quantity"),
            last_inventory_status=p.get("last_inventory_status"),
            last_location_summary=p.get("last_location_summary"),
            is_active=p.get("is_active", True),
            created_at=p["created_at"],
            updated_at=p["updated_at"],
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting product sync status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/product/{sku}/reactivate")
async def reactivate_product(
    sku: str,
    current_user: dict = Depends(get_current_user)
):
    """Reactivate a product that was deactivated due to failures."""
    sync_store = get_sync_store()

    try:
        success = sync_store.reactivate_product(sku)

        if success:
            return {"message": f"Product {sku} reactivated successfully", "sku": sku}
        else:
            raise HTTPException(status_code=404, detail="Product not found or already active")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reactivating product: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trigger/{sku}")
async def trigger_immediate_sync(
    sku: str,
    current_user: dict = Depends(get_current_user)
):
    """Trigger an immediate sync for a specific product."""
    from app.celery_app.tasks.sync_shopify import sync_single_product_immediate

    user_id = current_user["user_id"]

    try:
        sync_single_product_immediate.delay(sku, user_id)

        return {
            "status": "queued",
            "sku": sku,
            "message": "Sync job queued. Will process when rate limiter allows."
        }

    except Exception as e:
        logger.error(f"Error triggering immediate sync: {e}")
        raise HTTPException(status_code=500, detail=str(e))
