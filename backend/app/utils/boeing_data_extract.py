"""
Boeing data extraction — extract product data from Boeing API responses for sync.
Version: 1.0.0
"""
import logging
from typing import Any, Dict, Optional

from app.utils.type_converters import to_float, to_int

logger = logging.getLogger("boeing_data_extract")


def extract_boeing_product_data(boeing_response: Dict[str, Any], sku: str) -> Optional[Dict[str, Any]]:
    """Extract normalized product data from Boeing API response for a specific SKU."""
    line_items = boeing_response.get("lineItems", [])
    currency = boeing_response.get("currency", "USD")

    for item in line_items:
        aviall_part = item.get("aviallPartNumber", "")
        product_code = item.get("productCode", "")

        if aviall_part.upper() != sku.upper() and product_code.upper() != sku.upper():
            continue

        list_price = to_float(item.get("listPrice"))
        net_price = to_float(item.get("netPrice"))

        location_availabilities = item.get("locationAvailabilities") or []
        total_quantity = 0
        locations = []
        location_quantities = []

        for loc in location_availabilities:
            loc_name = loc.get("location", "")
            loc_qty = to_int(loc.get("availQuantity")) or 0
            total_quantity += loc_qty
            if loc_name:
                locations.append({"location": loc_name, "quantity": loc_qty})
                location_quantities.append({"location": loc_name, "quantity": loc_qty})

        in_stock = item.get("inStock")
        inventory_status = None
        if in_stock is True or total_quantity > 0:
            inventory_status = "in_stock"
        elif in_stock is False:
            inventory_status = "out_of_stock"

        location_summary = None
        if location_availabilities:
            parts = []
            for loc in location_availabilities:
                loc_name = loc.get("location")
                loc_qty = to_int(loc.get("availQuantity"))
                if loc_name:
                    parts.append(f"{loc_name}: {loc_qty if loc_qty is not None else 0}")
            location_summary = "; ".join(parts) if parts else None

        return {
            "sku": sku, "boeing_sku": aviall_part or sku,
            "list_price": list_price, "net_price": net_price, "currency": currency,
            "inventory_quantity": total_quantity, "inventory_status": inventory_status,
            "in_stock": in_stock, "locations": locations,
            "location_quantities": location_quantities,
            "location_summary": location_summary,
            "estimated_lead_time_days": None, "boeing_raw": item,
        }

    available_parts = [item.get("aviallPartNumber", "") for item in line_items]
    logger.warning(f"SKU {sku} not found in Boeing response. Available: {available_parts[:5]}...")
    return None


def create_out_of_stock_data(sku: str) -> Dict[str, Any]:
    """Create a synthetic out-of-stock record for a missing SKU."""
    return {
        "sku": sku, "boeing_sku": sku,
        "list_price": None, "net_price": None, "currency": "USD",
        "inventory_quantity": 0, "inventory_status": "out_of_stock",
        "in_stock": False, "locations": [], "location_quantities": [],
        "location_summary": None, "estimated_lead_time_days": None,
        "is_missing_sku": True,
    }
