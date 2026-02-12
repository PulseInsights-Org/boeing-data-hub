"""
Sync Scheduler Helper Functions.

Provides utility functions for the sync scheduler system:
- Least-loaded slot allocation algorithm
- Boeing response hashing for change detection
- Data extraction from Boeing API responses
- Slot distribution calculations

Key Algorithm: Least-Loaded Packing
Instead of distributing products evenly across all 24 hours (which wastes
Boeing API capacity), we only activate the minimum number of slots needed
to have 10+ products per slot.

Example:
- 100 products → 10 active slots (10 products each) ✓
- NOT 100 products → 24 slots (4 products each) ✗

This ensures every Boeing API call sends exactly 10 SKUs.

Configuration (via settings singleton):
    sync_batch_size: Products per Boeing API call (default: 10)
    sync_mode: "production" or "testing"
"""

import hashlib
import json
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from app.core.config import settings

logger = logging.getLogger("sync_helpers")

# Configuration from settings singleton
MAX_SKUS_PER_API_CALL = settings.sync_batch_size
MIN_PRODUCTS_PER_SLOT = MAX_SKUS_PER_API_CALL  # Target for efficient batching
SYNC_MODE = settings.sync_mode
MAX_HOURS = 24  # Keep for backward compatibility
MAX_BUCKETS = settings.sync_max_buckets

# Log sync mode at module load
if SYNC_MODE == "testing":
    logger.info(f"[SYNC_HELPERS] 🧪 TESTING MODE: Using {MAX_BUCKETS} minute buckets (10-min intervals)")
else:
    logger.info(f"[SYNC_HELPERS] 🚀 PRODUCTION MODE: Using {MAX_BUCKETS} hour buckets")


def compute_boeing_hash(boeing_response: Dict[str, Any]) -> str:
    """
    Compute a deterministic hash of Boeing API response data.

    Used for change detection - if hash matches previous sync,
    no Shopify update is needed (saves API calls).

    Hash includes:
    - price (list_price or net_price)
    - inventory_quantity
    - inventory_status (in_stock/out_of_stock)
    - location_summary (locations with quantities)

    Args:
        boeing_response: Normalized data extracted from Boeing API

    Returns:
        SHA-256 hash string (first 16 chars for storage efficiency)
    """
    # Extract only the fields we care about for change detection
    # Simplified hash focusing on key sync fields
    relevant_data = {
        "price": boeing_response.get("list_price") or boeing_response.get("net_price"),
        "quantity": boeing_response.get("inventory_quantity", 0),
        "status": boeing_response.get("inventory_status"),
        "locations": boeing_response.get("location_summary"),
    }

    # Sort keys for deterministic hashing
    json_str = json.dumps(relevant_data, sort_keys=True, default=str)
    hash_obj = hashlib.sha256(json_str.encode())

    # Return first 16 chars (64 bits - sufficient for change detection)
    return hash_obj.hexdigest()[:16]


def compute_sync_hash(
    price: Optional[float],
    quantity: int,
    inventory_status: Optional[str],
    location_summary: Optional[str]
) -> str:
    """
    Compute a sync hash from individual fields.

    Used for computing hash from database fields or for
    out-of-stock synthetic records.

    Args:
        price: Product price
        quantity: Inventory quantity
        inventory_status: "in_stock" or "out_of_stock"
        location_summary: Location summary string

    Returns:
        SHA-256 hash string (first 16 chars)
    """
    relevant_data = {
        "price": price,
        "quantity": quantity,
        "status": inventory_status,
        "locations": location_summary,
    }

    json_str = json.dumps(relevant_data, sort_keys=True, default=str)
    hash_obj = hashlib.sha256(json_str.encode())

    return hash_obj.hexdigest()[:16]


