"""
Change detection — determine if Shopify needs updating based on Boeing data changes.
Version: 1.0.0
"""
from typing import Any, Dict, Optional, Tuple

from app.utils.hash_utils import compute_boeing_hash


def should_update_shopify(
    new_data: Dict[str, Any], last_hash: Optional[str],
    last_price: Optional[float], last_quantity: Optional[int],
) -> Tuple[bool, str]:
    """Determine if Shopify needs to be updated based on Boeing data changes."""
    new_hash = compute_boeing_hash(new_data)

    if last_hash and new_hash == last_hash:
        return False, "no_change"

    new_price = new_data.get("list_price") or new_data.get("net_price")
    new_qty = new_data.get("inventory_quantity", 0)
    reasons = []
    if last_price is not None and new_price != last_price:
        reasons.append(f"price: {last_price} -> {new_price}")
    if last_quantity is not None and new_qty != last_quantity:
        reasons.append(f"quantity: {last_quantity} -> {new_qty}")

    if reasons:
        return True, "; ".join(reasons)
    return True, "first_sync_or_hash_mismatch"
