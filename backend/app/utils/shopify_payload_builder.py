"""
Shopify payload builder — pure transformation from record to Shopify REST payload.

Shopify payload builder – pure transformation logic.

Extracted from shopify_client.py so that the client contains
only HTTP transport and the builder can be unit-tested without
network calls.
Version: 1.0.0
"""

import logging
from typing import Any, Dict

from app.core.config import settings
from app.core.constants.publishing import (
    UOM_MAPPING,
    CERT_MAPPING,
    TRACE_ALLOWED_DOMAINS,
    PRODUCT_TAGS,
)
from app.core.constants.pricing import FALLBACK_IMAGE_URL, MARKUP_FACTOR

logger = logging.getLogger("shopify_payload_builder")

# Location code mapping from settings
_INVENTORY_LOCATION_CODES: dict = settings.shopify_inventory_location_codes or {}


# ── Mapping helpers ───────────────────────────────────────────────

def map_unit_of_measure(uom: str) -> str:
    """Map Boeing UOM values to Shopify allowed choices."""
    if not uom:
        return ""
    mapped = UOM_MAPPING.get(uom.upper().strip(), "")
    if not mapped:
        logger.info(f"shopify UOM not mapped, skipping: {uom}")
    return mapped


def map_cert(cert: str) -> str:
    """Map cert values to Shopify allowed choices."""
    if not cert:
        return ""
    cert_upper = cert.upper().strip()
    for keywords, value in CERT_MAPPING:
        if any(kw in cert_upper for kw in keywords):
            return value
    # Default to FAA 8130-3 for aerospace parts
    return "FAA 8130-3"


def validate_trace_url(trace: str) -> str:
    """Validate trace URL against Shopify allowed domains."""
    if not trace:
        return ""
    trace = trace.strip()
    for domain in TRACE_ALLOWED_DOMAINS:
        if trace.startswith(domain):
            return trace
    logger.info(f"shopify trace URL not from allowed domain, skipping: {trace}")
    return ""


def map_inventory_location(
    location: str,
    location_id: str = "",
    inventory_location_codes: dict | None = None,
) -> str:
    """Map inventory location to exactly 3 characters.

    Args:
        location: Full location string like "Dallas Central: 106"
        location_id: Optional pre-defined location ID (exactly 3 chars)
        inventory_location_codes: Override mapping; defaults to settings.
    """
    codes = inventory_location_codes or _INVENTORY_LOCATION_CODES

    if location_id and len(location_id.strip()) == 3:
        return location_id.strip()
    if location and len(location.strip()) == 3:
        return location.strip()

    if location and codes:
        first_location = location.split(";")[0].strip() if ";" in location else location
        location_name = first_location.split(":")[0].strip() if ":" in first_location else first_location.strip()

        if location_name in codes:
            code = codes[location_name]
            if len(code) == 3:
                return code

        location_upper = location_name.upper()
        for name, code in codes.items():
            if name.upper() in location_upper or location_upper in name.upper():
                if len(code) == 3:
                    return code

    return ""


# ── Metafield builder ─────────────────────────────────────────────

