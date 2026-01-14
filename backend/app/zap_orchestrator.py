import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from .boeing_client import search_products
from .shopify_client import get_variant_by_sku


def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _tag_date() -> str:
    return datetime.utcnow().strftime("%b-%d-%y").upper()


def _base_item(part_no: str, requested_qty: int) -> Dict[str, Any]:
    return {
        "part_no": part_no,
        "condition": "OH",
        "requested_qty": requested_qty,
        "no_quote": True,
    }


async def _shopify_item(part_no: str, requested_qty: int, uom: str) -> Optional[Dict[str, Any]]:
    variant = await get_variant_by_sku(part_no)
    if not variant:
        return None

    price = _to_float(variant.get("price"))
    inventory = _to_int(variant.get("inventoryQuantity"))

    item = _base_item(part_no, requested_qty)
    item.update(
        {
            "no_quote": False,
            "qty_available": inventory if inventory is not None else 0,
            "traceability": "SHOPIFY",
            "uom": uom,
            "price_usd": price if price is not None else 0,
            "price_type": "OUTRIGHT",
            "tag_date": _tag_date(),
            "lead_time": "ON REQUEST",
        }
    )
    return item


async def _boeing_item(part_no: str, requested_qty: int, uom: str) -> Optional[Dict[str, Any]]:
    results = await search_products(part_no)
    if not results:
        return None

    product = results[0]
    price = _to_float(product.get("price"))
    inventory = _to_int(product.get("inventory"))
    base_uom = product.get("baseUOM") or uom

    item = _base_item(part_no, requested_qty)
    item.update(
        {
            "no_quote": False,
            "qty_available": inventory if inventory is not None else 0,
            "traceability": "VENDOR",
            "uom": base_uom,
            "price_usd": price if price is not None else 0,
            "price_type": "OUTRIGHT",
            "tag_date": _tag_date(),
            "lead_time": "ON REQUEST",
        }
    )
    return item


async def build_final_quote(payload: Dict[str, Any]) -> Dict[str, Any]:
    requested_parts: List[Dict[str, Any]] = payload.get("requested_parts") or []
    items: List[Dict[str, Any]] = []

    for part in requested_parts:
        part_no = (
            part.get("part_number_searched")
            or part.get("supplier_part_number")
            or part.get("part_number")
            or ""
        )
        requested_qty = _to_int(part.get("quantity")) or 0
        uom = part.get("unit_of_measure") or "EA"

        item = await _shopify_item(part_no, requested_qty, uom)
        if item is None:
            item = await _boeing_item(part_no, requested_qty, uom)
        if item is None:
            item = _base_item(part_no, requested_qty)

        items.append(item)

    final_payload = {
        "rfq_details": payload.get("rfq_details") or {},
        "buyer_details": payload.get("buyer_details") or {},
        "quote_details": {
            "quote_prepared_by": "Manesh Bhide",
            "supplier_comments": "Auto-generated via inventory lookup",
            "items": items,
        },
    }

    print(json.dumps(final_payload, indent=2))
    return final_payload
