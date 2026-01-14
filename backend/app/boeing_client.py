import os
from typing import Any, Dict, List

import httpx
from fastapi import HTTPException
from dotenv import load_dotenv

load_dotenv()

from .supabase_client import insert_boeing_raw_data, upsert_product_staging

"""Boeing Price & Availability client

Implements the 3-step flow described by Boeing:

1) POST oauth2/v2.0/token to obtain an access token
2) GET /boeing-part-price-availability/token/v1/oauth with x-username/x-password
   to obtain an x-part-access-token
3) POST /boeing-part-price-availability/price-availability/v1/wtoken with
   productCodes and the two tokens to retrieve price & availability data.

All credentials are read from environment variables and MUST NOT be
hard-coded in source.
"""

BOEING_OAUTH_TOKEN_URL = os.getenv(
    "BOEING_OAUTH_TOKEN_URL",
    "https://api.developer.boeingservices.com/oauth2/v2.0/token",
)
BOEING_CLIENT_ID = os.getenv("BOEING_CLIENT_ID")
BOEING_CLIENT_SECRET = os.getenv("BOEING_CLIENT_SECRET")
BOEING_SCOPE = os.getenv("BOEING_SCOPE", "api://helixapis.com/.default")

BOEING_PNA_OAUTH_URL = os.getenv(
    "BOEING_PNA_OAUTH_URL",
    "https://api.developer.boeingservices.com/boeing-part-price-availability/token/v1/oauth",
)
BOEING_PNA_PRICE_URL = os.getenv(
    "BOEING_PNA_PRICE_URL",
    "https://api.developer.boeingservices.com/boeing-part-price-availability/price-availability/v1/wtoken",
)

BOEING_USERNAME = os.getenv("BOEING_USERNAME")
BOEING_PASSWORD = os.getenv("BOEING_PASSWORD")


async def _get_oauth_access_token() -> str:
    """Step 1: Get access token from oauth2/v2.0/token using client_id/secret."""

    if not (BOEING_CLIENT_ID and BOEING_CLIENT_SECRET):
        raise HTTPException(
            status_code=500,
            detail="BOEING_CLIENT_ID and BOEING_CLIENT_SECRET env vars are required",
        )

    data = {
        "client_id": BOEING_CLIENT_ID,
        "client_secret": BOEING_CLIENT_SECRET,
        "scope": BOEING_SCOPE,
        "grant_type": "client_credentials",
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            BOEING_OAUTH_TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Boeing OAuth error: {resp.text}",
        )

    body = resp.json()
    token = body.get("access_token")
    if not token:
        raise HTTPException(status_code=500, detail="No access_token in Boeing OAuth response")
    return token


async def _get_part_access_token(access_token: str) -> str:
    """Step 2: Call token/v1/oauth to get x-part-access-token.

    We assume the token is returned either in the response headers as
    'x-part-access-token' or as a JSON field; we check both.
    """

    if not (BOEING_USERNAME and BOEING_PASSWORD):
        raise HTTPException(
            status_code=500,
            detail="BOEING_USERNAME and BOEING_PASSWORD env vars are required",
        )

    headers = {
        "x-username": BOEING_USERNAME,
        "x-password": BOEING_PASSWORD,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(BOEING_PNA_OAUTH_URL, headers=headers)

    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Boeing PNA oauth error: {resp.text}",
        )

    # Try header first (closest to the cURL usage), then JSON body as fallback
    part_token = resp.headers.get("x-part-access-token")
    if not part_token:
        try:
            body = resp.json()
        except Exception:  # pragma: no cover - defensive
            body = {}
        part_token = body.get("x-part-access-token") or body.get("access_token")

    if not part_token:
        raise HTTPException(status_code=500, detail="Missing x-part-access-token in Boeing response")

    return part_token