def build_metafields(product: Dict[str, Any]) -> list[Dict[str, Any]]:
    """Build custom-namespace metafields for a Shopify product."""
    shopify_data = product.get("shopify") or {}

    raw_sku = shopify_data.get("sku") or product.get("sku") or product.get("partNumber") or ""
    part_number = raw_sku.split("=")[0] if "=" in raw_sku else raw_sku
    alternate_part_number = ""

    manufacturer = shopify_data.get("manufacturer") or product.get("manufacturer") or product.get("supplier_name") or ""
    notes = shopify_data.get("notes") or product.get("notes") or ""
    expiration_date = shopify_data.get("expiration_date") or product.get("expiration_date") or ""

    raw_location = shopify_data.get("location_summary") or product.get("location_summary") or ""
    loc_id = shopify_data.get("location_id") or product.get("location_id") or ""
    inventory_location = map_inventory_location(raw_location, loc_id)

    raw_condition = product.get("condition") or "NE"
    condition = raw_condition[:2] if len(raw_condition) > 3 else raw_condition

    raw_uom = shopify_data.get("unit_of_measure") or product.get("baseUOM") or product.get("base_uom") or ""
    unit_of_measure = map_unit_of_measure(raw_uom)

    metafields: list[Dict[str, Any]] = []

    part_name = product.get("name") or ""
    simple_fields = [
        ("part_number", part_number, "single_line_text_field"),
        ("part_name", part_name, "single_line_text_field"),
        ("alternate_part_number", alternate_part_number, "single_line_text_field"),
        ("manufacturer", manufacturer, "single_line_text_field"),
        ("notes", notes, "multi_line_text_field"),
    ]
    for key, value, mtype in simple_fields:
        if str(value).strip():
            metafields.append({"namespace": "custom", "key": key, "value": str(value), "type": mtype})

    if inventory_location and len(inventory_location) == 3:
        metafields.append({"namespace": "custom", "key": "inventory_location", "value": inventory_location, "type": "single_line_text_field"})

    if condition and len(condition) <= 3:
        metafields.append({"namespace": "custom", "key": "condition", "value": condition, "type": "single_line_text_field"})

    if unit_of_measure:
        metafields.append({"namespace": "custom", "key": "unit_of_measure", "value": unit_of_measure, "type": "single_line_text_field"})

    raw_cert = shopify_data.get("cert") or product.get("cert") or "FAA 8130-3"
    cert = map_cert(raw_cert)
    if cert:
        metafields.append({"namespace": "custom", "key": "trace", "value": cert, "type": "single_line_text_field"})

    raw_trace_url = shopify_data.get("trace") or product.get("trace") or ""
    trace_url = validate_trace_url(raw_trace_url)
    if trace_url:
        metafields.append({"namespace": "custom", "key": "tracedoc", "value": trace_url, "type": "url"})

    if expiration_date:
        metafields.append({"namespace": "custom", "key": "expiration_date", "value": str(expiration_date), "type": "date"})

    estimated_lead_time = (
        shopify_data.get("estimated_lead_time_days")
        if shopify_data.get("estimated_lead_time_days") is not None
        else product.get("estimated_lead_time_days")
    )
    if estimated_lead_time is not None:
        metafields.append({"namespace": "custom", "key": "estimated_lead_time", "value": str(int(estimated_lead_time)), "type": "number_integer"})

    return metafields


# ── Full payload builder ──────────────────────────────────────────

