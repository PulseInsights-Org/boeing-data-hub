"""
Hash utilities — deterministic hashing for sync change detection.

Hashing utilities for Boeing data change detection.

Extracted from sync_helpers.py for reuse across the codebase.
Version: 1.0.0
"""

import hashlib
import json
from typing import Any, Dict, Optional


def compute_boeing_hash(boeing_response: Dict[str, Any]) -> str:
    """
    Compute a deterministic hash of Boeing API response data.

    Used for change detection - if hash matches previous sync,
    no Shopify update is needed (saves API calls).

    Args:
        boeing_response: Normalized data extracted from Boeing API

    Returns:
        SHA-256 hash string (first 16 chars for storage efficiency)
    """
    relevant_data = {
        "price": boeing_response.get("list_price") or boeing_response.get("net_price"),
        "quantity": boeing_response.get("inventory_quantity", 0),
        "status": boeing_response.get("inventory_status"),
        "locations": boeing_response.get("location_summary"),
    }

    json_str = json.dumps(relevant_data, sort_keys=True, default=str)
    hash_obj = hashlib.sha256(json_str.encode())

    return hash_obj.hexdigest()[:16]


def compute_sync_hash(
    price: Optional[float],
    quantity: int,
    inventory_status: Optional[str],
    location_summary: Optional[str],
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
