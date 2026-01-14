import os
from typing import Any, Dict, List

import httpx
from fastapi import HTTPException
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set for Supabase access")

SUPABASE_REST_URL = SUPABASE_URL.rstrip("/") + "/rest/v1"


async def _supabase_post(table: str, rows: List[Dict[str, Any]], prefer: str = "return=minimal") -> None:
    """Low-level helper to POST rows into a Supabase table via REST API."""
    if not rows:
        return

    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(f"{SUPABASE_REST_URL}/{table}", headers=headers, json=rows)

    if resp.status_code >= 300:
        raise HTTPException(
            status_code=500,
            detail=f"Supabase insert into {table} failed: {resp.status_code} {resp.text}",
        )


async def insert_boeing_raw_data(search_query: str, raw_payload: Dict[str, Any]) -> None:
    """Insert the full raw Boeing response into boeing_raw_data table."""
    row = {
        "search_query": search_query,
        "raw_payload": raw_payload,
    }
    await _supabase_post("boeing_raw_data", [row])


async def upsert_product_staging(records: List[Dict[str, Any]]) -> None:
    """Upsert normalized product records into product_staging table.

    Input records are the normalized Boeing products returned by
    boeing_client.search_products (Boeing/NormalizedProduct shape).

    Here we transform them into a Shopify-friendly schema for the
    product_staging table:

    - id / sku
    - title (name)
    - body_html (description)
    - vendor (manufacturer/supplier)
    - price, currency
    - inventory_quantity, inventory_status
    - weight, weight_unit
    - country_of_origin
    - dim_length, dim_width, dim_height, dim_uom
    - status
    """

    if not records:
        return

    db_rows: List[Dict[str, Any]] = []

    for rec in records:
        raw: Dict[str, Any] = rec.get("rawBoeingData") or {}

        part_number = rec.get("partNumber") or rec.get("id")
        name = rec.get("name") or part_number
        description = rec.get("description") or ""

        manufacturer = rec.get("manufacturer") or ""
        supplier_name = raw.get("supplierName") or rec.get("distrSrc") or manufacturer

        price = rec.get("price")
        currency = rec.get("currency") or raw.get("currency")

        inventory_qty = rec.get("inventory")
        inventory_status = rec.get("availability")

        weight = rec.get("weight")
        weight_unit = rec.get("weightUnit")

        country_of_origin = raw.get("countryOfOrigin")

        dim_length = rec.get("length")
        dim_width = rec.get("width")
        dim_height = rec.get("height")
        dim_uom = rec.get("dimensionUom")

        status = rec.get("status") or "fetched"

        db_rows.append(
            {
                "id": part_number,
                "sku": part_number,
                "title": name,
                "body_html": description,
                "vendor": supplier_name or manufacturer,
                "price": price,
                "currency": currency,
                "inventory_quantity": inventory_qty,
                "inventory_status": inventory_status,
                "weight": weight,
                "weight_unit": weight_unit,
                "country_of_origin": country_of_origin,
                "dim_length": dim_length,
                "dim_width": dim_width,
                "dim_height": dim_height,
                "dim_uom": dim_uom,
                "status": status,
            }
        )

    # Use PostgREST upsert semantics: onConflict=id, merge duplicates
    await _supabase_post(
        "product_staging",
        db_rows,
        prefer="resolution=merge-duplicates",
    )


async def upsert_product(record: Dict[str, Any], shopify_product_id: str | None = None) -> None:
    """Upsert a finalized product into the product table.

    `record` is a NormalizedProduct-like dict coming from the frontend or
    backend, and we project it into the Shopify-friendly `product` schema.
    """

    raw: Dict[str, Any] = record.get("rawBoeingData") or {}

    part_number = record.get("partNumber") or record.get("id")
    name = record.get("title") or record.get("name") or part_number
    description = record.get("description") or ""

    manufacturer = record.get("manufacturer") or ""
    vendor = record.get("vendor") or manufacturer or record.get("distrSrc") or ""

    price = record.get("price")
    currency = record.get("currency") or raw.get("currency")

    inventory_qty = record.get("inventory")
    inventory_status = record.get("availability")

    weight = record.get("weight")
    weight_unit = record.get("weightUnit")

    country_of_origin = raw.get("countryOfOrigin")

    dim_length = record.get("length")
    dim_width = record.get("width")
    dim_height = record.get("height")
    dim_uom = record.get("dimensionUom")

    db_row = {
        "id": part_number,
        "sku": part_number,
        "title": name,
        "body_html": description,
        "vendor": vendor,
        "price": price,
        "currency": currency,
        "inventory_quantity": inventory_qty,
        "inventory_status": inventory_status,
        "weight": weight,
        "weight_unit": weight_unit,
        "country_of_origin": country_of_origin,
        "dim_length": dim_length,
        "dim_width": dim_width,
        "dim_height": dim_height,
        "dim_uom": dim_uom,
        "shopify_product_id": shopify_product_id,
    }

    await _supabase_post(
        "product",
        [db_row],
        prefer="resolution=merge-duplicates",
    )


async def update_quote_form_data(rfq_no: str, form_data: Dict[str, Any]) -> None:
    """Upsert form_data into quotes by rfq_no."""
    await _supabase_post(
        "quotes",
        [{"rfq_no": rfq_no, "form_data": form_data}],
        prefer="resolution=merge-duplicates",
    )
