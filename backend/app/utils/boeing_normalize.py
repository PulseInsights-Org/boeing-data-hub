import logging
from typing import Any, Dict, List


logger = logging.getLogger("boeing_normalize")


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        val = float(value)
        return val if val != 0 else None
    except Exception:
        return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _strip_variant_suffix(part_number: str) -> str:
    if not part_number:
        return ""
    return part_number.split("=", 1)[0]


def normalize_boeing_payload(query: str, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    currency = payload.get("currency")
    line_items = payload.get("lineItems") or []

    normalized: List[Dict[str, Any]] = []

    for item in line_items:
        part_number = item.get("aviallPartNumber") or ""
        # Store FULL part number (with variant suffix like "WF338109=K3") in database
        # Strip suffix only for Shopify display
        title = part_number  # Full SKU for database
        shopify_title = _strip_variant_suffix(part_number)  # Stripped for Shopify display
        name = item.get("name") or part_number or query
        description = item.get("description") or ""

        dim_str = item.get("dim") or ""
        length = width = height = None
        try:
            parts = [p.strip() for p in dim_str.lower().split("x")] if dim_str else []
            if len(parts) == 3:
                length = _to_float(parts[0])
                width = _to_float(parts[1])
                height = _to_float(parts[2])
        except Exception:
            length = width = height = None

        dimension_uom = item.get("dimUOM") or ""

        weight_val = _to_float(item.get("weight"))
        weight_uom = item.get("weightUOM") or ""

        list_price = _to_float(item.get("listPrice"))
        net_price = _to_float(item.get("netPrice"))
        quantity = _to_int(item.get("quantity"))
        in_stock = item.get("inStock")

        location_availabilities = item.get("locationAvailabilities") or []
        locations: List[str] = []
        avail_total = 0
        location_quantities: List[Dict[str, Any]] = []
        for loc in location_availabilities:
            loc_name = loc.get("location")
            loc_qty = _to_int(loc.get("availQuantity"))
            if loc_name:
                locations.append(loc_name)
                location_quantities.append({"location": loc_name, "quantity": loc_qty})
            if loc_qty:
                avail_total += loc_qty

        inventory_quantity = quantity if quantity is not None and quantity > 0 else (avail_total or 0)

        inventory_status = None
        if in_stock is True or inventory_quantity > 0:
            inventory_status = "in_stock"
        elif in_stock is False:
            inventory_status = "out_of_stock"

        cost_per_item = list_price if list_price is not None else net_price
        base_price = list_price if list_price is not None else net_price
        price = base_price * 1.1 if base_price is not None else None

        supplier_name = item.get("supplierName") or ""
        manufacturer = supplier_name or "Boeing"
        pma = (item.get("faaApprovalCode") or "").upper() == "PMA"
        condition = "NE"
        estimated_lead_time = 60

        # sku: Full SKU with variant suffix stored in database (e.g., "WF338109=K3")
        # shopify_sku: Stripped version for Shopify display (e.g., "WF338109")
        sku = part_number  # Full SKU stored in database
        shopify_sku = _strip_variant_suffix(part_number)  # Stripped for Shopify

        # Certificate value for description
        cert = "FAA 8130-3"

        # Unit of measure
        base_uom = item.get("baseUOM") or ""

        # Build description as concatenation of Part No, Description (name), Cert, Condition, UoM
        # Use stripped SKU for Shopify display
        body_html = f"""<p>Part No. {shopify_sku}</p>
<p>Description: {name}</p>
<p>Cert: {cert}</p>
<p>Condition: {condition}</p>
<p>Unit of Measure: {base_uom}</p>"""

        boeing_image_url = item.get("productImage")
        boeing_thumbnail_url = item.get("thumbnailImage")

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

        normalized.append(
            {
                "aviall_part_number": part_number,
                # Note: sku field now stores FULL SKU with variant suffix (e.g., "WF338109=K3")
                # Shopify worker will strip suffix when publishing
                "base_uom": item.get("baseUOM"),
                "country_of_origin": item.get("countryOfOrigin"),
                "description": description,
                "dim": dim_str,
                "dim_uom": dimension_uom,
                "eccn": item.get("eccn"),
                "faa_approval_code": item.get("faaApprovalCode"),
                "hazmat_code": item.get("hazmatCode"),
                "in_stock": in_stock,
                "list_price": list_price,
                "location_availabilities": [
                    {
                        "location": loc.get("location"),
                        "avail_quantity": _to_int(loc.get("availQuantity")),
                    }
                    for loc in location_availabilities
                ]
                or None,
                "name": name,
                "net_price": net_price,
                "product_image": boeing_image_url,
                "quantity": quantity,
                "schedule_b_code": item.get("scheduleBCode"),
                "supplier_name": supplier_name,
                "thumbnail_image": boeing_thumbnail_url,
                "weight": weight_val,
                "weight_uom": weight_uom,
                "currency": currency,
                "boeing_image_url": boeing_image_url,
                "boeing_thumbnail_url": boeing_thumbnail_url,
                "title": title,
                "sku": sku,
                "vendor": None,
                "manufacturer": manufacturer,
                "cost_per_item": cost_per_item,
                "price": price,
                "inventory_quantity": inventory_quantity,
                "inventory_status": inventory_status,
                "location_summary": location_summary,
                "condition": condition,
                "pma": pma,
                "estimated_lead_time_days": estimated_lead_time,
                "dim_length": length,
                "dim_width": width,
                "dim_height": height,
                "cert": cert,
                "shopify": {
                    # Shopify fields use STRIPPED values (no variant suffix)
                    "title": shopify_title,
                    "sku": shopify_sku,
                    "description": name,
                    "body_html": body_html,
                    "vendor": None,
                    "manufacturer": manufacturer,
                    "cost_per_item": cost_per_item,
                    "price": price,
                    "currency": currency,
                    "unit_of_measure": item.get("baseUOM"),
                    "country_of_origin": item.get("countryOfOrigin"),
                    "length": length,
                    "width": width,
                    "height": height,
                    "dim_uom": dimension_uom,
                    "weight": weight_val,
                    "weight_uom": weight_uom,
                    "inventory_quantity": inventory_quantity,
                    "locations": locations or None,
                    "location_quantities": location_quantities or None,
                    "location_summary": location_summary,
                    "product_image": item.get("productImage"),
                    "thumbnail_image": item.get("thumbnailImage"),
                    "cert": cert,
                    "condition": condition,
                    "pma": pma,
                    "estimated_lead_time_days": estimated_lead_time,
                },
                "raw_boeing_data": {
                    **item,
                    "currency": currency,
                },
            }
        )

    return normalized