async def search_products(query: str) -> List[Dict[str, Any]]:
    """Fetch price & availability for a given product code from Boeing.

    'query' here is treated as the productCode (part number) entered by the user.
    """

    # Step 1: OAuth access token
    access_token = await _get_oauth_access_token()

    # Step 2: x-part-access-token
    part_access_token = await _get_part_access_token(access_token)

    # Step 3: Price & availability request
    body = {
        "showNoStock": True,
        "showLocation": True,
        "productCodes": [query],
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
        "x-boeing-parts-authorization": part_access_token,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(BOEING_PNA_PRICE_URL, headers=headers, json=body)

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    payload = resp.json()

    # Response example provided by user:
    # {
    #   "currency": "USD",
    #   "lineItems": [
    #       {
    #           "aviallPartNumber": "311N5049-173=BC",
    #           "baseUOM": "EA",
    #           "countryOfOrigin": "United States",
    #           "description": "",
    #           "dim": "0 x 0 x 0",
    #           "dimUOM": "IN",
    #           "inStock": false,
    #           "listPrice": 0,
    #           "name": "DOOR: ...",
    #           "netPrice": 0,
    #           "quantity": 0,
    #           "supplierName": "BOEING COMMERCIAL AIRPLANES",
    #           "weight": "0",
    #           "weightUOM": "LB"
    #       },
    #       ...
    #   ]
    # }

    currency = payload.get("currency")
    line_items = payload.get("lineItems") or []

    normalized: List[Dict[str, Any]] = []

    for idx, item in enumerate(line_items):
        part_number = item.get("aviallPartNumber") or ""
        name = item.get("name") or part_number or query
        description = item.get("description") or ""
        manufacturer = item.get("supplierName") or "Boeing"
        distr_src = item.get("supplierName") or ""

        # Dimensions: "0 x 0 x 0" with unit in dimUOM
        dim_str = item.get("dim") or ""
        length = width = height = None
        try:
            parts = [p.strip() for p in dim_str.lower().split("x")] if dim_str else []
            if len(parts) == 3:
                l, w, h = parts
                def _to_float(v: str) -> float | None:
                    try:
                        val = float(v)
                        return val if val != 0 else None
                    except Exception:
                        return None

                length = _to_float(l)
                width = _to_float(w)
                height = _to_float(h)
        except Exception:
            # If parsing fails, keep dimensions as None
            length = width = height = None

        dimension_uom = item.get("dimUOM") or ""

        # Weight is a string; convert to float, ignore zeros
        weight_val = None
        weight_str = item.get("weight")
        if weight_str is not None:
            try:
                w = float(weight_str)
                weight_val = w if w != 0 else None
            except Exception:
                weight_val = None

        weight_unit = item.get("weightUOM") or ""

        # Pricing and inventory from Boeing response
        list_price = item.get("listPrice")
        net_price = item.get("netPrice")
        quantity = item.get("quantity")
        in_stock = item.get("inStock")

        # Prefer netPrice as actual price, fallback to listPrice
        price_val = None
        try:
            if net_price is not None:
                price_val = float(net_price)
            elif list_price is not None:
                price_val = float(list_price)
        except Exception:
            price_val = None

        inventory_val = None
        try:
            if quantity is not None:
                inventory_val = int(quantity)
        except Exception:
            inventory_val = None

        availability = None
        if inventory_val is not None and inventory_val > 0 and (in_stock is True or in_stock is None):
            availability = "in_stock"
        elif in_stock is False or (inventory_val is not None and inventory_val == 0):
            availability = "out_of_stock"

        normalized.append(
            {
                # Primary key used in product_staging
                "id": part_number or f"boeing-{idx}",
                # Core descriptive fields
                "name": name,
                "description": description,
                # Align with Boeing response where possible
                "aviallPartNumber": part_number,
                "baseUOM": item.get("baseUOM"),
                "countryOfOrigin": item.get("countryOfOrigin"),
                "hazmatCode": item.get("hazmatCode"),
                "supplierName": item.get("supplierName"),
                # Normalized fields expected by frontend
                "partNumber": part_number,
                "manufacturer": manufacturer,
                "distrSrc": distr_src,
                "pnAUrl": "",  # Not provided in this response format
                "length": length,
                "width": width,
                "height": height,
                "dimensionUom": dimension_uom,
                "weight": weight_val,
                "weightUnit": weight_unit,
                "price": price_val,
                "inventory": inventory_val,
                "availability": availability,
                "currency": currency,
                # Raw data for auditing
                "rawBoeingData": {
                    **item,
                    "currency": currency,
                },
            }
        )

    # Persist raw payload and normalized rows to Supabase
    await insert_boeing_raw_data(search_query=query, raw_payload=payload)
    await upsert_product_staging(normalized)

    return normalized