def get_slot_distribution(slot_counts: Dict[int, int]) -> Dict[str, Any]:
    """
    Analyze current slot distribution and categorize slots.

    Slots are categorized as:
    - DORMANT: 0 products (not used)
    - FILLING: 1-9 products (not yet efficient)
    - ACTIVE: 10+ products (efficient batching)

    SYNC MODE:
    - production: Analyzes hour buckets (0-23)
    - testing: Analyzes minute buckets (0-5)

    Args:
        slot_counts: Dict mapping bucket to product count

    Returns:
        Dict with slot analysis including active, filling, dormant counts
    """
    dormant = []  # 0 products
    filling = []  # 1-9 products
    active = []   # 10+ products

    # Use MAX_BUCKETS based on sync mode
    for bucket in range(MAX_BUCKETS):
        count = slot_counts.get(bucket, 0)
        if count == 0:
            dormant.append(bucket)
        elif count < MIN_PRODUCTS_PER_SLOT:
            filling.append(bucket)
        else:
            active.append(bucket)

    return {
        "dormant_slots": dormant,
        "filling_slots": filling,
        "active_slots": active,
        "dormant_count": len(dormant),
        "filling_count": len(filling),
        "active_count": len(active),
        "total_products": sum(slot_counts.values()),
        "slot_counts": slot_counts,
        "sync_mode": SYNC_MODE,
        "max_buckets": MAX_BUCKETS,
    }


def get_least_loaded_slot(slot_counts: Dict[int, int], total_products: int) -> int:
    """
    Get the optimal slot for a new product using least-loaded packing.

    Algorithm:
    1. Calculate minimum slots needed: ceil(total_products / 10)
    2. Only consider slots 0 to (min_slots - 1)
    3. Return the least loaded slot among those

    SYNC MODE:
    - production: Uses hour buckets (0-23)
    - testing: Uses minute buckets (0-5 for 10-min intervals)

    Args:
        slot_counts: Current product count per slot
        total_products: Total products including the new one

    Returns:
        Optimal bucket (0-23 for production, 0-5 for testing)
    """
    # Calculate minimum slots needed (respects MAX_BUCKETS based on mode)
    min_slots_needed = min(MAX_BUCKETS, max(1, math.ceil(total_products / MIN_PRODUCTS_PER_SLOT)))

    # Find least loaded slot among active range
    min_count = float('inf')
    best_slot = 0

    for slot in range(min_slots_needed):
        count = slot_counts.get(slot, 0)
        if count < min_count:
            min_count = count
            best_slot = slot

    logger.debug(
        f"Least-loaded allocation: total={total_products}, "
        f"active_slots={min_slots_needed}, selected_slot={best_slot} (count={min_count})"
    )

    return best_slot


def calculate_batch_groups(
    products: List[Dict[str, Any]],
    max_batch_size: int = MAX_SKUS_PER_API_CALL
) -> List[List[Dict[str, Any]]]:
    """
    Group products into batches of exactly max_batch_size for Boeing API calls.

    Args:
        products: List of product dicts with 'sku' field
        max_batch_size: Maximum products per batch (default 10)

    Returns:
        List of batches, each containing up to max_batch_size products
    """
    batches = []
    current_batch = []

    for product in products:
        current_batch.append(product)
        if len(current_batch) >= max_batch_size:
            batches.append(current_batch)
            current_batch = []

    # Handle remaining products
    if current_batch:
        batches.append(current_batch)

    return batches


def aggregate_filling_slots(
    slot_products: Dict[int, List[Dict[str, Any]]],
    filling_slots: List[int]
) -> Tuple[List[List[Dict[str, Any]]], List[str]]:
    """
    Aggregate products from all filling slots to create complete batches.

    When a slot has <10 products, it would waste API capacity.
    Instead, we borrow from other filling slots to create full batches.

    Args:
        slot_products: Dict mapping hour to list of product dicts
        filling_slots: List of slots with 1-9 products

    Returns:
        Tuple of (batches, all_skus_borrowed)
        - batches: List of complete 10-product batches
        - all_skus_borrowed: All SKUs included in borrowing
    """
    # Collect all products from filling slots
    all_filling_products = []
    for slot in filling_slots:
        products = slot_products.get(slot, [])
        all_filling_products.extend(products)

    if not all_filling_products:
        return [], []

    # Create complete batches from aggregated products
    batches = calculate_batch_groups(all_filling_products, MAX_SKUS_PER_API_CALL)
    all_skus = [p.get("sku") for p in all_filling_products]

    logger.info(
        f"Aggregated {len(all_filling_products)} products from {len(filling_slots)} "
        f"filling slots into {len(batches)} batches"
    )

    return batches, all_skus


