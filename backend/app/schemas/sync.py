"""
Sync schemas — sync schedule and status models.

Sync pipeline schemas.

Defines request/response models for the Auto-Sync dashboard.
Version: 1.0.0
"""
from typing import Optional, List

from pydantic import BaseModel


class SyncStatusCounts(BaseModel):
    pending: int = 0
    syncing: int = 0
    success: int = 0
    failed: int = 0


class SlotInfo(BaseModel):
    hour: int
    count: int
    status: str  # 'active', 'filling', 'dormant'


class SyncDashboardResponse(BaseModel):
    """Complete dashboard data response."""
    total_products: int
    active_products: int
    inactive_products: int
    success_rate_percent: float
    high_failure_count: int
    status_counts: SyncStatusCounts
    current_hour: int
    current_hour_products: int
    sync_mode: str
    max_buckets: int
    slot_distribution: List[SlotInfo]
    active_slots: int
    filling_slots: int
    dormant_slots: int
    efficiency_percent: float
    last_updated: str


class SyncProduct(BaseModel):
    """Individual product sync info."""
    id: str
    sku: str
    user_id: str
    hour_bucket: int
    sync_status: str
    last_sync_at: Optional[str]
    consecutive_failures: int
    last_error: Optional[str]
    last_price: Optional[float]
    last_quantity: Optional[int]
    last_inventory_status: Optional[str]
    last_location_summary: Optional[str]
    is_active: bool
    created_at: str
    updated_at: str


class SyncProductsResponse(BaseModel):
    """Paginated sync products response."""
    products: List[SyncProduct]
    total: int
    limit: int
    offset: int


class SyncHistoryItem(BaseModel):
    """Single sync history entry."""
    sku: str
    sync_status: str
    last_sync_at: Optional[str]
    last_price: Optional[float]
    last_quantity: Optional[int]
    last_inventory_status: Optional[str]
    last_error: Optional[str]
    hour_bucket: int


class SyncHistoryResponse(BaseModel):
    """Recent sync history response."""
    items: List[SyncHistoryItem]
    total: int


class FailedProduct(BaseModel):
    """Failed product with error details."""
    sku: str
    consecutive_failures: int
    last_error: Optional[str]
    last_sync_at: Optional[str]
    hour_bucket: int
    is_active: bool


class FailedProductsResponse(BaseModel):
    """Failed products list response."""
    products: List[FailedProduct]
    total: int


class HourlyStats(BaseModel):
    """Stats for a specific hour."""
    hour: int
    total: int
    pending: int
    syncing: int
    success: int
    failed: int


class HourlyStatsResponse(BaseModel):
    """24-hour stats breakdown."""
    hours: List[HourlyStats]
    current_hour: int
