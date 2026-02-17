"""
Slot manager — bucket allocation and slot distribution for sync scheduling.
Version: 1.0.0
"""
import logging
import math
from typing import Any, Dict, List

from app.core.config import settings

logger = logging.getLogger("slot_manager")

MAX_SKUS_PER_API_CALL = settings.sync_batch_size
MIN_PRODUCTS_PER_SLOT = MAX_SKUS_PER_API_CALL
SYNC_MODE = settings.sync_mode
MAX_BUCKETS = settings.sync_max_buckets


def get_slot_distribution(slot_counts: Dict[int, int]) -> Dict[str, Any]:
    """Analyze current slot distribution and categorize slots."""
    dormant, filling, active = [], [], []

    for bucket in range(MAX_BUCKETS):
        count = slot_counts.get(bucket, 0)
        if count == 0:
            dormant.append(bucket)
        elif count < MIN_PRODUCTS_PER_SLOT:
            filling.append(bucket)
        else:
            active.append(bucket)

    return {
        "dormant_slots": dormant, "filling_slots": filling, "active_slots": active,
        "dormant_count": len(dormant), "filling_count": len(filling), "active_count": len(active),
        "total_products": sum(slot_counts.values()),
        "slot_counts": slot_counts, "sync_mode": SYNC_MODE, "max_buckets": MAX_BUCKETS,
    }


def get_least_loaded_slot(slot_counts: Dict[int, int], total_products: int) -> int:
    """Get the optimal slot for a new product using least-loaded packing.

    DEPRECATED: Use get_optimal_slot() instead, which fills buckets in
    multiples of batch_size (10) to minimise wasted API calls.
    """
    min_slots_needed = min(MAX_BUCKETS, max(1, math.ceil(total_products / MIN_PRODUCTS_PER_SLOT)))

    min_count = float('inf')
    best_slot = 0
    for slot in range(min_slots_needed):
        count = slot_counts.get(slot, 0)
        if count < min_count:
            min_count = count
            best_slot = slot
    return best_slot


def get_optimal_slot(
    slot_counts: Dict[int, int],
    batch_size: int = MAX_SKUS_PER_API_CALL,
) -> int:
    """Pick the best bucket for a new product using multiples-of-batch-size filling.

    Algorithm:
      1. Find the first bucket (lowest index) that is partially filled,
         i.e. count > 0 and count % batch_size != 0.  Fill that one first
         so it reaches the next multiple of batch_size before opening another.
      2. If every active bucket is already at a multiple (or empty), pick
         the bucket with the lowest count (lowest index as tiebreaker).

    This ensures each bucket reaches a clean multiple of the Boeing API
    batch size (10) before products spill into the next bucket, minimising
    wasted "partial batch" API calls during the hourly sync.
    """
    # Step 1 — complete the partially-filled bucket
    for bucket in range(MAX_BUCKETS):
        count = slot_counts.get(bucket, 0)
        if count > 0 and count % batch_size != 0:
            return bucket

    # Step 2 — all buckets are at multiples (or empty): pick the lowest
    min_count = float("inf")
    best = 0
    for bucket in range(MAX_BUCKETS):
        count = slot_counts.get(bucket, 0)
        if count < min_count:
            min_count = count
            best = bucket
    return best


def precompute_slot_assignments(
    slot_counts: Dict[int, int],
    count: int,
    batch_size: int = MAX_SKUS_PER_API_CALL,
) -> List[int]:
    """Pre-compute slot assignments for an entire batch of products.

    Simulates sequential insertion using get_optimal_slot so that a
    single orchestrator (publish_batch) can hand each publish_product
    worker its slot up-front — eliminating the read-compute-write race
    condition that occurs when concurrent workers each call
    get_least_loaded_slot independently.

    Args:
        slot_counts: Current {bucket: count} from sync_store.get_slot_counts()
        count: Number of products to assign
        batch_size: Boeing API batch size (default 10)

    Returns:
        List of bucket numbers, one per product, in insertion order.
    """
    counts = dict(slot_counts)  # mutable copy
    assignments: List[int] = []
    for _ in range(count):
        slot = get_optimal_slot(counts, batch_size)
        counts[slot] = counts.get(slot, 0) + 1
        assignments.append(slot)
    return assignments