def _to_float(value: Any) -> Optional[float]:
    """Convert value to float, returning None if invalid or zero."""
    if value is None:
        return None
    try:
        val = float(value)
        return val if val != 0 else None
    except (ValueError, TypeError):
        return None


def _to_int(value: Any) -> Optional[int]:
    """Convert value to int, returning None if invalid."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def extract_boeing_product_data(
    boeing_response: Dict[str, Any],
    sku: str
) -> Optional[Dict[str, Any]]:
    """
    Extract normalized product data from Boeing API response.

    Boeing API returns data in lineItems array. This function finds
    the matching SKU and extracts relevant fields.

    IMPORTANT: Field mappings match boeing_normalize.py for consistency:
    - listPrice, netPrice: Direct fields (NOT nested under "pricing")
    - locationAvailabilities: Array with availQuantity (NOT "availability")
    - currency: Top-level response field

    Args:
        boeing_response: Raw Boeing API response
        sku: Part number (full boeing_sku with variant suffix like "RS1157-1=E9")

    Returns:
        Normalized dict with price, quantity, locations, etc.
        None if SKU not found in response
    """
    line_items = boeing_response.get("lineItems", [])
    currency = boeing_response.get("currency", "USD")

    for item in line_items:
        # Boeing returns aviallPartNumber (full SKU with variant suffix)
        aviall_part = item.get("aviallPartNumber", "")
        # Also check productCode as fallback
        product_code = item.get("productCode", "")

        # Match against either field (case-insensitive)
        if aviall_part.upper() == sku.upper() or product_code.upper() == sku.upper():
            # Extract pricing - DIRECT fields, not nested (matches boeing_normalize.py)
            list_price = _to_float(item.get("listPrice"))
            net_price = _to_float(item.get("netPrice"))

            # Extract inventory - use locationAvailabilities (matches boeing_normalize.py)
            location_availabilities = item.get("locationAvailabilities") or []
            total_quantity = 0
            locations = []
            location_quantities = []

            for loc in location_availabilities:
                loc_name = loc.get("location", "")
                loc_qty = _to_int(loc.get("availQuantity")) or 0
                total_quantity += loc_qty
                if loc_name:
                    locations.append({
                        "location": loc_name,
                        "quantity": loc_qty,
                    })
                    location_quantities.append({
                        "location": loc_name,
                        "quantity": loc_qty,
                    })

            # Determine inventory status
            in_stock = item.get("inStock")
            inventory_status = None
            if in_stock is True or total_quantity > 0:
                inventory_status = "in_stock"
            elif in_stock is False:
                inventory_status = "out_of_stock"

            # Build location summary (matches boeing_normalize.py format)
            location_summary = None
            if location_availabilities:
                parts = []
                for loc in location_availabilities:
                    loc_name = loc.get("location")
                    loc_qty = _to_int(loc.get("availQuantity"))
                    if loc_name:
                        qty_str = str(loc_qty) if loc_qty is not None else "0"
                        parts.append(f"{loc_name}: {qty_str}")
                location_summary = "; ".join(parts) if parts else None

            return {
                "sku": sku,
                "boeing_sku": aviall_part or sku,  # Full SKU with variant suffix
                "list_price": list_price,
                "net_price": net_price,
                "currency": currency,
                "inventory_quantity": total_quantity,
                "inventory_status": inventory_status,
                "in_stock": in_stock,
                "locations": locations,
                "location_quantities": location_quantities,
                "location_summary": location_summary,
                "estimated_lead_time_days": None,
                "boeing_raw": item,  # Keep raw for debugging
            }

    # Log available part numbers for debugging
    available_parts = [item.get("aviallPartNumber", "") for item in line_items]
    logger.warning(f"SKU {sku} not found in Boeing response. Available: {available_parts[:5]}...")
    return None


def create_out_of_stock_data(sku: str) -> Dict[str, Any]:
    """
    Create a synthetic out-of-stock product data record.

    Used when a SKU is not found in Boeing response with showNoStock=false,
    indicating the product is out of stock.

    Args:
        sku: Product SKU

    Returns:
        Dict with out-of-stock state for sync
    """
    return {
        "sku": sku,
        "boeing_sku": sku,
        "list_price": None,  # Price unchanged when out of stock
        "net_price": None,
        "currency": "USD",
        "inventory_quantity": 0,
        "inventory_status": "out_of_stock",
        "in_stock": False,
        "locations": [],
        "location_quantities": [],
        "location_summary": None,
        "estimated_lead_time_days": None,
        "is_missing_sku": True,  # Flag to indicate this is a missing SKU
    }


def should_update_shopify(
    new_data: Dict[str, Any],
    last_hash: Optional[str],
    last_price: Optional[float],
    last_quantity: Optional[int]
) -> Tuple[bool, str]:
    """
    Determine if Shopify needs to be updated based on Boeing data.

    Compares new Boeing data against previous sync state.
    Uses hash for quick comparison, with price/quantity as fallback.

    Args:
        new_data: Newly extracted Boeing data
        last_hash: Previous sync hash (if any)
        last_price: Previous synced price
        last_quantity: Previous synced quantity

    Returns:
        Tuple of (should_update, reason)
    """
    new_hash = compute_boeing_hash(new_data)

    # If we have a previous hash, compare
    if last_hash:
        if new_hash == last_hash:
            return False, "no_change"

    # Check for significant changes
    new_price = new_data.get("list_price") or new_data.get("net_price")
    new_qty = new_data.get("inventory_quantity", 0)

    reasons = []

    if last_price is not None and new_price != last_price:
        reasons.append(f"price: {last_price} → {new_price}")

    if last_quantity is not None and new_qty != last_quantity:
        reasons.append(f"quantity: {last_quantity} → {new_qty}")

    if reasons:
        return True, "; ".join(reasons)

    # First sync or hash changed
    return True, "first_sync_or_hash_mismatch"


def get_current_hour_utc() -> int:
    """Get current hour in UTC (0-23)."""
    return datetime.now(timezone.utc).hour


def get_current_minute_bucket() -> int:
    """
    Get current minute bucket for testing mode.

    Buckets are 10-minute intervals:
    - Bucket 0: minutes 0-9
    - Bucket 1: minutes 10-19
    - Bucket 2: minutes 20-29
    - Bucket 3: minutes 30-39
    - Bucket 4: minutes 40-49
    - Bucket 5: minutes 50-59

    Returns:
        Current minute bucket (0-5)
    """
    return datetime.now(timezone.utc).minute // 10


def get_current_bucket() -> int:
    """
    Get current bucket based on sync mode.

    SYNC MODE:
    - production: Returns hour bucket (0-23)
    - testing: Returns minute bucket (0-5)

    Returns:
        Current bucket value
    """
    if SYNC_MODE == "testing":
        return get_current_minute_bucket()
    return get_current_hour_utc()


def is_within_sync_window(target_hour: int, window_minutes: int = 15) -> bool:
    """
    Check if current time is within sync window for target hour.

    Sync window is from HH:45 to HH+1:00 (last 15 minutes of each hour).
    This staggers syncs to avoid thundering herd at exactly HH:00.

    Args:
        target_hour: The hour bucket to check (0-23)
        window_minutes: Minutes before hour end to start syncing

    Returns:
        True if within sync window
    """
    now = datetime.now(timezone.utc)
    current_hour = now.hour
    current_minute = now.minute

    # Sync window is 45-59 minutes of target_hour
    # (e.g., hour_bucket=14 syncs at 14:45-14:59)
    if current_hour == target_hour and current_minute >= (60 - window_minutes):
        return True

    return False


def calculate_next_retry_time(consecutive_failures: int, base_hours: int = 4) -> int:
    """
    Calculate hours until next retry using exponential backoff.

    Retry schedule:
    - 1st failure: 4 hours
    - 2nd failure: 8 hours
    - 3rd failure: 16 hours
    - 4th failure: 32 hours (capped at 24)
    - 5th+ failure: Mark as inactive (handled elsewhere)

    Args:
        consecutive_failures: Number of consecutive failures
        base_hours: Base retry interval

    Returns:
        Hours until next retry (capped at 24)
    """
    # Exponential backoff: 4, 8, 16, 24 (capped)
    hours = base_hours * (2 ** (consecutive_failures - 1))
    return min(hours, 24)
