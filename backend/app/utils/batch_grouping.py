"""
Batch grouping — group products into batches for Boeing API calls.
Version: 1.0.0
"""
import logging
from typing import Any, Dict, List, Tuple

from app.utils.slot_manager import MAX_SKUS_PER_API_CALL

logger = logging.getLogger("batch_grouping")


def calculate_batch_groups(
    products: List[Dict[str, Any]], max_batch_size: int = MAX_SKUS_PER_API_CALL,
) -> List[List[Dict[str, Any]]]:
    """Group products into batches of max_batch_size for Boeing API calls."""
    batches = []
    current_batch = []
    for product in products:
        current_batch.append(product)
        if len(current_batch) >= max_batch_size:
            batches.append(current_batch)
            current_batch = []
    if current_batch:
        batches.append(current_batch)
    return batches


def aggregate_filling_slots(
    slot_products: Dict[int, List[Dict[str, Any]]], filling_slots: List[int],
) -> Tuple[List[List[Dict[str, Any]]], List[str]]:
    """Aggregate products from filling slots to create complete batches."""
    all_filling_products = []
    for slot in filling_slots:
        all_filling_products.extend(slot_products.get(slot, []))

    if not all_filling_products:
        return [], []

    batches = calculate_batch_groups(all_filling_products, MAX_SKUS_PER_API_CALL)
    all_skus = [p.get("sku") for p in all_filling_products]

    logger.info(
        f"Aggregated {len(all_filling_products)} products from {len(filling_slots)} "
        f"filling slots into {len(batches)} batches"
    )
    return batches, all_skus