def build_product_payload(product: Dict[str, Any]) -> Dict[str, Any]:
    """Build the full Shopify REST API product payload.

    This is the pure-logic counterpart of ShopifyClient.to_shopify_product_body().
    """
    shopify_data = product.get("shopify") or {}
    title = shopify_data.get("title") or product.get("title") or product.get("name") or product.get("partNumber")
    description = shopify_data.get("description") or product.get("description") or ""
    body_html = shopify_data.get("body_html") or (f"<p>{description}</p>" if description.strip() else "")
    manufacturer = shopify_data.get("manufacturer") or product.get("manufacturer") or ""
    vendor = shopify_data.get("vendor") or product.get("vendor") or ""
    country_of_origin = shopify_data.get("country_of_origin") or product.get("countryOfOrigin") or ""

    base_cost = (
        shopify_data.get("cost_per_item")
        or product.get("list_price")
        or product.get("net_price")
        or product.get("price")
        or 0
    )
    price = shopify_data.get("price") or (base_cost * MARKUP_FACTOR if base_cost else 0)
    inventory = shopify_data.get("inventory_quantity") or product.get("inventory") or product.get("inventory_quantity") or 0
    weight = shopify_data.get("weight") or product.get("weight") or 0
    weight_unit = shopify_data.get("weight_uom") or product.get("weightUnit") or "lb"

    part_number = shopify_data.get("sku") or product.get("sku") or product.get("partNumber") or ""
    dim = {
        "length": shopify_data.get("length") or product.get("length") or product.get("dim_length"),
        "width": shopify_data.get("width") or product.get("width") or product.get("dim_width"),
        "height": shopify_data.get("height") or product.get("height") or product.get("dim_height"),
        "unit": shopify_data.get("dim_uom") or product.get("dimensionUom") or product.get("dim_uom"),
    }

    base_uom = shopify_data.get("unit_of_measure") or product.get("baseUOM") or product.get("base_uom") or ""
    hazmat_code = product.get("hazmatCode") or product.get("hazmat_code") or ""
    faa_approval = product.get("faaApprovalCode") or product.get("faa_approval_code") or ""
    eccn = product.get("eccn") or ""
    schedule_b_code = product.get("schedule_b_code") or ""
    condition = product.get("condition") or "NE"
    pma = product.get("pma") or False
    lead_time = product.get("estimated_lead_time_days") if product.get("estimated_lead_time_days") is not None else product.get("estimatedLeadTimeDays")
    trace = shopify_data.get("trace") or product.get("trace") or ""
    expiration_date = shopify_data.get("expiration_date") or product.get("expiration_date") or ""
    notes = shopify_data.get("notes") or product.get("notes") or ""

    tags = list(PRODUCT_TAGS)
    if country_of_origin:
        tags.append(f"origin-{country_of_origin.lower().replace(' ', '-')}")

    # Images
    images = []
    primary_image = product.get("image_url") or shopify_data.get("product_image") or product.get("product_image")
    if primary_image:
        images.append({"src": primary_image})
    thumbnail_image = shopify_data.get("thumbnail_image") or product.get("thumbnail_image")
    if thumbnail_image and thumbnail_image != primary_image:
        if "aviall.com" not in thumbnail_image and "boeing.com" not in thumbnail_image:
            images.append({"src": thumbnail_image})
    if not images:
        images.append({"src": FALLBACK_IMAGE_URL})

    # Location quantities
    location_quantities = shopify_data.get("location_quantities") or []
    inventory_location_str = shopify_data.get("location_summary") or ""
    if not inventory_location_str and location_quantities:
        inventory_location_str = ", ".join(
            f"{loc.get('location')}: {loc.get('quantity')}"
            for loc in location_quantities
            if loc.get("location") and loc.get("quantity") is not None
        )

    initial_inventory = 0 if location_quantities else int(inventory)

    payload = {
        "product": {
            "title": title,
            "body_html": body_html,
            "vendor": "BDI",
            "tags": tags,
            "images": images,
            "variants": [
                {
                    "sku": part_number,
                    "price": str(price),
                    "inventory_management": "shopify",
                    "inventory_quantity": initial_inventory,
                    "weight": float(weight) if weight else 0,
                    "weight_unit": "kg" if weight_unit == "kg" else "lb",
                }
            ],
            "metafields": [
                {"namespace": "boeing", "key": "part_number", "value": part_number, "type": "single_line_text_field"},
                {"namespace": "boeing", "key": "alternate_part_number", "value": "", "type": "single_line_text_field"},
                {"namespace": "boeing", "key": "dimensions", "value": str(dim), "type": "single_line_text_field"},
                {"namespace": "boeing", "key": "distribution_source", "value": product.get("supplierName") or product.get("supplier_name") or "", "type": "single_line_text_field"},
                {"namespace": "boeing", "key": "country_of_origin", "value": country_of_origin, "type": "single_line_text_field"},
                {"namespace": "boeing", "key": "unit_of_measure", "value": base_uom, "type": "single_line_text_field"},
                {"namespace": "boeing", "key": "manufacturer", "value": shopify_data.get("manufacturer") or product.get("manufacturer") or product.get("supplier_name") or "", "type": "single_line_text_field"},
                {"namespace": "boeing", "key": "hazmat_code", "value": hazmat_code, "type": "single_line_text_field"},
                {"namespace": "boeing", "key": "faa_approval_code", "value": faa_approval, "type": "single_line_text_field"},
                {"namespace": "boeing", "key": "eccn", "value": eccn, "type": "single_line_text_field"},
                {"namespace": "boeing", "key": "schedule_b_code", "value": schedule_b_code, "type": "single_line_text_field"},
                {"namespace": "boeing", "key": "description", "value": shopify_data.get("description") or product.get("name") or "", "type": "single_line_text_field"},
                {"namespace": "boeing", "key": "condition", "value": condition, "type": "single_line_text_field"},
                {"namespace": "boeing", "key": "pma", "value": str(pma).lower(), "type": "single_line_text_field"},
                {"namespace": "boeing", "key": "estimated_lead_time", "value": str(lead_time), "type": "number_integer"},
                {"namespace": "boeing", "key": "cert", "value": shopify_data.get("cert") or product.get("cert") or "FAA 8130-3", "type": "single_line_text_field"},
                {"namespace": "boeing", "key": "trace", "value": trace, "type": "url"},
                {"namespace": "boeing", "key": "expiration_date", "value": expiration_date, "type": "date"},
                {"namespace": "boeing", "key": "notes", "value": notes, "type": "multi_line_text_field"},
                {"namespace": "boeing", "key": "inventory_location", "value": inventory_location_str, "type": "single_line_text_field"},
            ],
        }
    }

    # Replace boeing metafields with custom-namespace metafields
    payload["product"]["metafields"] = build_metafields(product)
    return payload
