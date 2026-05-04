"""
Manual sync script — standalone emergency re-sync of all active products.

Bypasses Celery/Redis entirely. Fetches all active products from the
product_sync_schedule table, queries Boeing API in batches of 10,
detects changes via hash comparison, and (in production mode) pushes
updates to Shopify and persists new hashes to the database.

Modes:
  TESTING     — Real Boeing API calls, real change detection, emails sent,
                but NO Shopify writes and NO database writes.
  PRODUCTION  — Full end-to-end: Boeing fetch, Shopify update, DB persist.

Usage:
  cd backend/
  python -m scripts.manual_sync

Version: 1.0.0
"""

import asyncio
import hashlib
import json
import logging
import math
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from dotenv import load_dotenv

# ── Bootstrap ────────────────────────────────────────────────────────────────
load_dotenv()

from app.core.config import Settings  # noqa: E402
from app.core.constants.pricing import MARKUP_FACTOR  # noqa: E402

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("manual_sync")

# ── Constants ────────────────────────────────────────────────────────────────
BATCH_SIZE = 10
BOEING_DELAY_SECONDS = 30
SHOPIFY_DELAY_SECONDS = 2
RETRY_DELAY_SECONDS = 60
MAX_RETRIES = 1

# Colors for terminal output
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_RED = "\033[91m"
C_CYAN = "\033[96m"
C_DIM = "\033[2m"


# ═════════════════════════════════════════════════════════════════════════════
#  HELPER FUNCTIONS
# ═════════════════════════════════════════════════════════════════════════════


def compute_boeing_hash(boeing_data: Dict[str, Any]) -> str:
    """Compute SHA-256 hash (16 chars) — identical to app.utils.hash_utils."""
    relevant_data = {
        "price": boeing_data.get("list_price") or boeing_data.get("net_price"),
        "quantity": boeing_data.get("inventory_quantity", 0),
        "status": boeing_data.get("inventory_status"),
        "locations": boeing_data.get("location_summary"),
    }
    json_str = json.dumps(relevant_data, sort_keys=True, default=str)
    return hashlib.sha256(json_str.encode()).hexdigest()[:16]


def extract_boeing_product_data(
    boeing_response: Dict[str, Any], sku: str
) -> Optional[Dict[str, Any]]:
    """Extract normalized product data — mirrors app.utils.boeing_data_extract."""
    line_items = boeing_response.get("lineItems", [])
    currency = boeing_response.get("currency", "USD")

    for item in line_items:
        aviall_part = item.get("aviallPartNumber", "")
        product_code = item.get("productCode", "")

        if aviall_part.upper() != sku.upper() and product_code.upper() != sku.upper():
            continue

        list_price = _to_float(item.get("listPrice"))
        net_price = _to_float(item.get("netPrice"))

        location_availabilities = item.get("locationAvailabilities") or []
        total_quantity = 0
        location_quantities = []

        for loc in location_availabilities:
            loc_name = loc.get("location", "")
            loc_qty = _to_int(loc.get("availQuantity")) or 0
            total_quantity += loc_qty
            if loc_name:
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
                loc_qty = _to_int(loc.get("availQuantity"))
                if loc_name:
                    parts.append(f"{loc_name}: {loc_qty if loc_qty is not None else 0}")
            location_summary = "; ".join(parts) if parts else None

        return {
            "sku": sku,
            "boeing_sku": aviall_part or sku,
            "list_price": list_price,
            "net_price": net_price,
            "currency": currency,
            "inventory_quantity": total_quantity,
            "inventory_status": inventory_status,
            "in_stock": in_stock,
            "locations": location_quantities,
            "location_quantities": location_quantities,
            "location_summary": location_summary,
            "is_missing_sku": False,
        }

    return None


def create_out_of_stock_data(sku: str) -> Dict[str, Any]:
    """Synthetic out-of-stock record — mirrors app.utils.boeing_data_extract."""
    return {
        "sku": sku,
        "boeing_sku": sku,
        "list_price": None,
        "net_price": None,
        "currency": "USD",
        "inventory_quantity": 0,
        "inventory_status": "out_of_stock",
        "in_stock": False,
        "locations": [],
        "location_quantities": [],
        "location_summary": None,
        "is_missing_sku": True,
    }


def should_update_shopify(
    new_data: Dict[str, Any],
    last_hash: Optional[str],
    last_price: Optional[float],
    last_quantity: Optional[int],
) -> Tuple[bool, str]:
    """Change detection — mirrors app.utils.change_detection."""
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


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


# ═════════════════════════════════════════════════════════════════════════════
#  BOEING API CLIENT (standalone, no Celery/Redis)
# ═════════════════════════════════════════════════════════════════════════════


class BoeingAPI:
    """Standalone Boeing API client with token caching."""

    def __init__(self, settings: Settings) -> None:
        self._oauth_url = settings.boeing_oauth_token_url
        self._client_id = settings.boeing_client_id
        self._client_secret = settings.boeing_client_secret
        self._scope = settings.boeing_scope
        self._pna_oauth_url = settings.boeing_pna_oauth_url
        self._pna_price_url = settings.boeing_pna_price_url
        self._username = settings.boeing_username
        self._password = settings.boeing_password
        # Cached tokens
        self._access_token: Optional[str] = None
        self._part_token: Optional[str] = None

    async def authenticate(self) -> None:
        """Fetch and cache both OAuth and PNA tokens."""
        logger.info("Authenticating with Boeing API...")

        # Step 1: OAuth access token
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                self._oauth_url,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": self._scope,
                    "grant_type": "client_credentials",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code != 200:
            raise RuntimeError(f"Boeing OAuth failed ({resp.status_code}): {resp.text[:200]}")
        self._access_token = resp.json().get("access_token")
        if not self._access_token:
            raise RuntimeError("No access_token in Boeing OAuth response")

        # Step 2: PNA part access token
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                self._pna_oauth_url,
                headers={
                    "x-username": self._username or "",
                    "x-password": self._password or "",
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code != 200:
            raise RuntimeError(f"Boeing PNA auth failed ({resp.status_code}): {resp.text[:200]}")

        self._part_token = resp.headers.get("x-part-access-token")
        if not self._part_token:
            try:
                body = resp.json()
            except Exception:
                body = {}
            self._part_token = body.get("x-part-access-token") or body.get("access_token")
        if not self._part_token:
            raise RuntimeError("Missing x-part-access-token in Boeing PNA response")

        logger.info("Boeing authentication successful")

    async def fetch_batch(
        self, part_numbers: List[str], _retry: int = 0
    ) -> Dict[str, Any]:
        """Fetch price/availability for a batch of part numbers."""
        if not self._access_token or not self._part_token:
            await self.authenticate()

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                self._pna_price_url,
                json={
                    "showNoStock": True,
                    "showLocation": True,
                    "productCodes": part_numbers,
                },
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._access_token}",
                    "x-boeing-parts-authorization": self._part_token or "",
                },
            )

        if resp.status_code == 401 and _retry == 0:
            # Token expired — re-authenticate and retry once
            logger.warning("Boeing token expired, re-authenticating...")
            await self.authenticate()
            return await self.fetch_batch(part_numbers, _retry=1)

        if resp.status_code != 200:
            raise RuntimeError(
                f"Boeing API error ({resp.status_code}): {resp.text[:500]}"
            )

        return resp.json()


# ═════════════════════════════════════════════════════════════════════════════
#  SHOPIFY API CLIENT (standalone)
# ═════════════════════════════════════════════════════════════════════════════


class ShopifyAPI:
    """Standalone Shopify Admin API client."""

    def __init__(self, settings: Settings) -> None:
        domain = settings.shopify_store_domain or ""
        domain = domain.replace("https://", "").replace("http://", "").rstrip("/")
        if not domain.endswith(".myshopify.com"):
            domain = f"{domain}.myshopify.com"
        self._base_url = f"https://{domain}/admin/api/{settings.shopify_api_version}"
        self._token = settings.shopify_admin_api_token or ""

    async def _call(
        self,
        method: str,
        path: str,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        _retry: int = 0,
    ) -> Dict[str, Any]:
        """Execute a Shopify REST call with automatic 429 retry.

        Shopify returns 429 when the leaky-bucket rate limit is hit.
        The Retry-After header tells us how long to wait. We retry
        up to 3 times with the server-suggested delay (or 2s fallback).
        """
        max_retries = 3
        url = f"{self._base_url}{path}"
        headers = {
            "X-Shopify-Access-Token": self._token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method=method, url=url, headers=headers,
                json=json_data, params=params,
            )

        if resp.status_code == 429 and _retry < max_retries:
            # Shopify sends Retry-After header (seconds to wait)
            retry_after = float(resp.headers.get("Retry-After", "2.0"))
            retry_after = min(retry_after, 10.0)  # cap at 10s
            logger.warning(
                f"Shopify 429 rate limit on {method} {path}, "
                f"waiting {retry_after}s (attempt {_retry + 1}/{max_retries})"
            )
            await asyncio.sleep(retry_after)
            return await self._call(method, path, json_data, params, _retry + 1)

        if resp.status_code >= 400:
            raise RuntimeError(
                f"Shopify API error ({resp.status_code}): {resp.text[:500]}"
            )
        return resp.json() if resp.text else {}

    async def update_product_pricing(
        self,
        shopify_product_id: str,
        price: Optional[float] = None,
        quantity: Optional[int] = None,
        metafields: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Update product variant price, optional inventory, and metafields."""
        data = await self._call("GET", f"/products/{shopify_product_id}.json")
        variants = (data.get("product") or {}).get("variants") or []
        if not variants:
            raise RuntimeError(f"Product {shopify_product_id} has no variants")

        variant = variants[0]
        variant_update: Dict[str, Any] = {"id": variant.get("id")}
        if price is not None:
            variant_update["price"] = str(price)

        payload: Dict[str, Any] = {
            "product": {
                "id": int(shopify_product_id),
                "variants": [variant_update],
            }
        }
        if metafields:
            payload["product"]["metafields"] = metafields

        result = await self._call(
            "PUT", f"/products/{shopify_product_id}.json", json_data=payload
        )

        if quantity is not None and variant.get("inventory_item_id"):
            await self._set_inventory(
                variant["inventory_item_id"], quantity
            )

        return result

    async def update_inventory_by_location(
        self,
        shopify_product_id: str,
        location_quantities: List[Dict[str, Any]],
        boeing_to_shopify_map: Dict[str, str],
        default_location_name: Optional[str] = None,
    ) -> None:
        """Update per-location inventory levels with proper location mapping.

        Args:
            shopify_product_id: Shopify product ID.
            location_quantities: Boeing location data [{location, quantity}].
            boeing_to_shopify_map: SHOPIFY_LOCATION_MAP from .env that maps
                Boeing location names to Shopify location names.
                e.g., {"Dallas Central": "Dallas", "Miami, FL": "Miami"}
            default_location_name: Default Shopify location to disconnect
                if it's not in the mapped set (prevents phantom inventory).
        """
        data = await self._call("GET", f"/products/{shopify_product_id}.json")
        variants = (data.get("product") or {}).get("variants") or []
        if not variants:
            return
        inventory_item_id = variants[0].get("inventory_item_id")
        if not inventory_item_id:
            return

        # Get Shopify location name -> ID mapping
        loc_data = await self._call("GET", "/locations.json")
        locations = loc_data.get("locations") or []
        shopify_loc_map = {
            loc["name"]: loc["id"] for loc in locations if loc.get("name")
        }

        # First: zero out ALL Shopify locations for this item
        # This prevents stale quantities from previous syncs lingering
        # on locations that Boeing no longer reports
        for loc_name, loc_id in shopify_loc_map.items():
            try:
                await self._call(
                    "POST",
                    "/inventory_levels/set.json",
                    json_data={
                        "location_id": loc_id,
                        "inventory_item_id": int(inventory_item_id),
                        "available": 0,
                    },
                )
            except Exception:
                pass  # Location may not be connected to this item

        # Then: set the correct quantities from Boeing with name mapping
        matched = 0
        matched_loc_ids = set()
        total_qty = 0
        for lq in location_quantities:
            boeing_loc_name = lq.get("location", "")
            qty = lq.get("quantity", 0)
            total_qty += qty

            # Apply SHOPIFY_LOCATION_MAP translation
            # "Dallas Central" -> "Dallas", "Miami, FL" -> "Miami"
            shopify_name = boeing_to_shopify_map.get(boeing_loc_name, boeing_loc_name)
            location_id = shopify_loc_map.get(shopify_name)

            if location_id:
                await self._call(
                    "POST",
                    "/inventory_levels/set.json",
                    json_data={
                        "location_id": location_id,
                        "inventory_item_id": int(inventory_item_id),
                        "available": qty,
                    },
                )
                matched += 1
                matched_loc_ids.add(location_id)
                logger.debug(
                    f"  Set {shopify_name} (from '{boeing_loc_name}'): {qty}"
                )
            else:
                logger.warning(
                    f"  Unmapped Boeing location '{boeing_loc_name}' "
                    f"(mapped to '{shopify_name}') — no Shopify location found"
                )

        # Fallback: if zero locations matched, set total to first location
        if matched == 0 and shopify_loc_map:
            first_loc_id = next(iter(shopify_loc_map.values()))
            await self._call(
                "POST",
                "/inventory_levels/set.json",
                json_data={
                    "location_id": first_loc_id,
                    "inventory_item_id": int(inventory_item_id),
                    "available": total_qty,
                },
            )

    async def _set_inventory(self, inventory_item_id: int, quantity: int) -> None:
        """Set inventory for default location."""
        loc_data = await self._call("GET", "/locations.json")
        locations = loc_data.get("locations") or []
        if not locations:
            return
        location_id = locations[0]["id"]
        await self._call(
            "POST",
            "/inventory_levels/set.json",
            json_data={
                "location_id": location_id,
                "inventory_item_id": int(inventory_item_id),
                "available": quantity,
            },
        )

    async def find_product_by_sku(self, sku: str) -> Optional[str]:
        """Find a Shopify product ID by SKU using GraphQL.

        Searches Shopify's variant catalog for the given SKU (base part
        number without variant suffix). Returns the numeric product ID
        or None if not found.
        """
        query = (
            'query FindBySku($q: String!) { '
            'productVariants(first: 5, query: $q) { '
            'edges { node { sku product { id } } } } }'
        )
        try:
            data = await self._call(
                "POST",
                "/graphql.json",
                json_data={"query": query, "variables": {"q": f"sku:{sku}"}},
            )
            edges = (
                (data.get("data") or {})
                .get("productVariants", {})
                .get("edges", [])
            )
            for edge in edges:
                node = edge.get("node") or {}
                if node.get("sku") == sku:
                    product_gid = (node.get("product") or {}).get("id", "")
                    if product_gid:
                        return product_gid.rsplit("/", 1)[-1]
            return None
        except Exception as e:
            logger.warning(f"GraphQL SKU search failed for {sku}: {e}")
            return None


def strip_variant_suffix(sku: str) -> str:
    """Strip the Boeing variant suffix (e.g., '=K3', '=9B') from a SKU."""
    return sku.split("=", 1)[0] if sku else ""


# ═════════════════════════════════════════════════════════════════════════════
#  EMAIL BUILDER
# ═════════════════════════════════════════════════════════════════════════════


def build_start_email(
    product_count: int,
    batch_count: int,
    eta_minutes: int,
    mode: str,
    timestamp: str,
) -> str:
    """Build the 'Manual Sync Starting' HTML email."""
    mode_badge = (
        '<span style="background:#e74c3c;color:#fff;padding:2px 8px;border-radius:4px;'
        'font-size:11px;font-weight:bold;">PRODUCTION</span>'
        if mode == "production"
        else '<span style="background:#f39c12;color:#fff;padding:2px 8px;border-radius:4px;'
        'font-size:11px;font-weight:bold;">TESTING (DRY RUN)</span>'
    )

    hours, mins = divmod(eta_minutes, 60)
    eta_display = f"{hours}h {mins}m" if hours > 0 else f"{mins}m"

    def _row(label: str, value: str) -> str:
        return (
            f'<tr><td style="font-size:14px;color:#888;padding:8px 12px;'
            f'border-bottom:1px solid #f0f0f0;width:200px;">{label}</td>'
            f'<td style="font-size:14px;color:#333;font-weight:600;padding:8px 12px;'
            f'border-bottom:1px solid #f0f0f0;">{value}</td></tr>'
        )

    return f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:650px;margin:0 auto;background:#ffffff;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#1a3a5c;border-radius:8px 8px 0 0;">
<tr><td style="padding:20px 24px;">
  <div style="color:#ffffff;font-size:20px;font-weight:bold;">Manual Sync — Starting {mode_badge}</div>
  <div style="color:#ffffffcc;font-size:13px;margin-top:4px;">{timestamp}</div>
</td></tr>
</table>
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f7ff;padding:16px 24px;">
<tr><td style="padding:12px 0;">
  <div style="font-size:15px;color:#1a3a5c;line-height:1.5;">
    A manual sync has been triggered. All active products will be checked
    against the Boeing API for pricing and availability changes.
    {"<br><strong>Testing mode:</strong> No Shopify or database writes will be made." if mode == "testing" else ""}
  </div>
</td></tr>
</table>
<table width="100%" cellpadding="0" cellspacing="0" style="padding:16px 24px;">
  {_row("Products to Sync", f"{product_count:,}")}
  {_row("Boeing API Batches", str(batch_count))}
  {_row("Batch Delay", f"{BOEING_DELAY_SECONDS}s between batches")}
  {_row("Estimated Duration", f"~{eta_display}")}
  {_row("Mode", mode.upper())}
</table>
<table width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #e9ecef;">
<tr><td style="padding:14px 24px;text-align:center;font-size:11px;color:#999;">
  Manual sync triggered via scripts/manual_sync.py
</td></tr>
</table>
</div>"""


def build_completion_email(
    stats: Dict[str, Any],
    changed_products: List[Dict[str, str]],
    failed_products: List[Dict[str, str]],
    out_of_stock_skus: List[str],
    duration_display: Optional[str],
    timestamp: str,
) -> str:
    """Build the completion report — identical to auto-sync cycle report.

    Uses the same HTML template, colors, metric cards, donut chart,
    changes table, failures table, and OOS section as report_service.py.
    Stakeholders should not be able to tell this apart from the auto-sync.
    """
    import html as html_mod
    from collections import Counter

    CLR_H = "#1a1a2e"
    CLR_S = "#27ae60"
    CLR_F = "#e74c3c"
    CLR_CH = "#f39c12"
    CLR_N = "#6c7a89"
    CLR_UN = "#3498db"
    CLR_OOS = "#e67e22"
    CLR_BG = "#e8e8e8"
    CLR_BAR = "#3498db"

    total = stats["total"]
    success = stats["total"] - stats["failed"]
    failed = stats["failed"]
    changes_count = stats["changed"]
    unchanged = stats["unchanged"]

    # ── Header ───────────────────────────────────────────────────────────
    duration_badge = ""
    if duration_display:
        duration_badge = (
            f' <span style="background:#ffffff33;padding:2px 8px;'
            f'border-radius:4px;font-size:11px;margin-left:8px;">'
            f'{duration_display}</span>'
        )
    header = f"""<table width="100%" cellpadding="0" cellspacing="0" style="background:{CLR_H};border-radius:8px 8px 0 0;">
<tr><td style="padding:20px 24px;">
  <div style="color:#ffffff;font-size:20px;font-weight:bold;margin:0;">Boeing Data Hub — Sync Cycle Complete{duration_badge}</div>
  <div style="color:#ffffffcc;font-size:13px;margin-top:4px;">{timestamp}</div>
</td></tr>
</table>"""

    # ── Metric cards ─────────────────────────────────────────────────────
    def _card(value: int, label: str, color: str) -> str:
        return (
            f'<td style="padding:6px;" width="20%">'
            f'<table width="100%" cellpadding="0" cellspacing="0" '
            f'style="background:#f8f9fa;border-radius:6px;border:1px solid #e9ecef;">'
            f'<tr><td style="text-align:center;padding:14px 8px 4px;">'
            f'<div style="font-size:28px;font-weight:bold;color:{color};">{value}</div>'
            f'</td></tr><tr><td style="text-align:center;padding:2px 8px 12px;">'
            f'<div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:0.5px;">{label}</div>'
            f'</td></tr></table></td>'
        )

    metrics = (
        f'<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f0f5;padding:12px 8px;"><tr>'
        f'{_card(total, "Total", CLR_N)}'
        f'{_card(success, "Success", CLR_S)}'
        f'{_card(failed, "Failed", CLR_F)}'
        f'{_card(changes_count, "Changed", CLR_CH)}'
        f'{_card(unchanged, "Unchanged", CLR_UN)}'
        f'</tr></table>'
    )

    # ── Donut chart ──────────────────────────────────────────────────────
    donut = ""
    if total > 0:
        s_pct = success / total if total else 0
        f_pct = failed / total if total else 0
        s_rate = round(s_pct * 100, 1)
        r = 40
        circ = 2 * math.pi * r
        s_dash = s_pct * circ
        f_dash = f_pct * circ

        svg = (
            f'<svg width="110" height="110" viewBox="0 0 110 110" xmlns="http://www.w3.org/2000/svg">'
            f'<circle cx="55" cy="55" r="{r}" fill="none" stroke="{CLR_BG}" stroke-width="12"/>'
            f'<circle cx="55" cy="55" r="{r}" fill="none" stroke="{CLR_S}" stroke-width="12" '
            f'stroke-dasharray="{s_dash} {circ}" stroke-dashoffset="0" transform="rotate(-90 55 55)"/>'
            f'<circle cx="55" cy="55" r="{r}" fill="none" stroke="{CLR_F}" stroke-width="12" '
            f'stroke-dasharray="{f_dash} {circ}" stroke-dashoffset="{-s_dash}" transform="rotate(-90 55 55)"/>'
            f'<text x="55" y="52" text-anchor="middle" font-size="16" font-weight="bold" fill="{CLR_H}">{s_rate}%</text>'
            f'<text x="55" y="66" text-anchor="middle" font-size="9" fill="#888">success</text></svg>'
        )
        legend = ""
        if success > 0:
            legend += (
                f'<tr><td style="padding:2px 6px 2px 0;"><span style="display:inline-block;width:10px;height:10px;'
                f'border-radius:50%;background:{CLR_S};"></span></td>'
                f'<td style="font-size:13px;color:#444;padding:2px 0;">{success} Success</td></tr>'
            )
        if failed > 0:
            legend += (
                f'<tr><td style="padding:2px 6px 2px 0;"><span style="display:inline-block;width:10px;height:10px;'
                f'border-radius:50%;background:{CLR_F};"></span></td>'
                f'<td style="font-size:13px;color:#444;padding:2px 0;">{failed} Failed</td></tr>'
            )
        text_fb = f'<div style="font-size:12px;color:#888;margin-top:4px;">Success rate: {s_rate}% ({success}/{total})</div>'
        donut = (
            f'<table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;padding:16px 24px;">'
            f'<tr><td style="padding:0 0 6px;font-size:14px;font-weight:bold;color:{CLR_H};" colspan="2">Status Breakdown</td></tr>'
            f'<tr><td style="vertical-align:middle;width:130px;">{svg}{text_fb}</td>'
            f'<td style="vertical-align:middle;padding-left:16px;"><table cellpadding="0" cellspacing="0">{legend}</table></td></tr></table>'
        )

    # ── Changes table ────────────────────────────────────────────────────
    changes_html = ""
    changes_dict = {cp["sku"]: cp["reason"] for cp in changed_products}
    sec_title = f'<td colspan="2" style="padding:0 0 8px;font-size:14px;font-weight:bold;color:{CLR_H};">Changes This Cycle ({len(changes_dict)})</td>'
    if not changes_dict:
        changes_html = (
            f'<table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;padding:16px 24px;">'
            f'<tr>{sec_title}</tr>'
            f'<tr><td style="font-size:13px;color:#888;padding:4px 0;">No changes detected this cycle</td></tr></table>'
        )
    else:
        rows = ""
        for sku, reason in sorted(changes_dict.items()):
            rows += (
                f'<tr><td style="font-size:12px;color:#333;padding:6px 8px;border-bottom:1px solid #f0f0f0;">{html_mod.escape(sku)}</td>'
                f'<td style="font-size:12px;color:#555;padding:6px 8px;border-bottom:1px solid #f0f0f0;">{html_mod.escape(reason[:120])}</td></tr>'
            )
        changes_html = (
            f'<table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;padding:16px 24px;">'
            f'<tr>{sec_title}</tr><tr><td colspan="2">'
            f'<table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e9ecef;border-radius:6px;">'
            f'<tr style="background:#f8f9fa;">'
            f'<td style="font-size:10px;font-weight:bold;color:#888;padding:8px;text-transform:uppercase;border-bottom:1px solid #e9ecef;">SKU</td>'
            f'<td style="font-size:10px;font-weight:bold;color:#888;padding:8px;text-transform:uppercase;border-bottom:1px solid #e9ecef;">Change</td></tr>'
            f'{rows}</table></td></tr></table>'
        )

    # ── Failures table with error grouping ───────────────────────────────
    failures_html = ""
    f_title = f'<td colspan="3" style="padding:0 0 8px;font-size:14px;font-weight:bold;color:{CLR_H};">Failures ({len(failed_products)})</td>'
    if not failed_products:
        failures_html = (
            f'<table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;padding:16px 24px;">'
            f'<tr>{f_title}</tr>'
            f'<tr><td style="font-size:13px;color:#888;padding:4px 0;">No failures this cycle</td></tr></table>'
        )
    else:
        rows = ""
        for fp in failed_products[:30]:
            rows += (
                f'<tr><td style="font-size:12px;color:#333;padding:6px 8px;border-bottom:1px solid #f0f0f0;">{html_mod.escape(fp["sku"])}</td>'
                f'<td style="font-size:12px;color:#555;padding:6px 8px;border-bottom:1px solid #f0f0f0;">{html_mod.escape(fp["error"][:120])}</td>'
                f'<td style="font-size:12px;color:{CLR_F};padding:6px 8px;border-bottom:1px solid #f0f0f0;text-align:center;">1</td></tr>'
            )
        trunc = ""
        if len(failed_products) > 30:
            trunc = f'<tr><td colspan="3" style="font-size:11px;color:#999;padding:8px;text-align:center;">... and {len(failed_products)-30} more failures</td></tr>'
        failures_html = (
            f'<table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;padding:16px 24px;">'
            f'<tr>{f_title}</tr><tr><td colspan="3">'
            f'<table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e9ecef;border-radius:6px;">'
            f'<tr style="background:#f8f9fa;">'
            f'<td style="font-size:11px;font-weight:bold;color:#666;padding:8px;text-transform:uppercase;border-bottom:1px solid #e9ecef;">SKU</td>'
            f'<td style="font-size:11px;font-weight:bold;color:#666;padding:8px;text-transform:uppercase;border-bottom:1px solid #e9ecef;">Error</td>'
            f'<td style="font-size:11px;font-weight:bold;color:#666;padding:8px;text-transform:uppercase;border-bottom:1px solid #e9ecef;width:40px;text-align:center;">Fails</td></tr>'
            f'{rows}{trunc}</table></td></tr></table>'
        )

    # ── Out of Stock summary ─────────────────────────────────────────────
    oos_html = ""
    if out_of_stock_skus:
        sku_list = ", ".join(out_of_stock_skus[:20])
        more = f" ... and {len(out_of_stock_skus)-20} more" if len(out_of_stock_skus) > 20 else ""
        oos_html = (
            f'<table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;padding:16px 24px;">'
            f'<tr><td style="padding:0 0 8px;font-size:14px;font-weight:bold;color:{CLR_OOS};">Out of Stock ({len(out_of_stock_skus)})</td></tr>'
            f'<tr><td style="font-size:12px;color:#666;line-height:1.5;">{sku_list}{more}</td></tr></table>'
        )

    # ── Footer ───────────────────────────────────────────────────────────
    footer = """<table width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #e9ecef;">
<tr><td style="padding:14px 24px;text-align:center;font-size:11px;color:#999;">
  Auto-generated by Boeing Data Hub Sync System
</td></tr>
</table>"""

    return f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:650px;margin:0 auto;background:#ffffff;">
{header}
{metrics}
{donut}
{changes_html}
{failures_html}
{oos_html}
{footer}
</div>"""


# ═════════════════════════════════════════════════════════════════════════════
#  EMAIL SENDER
# ═════════════════════════════════════════════════════════════════════════════


def send_email(
    settings: Settings,
    subject: str,
    html_body: str,
    max_retries: int = 3,
    recipients_override: Optional[List[str]] = None,
) -> bool:
    """Send email via Resend with retry on 429 rate limit.

    Resend enforces 5 requests/second. If hit, waits and retries up to
    max_retries times with increasing backoff (2s, 4s, 8s).

    Args:
        recipients_override: If provided, send to these addresses instead
                             of settings.report_recipients.
    """
    recipients = recipients_override or settings.report_recipients
    if not recipients or not settings.resend_api_key:
        logger.warning("Email skipped — no recipients or no RESEND_API_KEY")
        return False

    import resend

    resend.api_key = settings.resend_api_key
    params: resend.Emails.SendParams = {
        "from": settings.resend_from_address,
        "to": recipients,
        "subject": subject,
        "html": html_body,
    }

    for attempt in range(max_retries + 1):
        try:
            resend.Emails.send(params)
            logger.info(f"Email sent to {recipients}: {subject}")
            return True
        except Exception as e:
            error_str = str(e).lower()
            is_rate_limit = "rate" in error_str or "429" in error_str or "too many" in error_str
            if is_rate_limit and attempt < max_retries:
                wait = 2 ** (attempt + 1)  # 2s, 4s, 8s
                logger.warning(
                    f"Resend rate limit hit, retrying in {wait}s "
                    f"(attempt {attempt + 1}/{max_retries})..."
                )
                time.sleep(wait)
            else:
                logger.error(f"Failed to send email: {e}")
                return False

    return False


# ═════════════════════════════════════════════════════════════════════════════
#  REPORT FILE WRITER
# ═════════════════════════════════════════════════════════════════════════════


def save_report_file(
    stats: Dict[str, Any],
    changed_products: List[Dict[str, str]],
    unchanged_skus: List[str],
    out_of_stock_skus: List[str],
    failed_products: List[Dict[str, str]],
    mode: str,
    duration_display: str,
    timestamp: str,
    retried_ok: int = 0,
) -> str:
    """Save a plain-text sync report to backend/scripts/reports/."""
    import os

    reports_dir = os.path.join(os.path.dirname(__file__), "reports")
    os.makedirs(reports_dir, exist_ok=True)

    now_file = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    mode_tag = "PROD" if mode == "production" else "TEST"
    filename = f"manual_sync_{mode_tag}_{now_file}.txt"
    filepath = os.path.join(reports_dir, filename)

    lines = []
    lines.append("=" * 70)
    lines.append(f"  MANUAL SYNC REPORT  --  {mode.upper()}")
    lines.append(f"  {timestamp}")
    lines.append("=" * 70)
    lines.append("")

    # Summary
    lines.append("SUMMARY")
    lines.append("-" * 40)
    lines.append(f"  Total Products:     {stats['total']}")
    lines.append(f"  Changed:            {stats['changed']}")
    lines.append(f"  Unchanged:          {stats['unchanged']}")
    lines.append(f"  Out of Stock:       {stats['out_of_stock']}")
    lines.append(f"  Failed:             {stats['failed']}")
    if retried_ok > 0:
        lines.append(f"  Retried OK:         {retried_ok}")
    lines.append(f"  Duration:           {duration_display}")
    lines.append(f"  Mode:               {mode.upper()}")
    lines.append("")

    # Changed products
    if changed_products:
        lines.append(f"CHANGED PRODUCTS ({len(changed_products)})")
        lines.append("-" * 70)
        lines.append(f"  {'SKU':<30}  {'Reason'}")
        lines.append(f"  {'-' * 28}  {'-' * 38}")
        for cp in changed_products:
            lines.append(f"  {cp['sku']:<30}  {cp['reason']}")
        lines.append("")

    # Unchanged products
    if unchanged_skus:
        lines.append(f"UNCHANGED PRODUCTS ({len(unchanged_skus)})")
        lines.append("-" * 40)
        for sku in unchanged_skus:
            lines.append(f"  {sku}")
        lines.append("")

    # Out of stock
    if out_of_stock_skus:
        lines.append(f"OUT OF STOCK ({len(out_of_stock_skus)})")
        lines.append("-" * 40)
        for sku in out_of_stock_skus:
            lines.append(f"  {sku}")
        lines.append("")

    # Failures
    if failed_products:
        lines.append(f"FAILURES ({len(failed_products)})")
        lines.append("-" * 70)
        lines.append(f"  {'SKU':<30}  {'Error'}")
        lines.append(f"  {'-' * 28}  {'-' * 38}")
        for fp in failed_products:
            lines.append(f"  {fp['sku']:<30}  {fp['error'][:120]}")
        lines.append("")

    lines.append("=" * 70)
    lines.append(f"  Report generated by scripts/manual_sync.py")
    lines.append("=" * 70)
    lines.append("")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return filepath


# ═════════════════════════════════════════════════════════════════════════════
#  DATABASE ACCESS (Supabase direct — no ORM)
# ═════════════════════════════════════════════════════════════════════════════


class SupabaseDirect:
    """Lightweight Supabase wrapper using postgrest directly.

    Bypasses the supabase-py SDK (which pulls in storage3/pyiceberg)
    and only uses the PostgREST client for table operations.
    """

    def __init__(self, url: str, key: str) -> None:
        from postgrest import SyncPostgrestClient

        rest_url = f"{url}/rest/v1"
        self._pg = SyncPostgrestClient(
            rest_url,
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
            },
        )

    def table(self, name: str):
        return self._pg.from_(name)


def get_supabase_client(settings: Settings) -> SupabaseDirect:
    """Initialize a lightweight Supabase client (PostgREST only)."""
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
    return SupabaseDirect(settings.supabase_url, settings.supabase_service_role_key)


def fetch_all_sync_products(supabase) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Fetch ALL products from product_sync_schedule (active + deactivated).

    Deactivated products (is_active=false) are included because the manual
    sync script is meant to cover ALL published products. Their is_active
    flag is re-enabled after a successful sync in Phase 4.

    Returns:
        Tuple of (all_products, reactivated_skus) where reactivated_skus
        lists the SKUs that were previously deactivated.
    """
    result = (
        supabase.table("product_sync_schedule")
        .select("*")
        .execute()
    )
    all_products = result.data or []
    reactivated_skus = [
        p["sku"] for p in all_products if not p.get("is_active")
    ]
    return all_products, reactivated_skus


def fetch_product_records(supabase, skus: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch product records (with shopify_product_id) keyed by SKU."""
    if not skus:
        return {}
    # Supabase .in_() has a limit; chunk if needed
    records = {}
    for i in range(0, len(skus), 50):
        chunk = skus[i : i + 50]
        result = (
            supabase.table("product")
            .select("sku,shopify_product_id,user_id,price,cost_per_item,inventory_quantity")
            .in_("sku", chunk)
            .execute()
        )
        for row in result.data or []:
            records[row["sku"]] = row
    return records


def update_sync_success(
    supabase,
    sku: str,
    new_hash: str,
    new_price: Optional[float],
    new_quantity: int,
    inventory_status: Optional[str],
    locations: Optional[List[Dict[str, Any]]],
) -> None:
    """Update product_sync_schedule with successful sync data."""
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "sync_status": "success",
        "last_sync_at": now,
        "last_boeing_hash": new_hash,
        "last_price": new_price,
        "last_quantity": new_quantity,
        "consecutive_failures": 0,
        "last_error": None,
        "is_active": True,  # Re-activate deactivated products
        "updated_at": now,
    }
    if inventory_status:
        payload["last_inventory_status"] = inventory_status
    if locations is not None:
        payload["last_locations"] = locations

    supabase.table("product_sync_schedule").update(payload).eq("sku", sku).execute()


def update_product_pricing(
    supabase,
    sku: str,
    user_id: str,
    price: Optional[float],
    cost: Optional[float],
    inventory: Optional[int],
) -> None:
    """Update the product table with new pricing/inventory."""
    payload = {}
    if price is not None:
        payload["price"] = price
    if cost is not None:
        payload["cost_per_item"] = cost
    if inventory is not None:
        payload["inventory_quantity"] = inventory

    if payload:
        supabase.table("product").update(payload).eq("sku", sku).eq(
            "user_id", user_id
        ).execute()


def get_stakeholder_recipients() -> List[str]:
    """Load stakeholder recipient list from STAKEHOLDER_RECIPIENTS env var.

    This is the list that receives the production completion email,
    which looks identical to the auto-sync report. Separate from
    REPORT_RECIPIENTS (developer-only, used for start notifications).
    """
    import os
    raw = os.getenv("STAKEHOLDER_RECIPIENTS", "[]")
    try:
        recipients = json.loads(raw)
        return recipients if isinstance(recipients, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN SYNC ENGINE
# ═════════════════════════════════════════════════════════════════════════════


async def run_sync(mode: str) -> None:
    """Main sync orchestration."""
    is_production = mode == "production"
    settings = Settings()
    start_time = time.time()
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%b %d, %Y — %H:%M UTC")

    print(f"\n{C_BOLD}{'=' * 60}{C_RESET}")
    print(
        f"{C_BOLD}  MANUAL SYNC — "
        f"{'PRODUCTION' if is_production else 'TESTING (DRY RUN)'}"
        f"{C_RESET}"
    )
    print(f"{C_BOLD}{'=' * 60}{C_RESET}\n")

    # ── Phase 1: Pre-flight ──────────────────────────────────────────────
    print(f"{C_CYAN}[Phase 1] Pre-flight checks...{C_RESET}")

    supabase = get_supabase_client(settings)
    sync_products, reactivated_skus = fetch_all_sync_products(supabase)

    if not sync_products:
        print(f"{C_RED}No products found in product_sync_schedule. Exiting.{C_RESET}")
        return

    skus = [p["sku"] for p in sync_products]
    product_records = fetch_product_records(supabase, skus)

    # Build lookup: sku → sync_schedule record
    sync_lookup: Dict[str, Dict[str, Any]] = {p["sku"]: p for p in sync_products}

    product_count = len(skus)
    batch_count = math.ceil(product_count / BATCH_SIZE)
    api_calls_estimate = batch_count
    eta_minutes = math.ceil(
        (batch_count * BOEING_DELAY_SECONDS) / 60
    ) + 2  # +2 min buffer for Shopify updates

    print(f"  Products to sync:    {C_BOLD}{product_count}{C_RESET}")
    if reactivated_skus:
        print(
            f"  Reactivating:        {C_YELLOW}{len(reactivated_skus)} "
            f"deactivated products included{C_RESET}"
        )
        for rsku in reactivated_skus:
            print(f"    + {rsku}")
    print(f"  Boeing API batches:  {C_BOLD}{batch_count}{C_RESET} (10 SKUs each)")
    print(f"  Batch delay:         {C_BOLD}{BOEING_DELAY_SECONDS}s{C_RESET}")
    print(f"  Estimated duration:  {C_BOLD}~{eta_minutes} minutes{C_RESET}")
    print(f"  Mode:                {C_BOLD}{'PRODUCTION' if is_production else 'TESTING'}{C_RESET}")
    print()

    # ── Send start email ─────────────────────────────────────────────────
    print(f"{C_CYAN}[Phase 1] Sending start notification email...{C_RESET}")
    start_html = build_start_email(
        product_count, batch_count, eta_minutes, mode, timestamp
    )
    date_str = now.strftime("%b %d, %Y")
    mode_label = "PRODUCTION" if is_production else "TEST"
    send_email(settings, f"Manual Sync Starting [{mode_label}] — {date_str}", start_html)
    print()

    # ── Phase 2: Boeing Fetch ────────────────────────────────────────────
    print(f"{C_CYAN}[Phase 2] Fetching from Boeing API...{C_RESET}")

    boeing = BoeingAPI(settings)
    await boeing.authenticate()

    batches = [skus[i : i + BATCH_SIZE] for i in range(0, len(skus), BATCH_SIZE)]

    # Results tracking
    changed_products: List[Dict[str, str]] = []
    unchanged_skus: List[str] = []
    out_of_stock_skus: List[str] = []
    failed_skus: List[Dict[str, str]] = []
    # sku → boeing_data for products needing Shopify update
    pending_shopify_updates: Dict[str, Dict[str, Any]] = {}
    # sku → boeing_data for ALL products (for DB update even if unchanged)
    all_boeing_data: Dict[str, Dict[str, Any]] = {}

    for batch_idx, batch in enumerate(batches):
        batch_num = batch_idx + 1
        print(
            f"  {C_DIM}[Batch {batch_num}/{len(batches)}]{C_RESET} "
            f"Fetching {len(batch)} SKUs: {batch[0]}...{batch[-1]}"
        )

        try:
            boeing_response = await boeing.fetch_batch(batch)

            for sku in batch:
                try:
                    product_data = extract_boeing_product_data(boeing_response, sku)

                    if not product_data:
                        product_data = create_out_of_stock_data(sku)
                        out_of_stock_skus.append(sku)
                        logger.info(f"  {sku} — out of stock (not in Boeing response)")

                    all_boeing_data[sku] = product_data

                    # Compare against stored hash
                    sync_record = sync_lookup.get(sku, {})
                    needs_update, reason = should_update_shopify(
                        product_data,
                        sync_record.get("last_boeing_hash"),
                        sync_record.get("last_price"),
                        sync_record.get("last_quantity"),
                    )

                    if needs_update:
                        changed_products.append({"sku": sku, "reason": reason})
                        pending_shopify_updates[sku] = product_data
                        print(
                            f"    {C_YELLOW}CHANGED{C_RESET}  {sku}: {reason}"
                        )
                    else:
                        unchanged_skus.append(sku)
                        print(f"    {C_GREEN}OK{C_RESET}       {sku}: no change")

                except Exception as sku_err:
                    failed_skus.append({"sku": sku, "error": str(sku_err)})
                    print(f"    {C_RED}ERROR{C_RESET}    {sku}: {sku_err}")

        except Exception as batch_err:
            logger.error(f"Batch {batch_num} failed: {batch_err}")
            for sku in batch:
                failed_skus.append({"sku": sku, "error": str(batch_err)})
            print(f"    {C_RED}BATCH FAILED{C_RESET}: {batch_err}")

        # Delay between batches (skip after last batch)
        if batch_idx < len(batches) - 1:
            print(
                f"  {C_DIM}  Waiting {BOEING_DELAY_SECONDS}s before next batch...{C_RESET}"
            )
            await asyncio.sleep(BOEING_DELAY_SECONDS)

    print()

    # ── Phase 3: Shopify Updates (production only) ───────────────────────
    shopify_failures: List[Dict[str, str]] = []
    resolved_ids: Dict[str, str] = {}  # sku -> corrected shopify_product_id

    if is_production and pending_shopify_updates:
        print(
            f"{C_CYAN}[Phase 3] Updating {len(pending_shopify_updates)} "
            f"products on Shopify...{C_RESET}"
        )
        shopify = ShopifyAPI(settings)

        for idx, (sku, boeing_data) in enumerate(pending_shopify_updates.items()):
            product_rec = product_records.get(sku, {})
            shopify_product_id = product_rec.get("shopify_product_id")

            # ── Resolve real Shopify product ID via GraphQL SKU search ──
            base_sku = strip_variant_suffix(sku)
            real_id = await shopify.find_product_by_sku(base_sku)

            if real_id:
                if shopify_product_id and str(real_id) != str(shopify_product_id):
                    logger.info(
                        f"Shopify ID corrected for {sku}: "
                        f"{shopify_product_id} -> {real_id}"
                    )
                    print(
                        f"    {C_CYAN}ID FIX{C_RESET}   {sku}: "
                        f"{shopify_product_id} -> {real_id}"
                    )
                shopify_product_id = str(real_id)
                resolved_ids[sku] = shopify_product_id
            elif not shopify_product_id:
                msg = f"No Shopify product found for SKU {base_sku}"
                shopify_failures.append({"sku": sku, "error": msg})
                print(f"    {C_RED}SKIP{C_RESET}     {sku}: {msg}")
                continue

            new_price = boeing_data.get("list_price") or boeing_data.get("net_price")
            new_quantity = boeing_data.get("inventory_quantity", 0)
            inventory_status = boeing_data.get("inventory_status")
            location_quantities = boeing_data.get("location_quantities") or []
            location_summary = boeing_data.get("location_summary")
            is_out_of_stock = (
                boeing_data.get("is_missing_sku", False)
                or inventory_status == "out_of_stock"
            )

            shopify_price = round(new_price * MARKUP_FACTOR, 2) if new_price else None

            metafields = None
            if location_summary:
                metafields = [
                    {
                        "namespace": "boeing",
                        "key": "location_summary",
                        "value": location_summary,
                        "type": "single_line_text_field",
                    }
                ]

            try:
                if location_quantities and not is_out_of_stock:
                    await shopify.update_product_pricing(
                        str(shopify_product_id),
                        price=shopify_price,
                        metafields=metafields,
                    )
                    await shopify.update_inventory_by_location(
                        str(shopify_product_id),
                        location_quantities,
                        boeing_to_shopify_map=settings.shopify_location_map,
                        default_location_name=settings.shopify_default_location_name,
                    )
                else:
                    await shopify.update_product_pricing(
                        str(shopify_product_id),
                        price=shopify_price,
                        quantity=new_quantity,
                        metafields=metafields,
                    )

                print(
                    f"    {C_GREEN}UPDATED{C_RESET}  {sku}: "
                    f"price=${shopify_price}, qty={new_quantity}"
                )

            except Exception as shop_err:
                shopify_failures.append({"sku": sku, "error": str(shop_err)})
                print(f"    {C_RED}SHOPIFY ERROR{C_RESET}  {sku}: {shop_err}")

            # Shopify rate limit delay
            if idx < len(pending_shopify_updates) - 1:
                await asyncio.sleep(SHOPIFY_DELAY_SECONDS)

        print()
    elif not is_production and pending_shopify_updates:
        print(
            f"{C_YELLOW}[Phase 3] TESTING MODE — Skipping Shopify updates for "
            f"{len(pending_shopify_updates)} changed products{C_RESET}\n"
        )
    else:
        print(f"{C_GREEN}[Phase 3] No Shopify updates needed — all products unchanged{C_RESET}\n")

    # ── Phase 4: Database Consistency (production only) ──────────────────
    if is_production:
        print(f"{C_CYAN}[Phase 4] Updating database records...{C_RESET}")

        # Combine Shopify failures into failed_skus for retry
        shopify_failed_set = {f["sku"] for f in shopify_failures}

        db_updated = 0
        for sku, boeing_data in all_boeing_data.items():
            # Skip products that failed at Shopify level (they weren't updated)
            if sku in shopify_failed_set:
                continue

            try:
                new_hash = compute_boeing_hash(boeing_data)
                new_price = boeing_data.get("list_price") or boeing_data.get("net_price")
                new_quantity = boeing_data.get("inventory_quantity", 0)
                inventory_status = boeing_data.get("inventory_status")
                location_quantities = boeing_data.get("location_quantities")

                update_sync_success(
                    supabase,
                    sku,
                    new_hash,
                    new_price,
                    new_quantity,
                    inventory_status,
                    location_quantities,
                )

                # Also update the product table
                product_rec = product_records.get(sku)
                if product_rec:
                    user_id = product_rec.get("user_id", "system")
                    shopify_price = round(new_price * MARKUP_FACTOR, 2) if new_price else None
                    update_product_pricing(
                        supabase, sku, user_id, shopify_price, new_price, new_quantity
                    )

                    # Fix stale Shopify product ID if we resolved a new one
                    corrected_id = resolved_ids.get(sku)
                    if corrected_id and str(corrected_id) != str(product_rec.get("shopify_product_id")):
                        try:
                            supabase.table("product") \
                                .update({"shopify_product_id": str(corrected_id)}) \
                                .eq("sku", sku) \
                                .eq("user_id", user_id) \
                                .execute()
                            logger.info(f"Fixed shopify_product_id for {sku}: {corrected_id}")
                        except Exception as id_err:
                            logger.warning(f"Failed to fix shopify_product_id for {sku}: {id_err}")
                else:
                    logger.warning(f"No product record for {sku} — sync_schedule updated, product table skipped")

                db_updated += 1
            except Exception as db_err:
                logger.error(f"DB update failed for {sku}: {db_err}")

        print(f"  Updated {db_updated}/{len(all_boeing_data)} sync records")
        print()
    else:
        print(
            f"{C_YELLOW}[Phase 4] TESTING MODE — Skipping database writes{C_RESET}\n"
        )

    # ── Phase 5: Retry failed products ───────────────────────────────────
    all_failures = failed_skus + shopify_failures
    retried_ok = 0

    if all_failures and MAX_RETRIES > 0:
        retry_skus = list({f["sku"] for f in all_failures})
        print(
            f"{C_CYAN}[Phase 5] Retrying {len(retry_skus)} failed products "
            f"after {RETRY_DELAY_SECONDS}s cooldown...{C_RESET}"
        )
        await asyncio.sleep(RETRY_DELAY_SECONDS)

        retry_batches = [
            retry_skus[i : i + BATCH_SIZE]
            for i in range(0, len(retry_skus), BATCH_SIZE)
        ]

        retry_changed: List[Dict[str, str]] = []
        retry_still_failed: List[Dict[str, str]] = []
        retry_shopify = ShopifyAPI(settings) if is_production else None

        for batch_idx, batch in enumerate(retry_batches):
            try:
                boeing_response = await boeing.fetch_batch(batch)

                for sku_idx, sku in enumerate(batch):
                    try:
                        product_data = extract_boeing_product_data(boeing_response, sku)
                        if not product_data:
                            product_data = create_out_of_stock_data(sku)

                        all_boeing_data[sku] = product_data
                        sync_record = sync_lookup.get(sku, {})
                        needs_update, reason = should_update_shopify(
                            product_data,
                            sync_record.get("last_boeing_hash"),
                            sync_record.get("last_price"),
                            sync_record.get("last_quantity"),
                        )

                        if is_production and needs_update and retry_shopify:
                            product_rec = product_records.get(sku, {})
                            shopify_product_id = product_rec.get("shopify_product_id")
                            if shopify_product_id:
                                new_price = product_data.get("list_price") or product_data.get("net_price")
                                shopify_price = round(new_price * MARKUP_FACTOR, 2) if new_price else None
                                new_quantity = product_data.get("inventory_quantity", 0)
                                await retry_shopify.update_product_pricing(
                                    str(shopify_product_id),
                                    price=shopify_price,
                                    quantity=new_quantity,
                                )
                                await asyncio.sleep(SHOPIFY_DELAY_SECONDS)

                        if is_production:
                            new_hash = compute_boeing_hash(product_data)
                            new_price = product_data.get("list_price") or product_data.get("net_price")
                            update_sync_success(
                                supabase, sku, new_hash, new_price,
                                product_data.get("inventory_quantity", 0),
                                product_data.get("inventory_status"),
                                product_data.get("location_quantities"),
                            )

                        retried_ok += 1
                        if needs_update:
                            retry_changed.append({"sku": sku, "reason": reason})
                        print(f"    {C_GREEN}RETRY OK{C_RESET}  {sku}")

                    except Exception as sku_err:
                        retry_still_failed.append({"sku": sku, "error": str(sku_err)})
                        print(f"    {C_RED}RETRY FAIL{C_RESET}  {sku}: {sku_err}")

            except Exception as batch_err:
                for sku in batch:
                    retry_still_failed.append({"sku": sku, "error": str(batch_err)})
                print(f"    {C_RED}RETRY BATCH FAIL{C_RESET}: {batch_err}")

            if batch_idx < len(retry_batches) - 1:
                await asyncio.sleep(BOEING_DELAY_SECONDS)

        # Merge retry results
        changed_products.extend(retry_changed)
        failed_skus = retry_still_failed  # Replace with only permanent failures
        print()
    else:
        # No retry happened — merge shopify_failures into failed_skus
        failed_skus = failed_skus + shopify_failures
        print(f"{C_GREEN}[Phase 5] No failures to retry{C_RESET}\n")

    # ── Phase 6: Summary & Completion Email ──────────────────────────────
    elapsed = time.time() - start_time
    total_minutes = int(elapsed // 60)
    total_seconds = int(elapsed % 60)
    duration_display = f"{total_minutes}m {total_seconds}s"

    stats = {
        "total": product_count,
        "changed": len(changed_products),
        "unchanged": len(unchanged_skus),
        "out_of_stock": len(out_of_stock_skus),
        "failed": len(failed_skus),
        "retried_ok": retried_ok,
    }

    print(f"{C_BOLD}{'=' * 60}{C_RESET}")
    print(f"{C_BOLD}  SYNC COMPLETE — {duration_display}{C_RESET}")
    print(f"{C_BOLD}{'=' * 60}{C_RESET}")
    print(f"  Total:          {stats['total']}")
    print(f"  {C_YELLOW}Changed:        {stats['changed']}{C_RESET}")
    print(f"  {C_GREEN}Unchanged:      {stats['unchanged']}{C_RESET}")
    print(f"  Out of Stock:   {stats['out_of_stock']}")
    print(f"  {C_RED}Failed:         {stats['failed']}{C_RESET}")
    if retried_ok > 0:
        print(f"  {C_GREEN}Retried OK:     {retried_ok}{C_RESET}")
    print(f"  Duration:       {duration_display}")
    print(f"  Mode:           {'PRODUCTION' if is_production else 'TESTING (DRY RUN)'}")
    print()

    if failed_skus:
        print(f"{C_RED}Permanent failures:{C_RESET}")
        for f in failed_skus:
            print(f"  - {f['sku']}: {f['error'][:100]}")
        print()

    # ── Save report to file ─────────────────────────────────────────────
    report_path = save_report_file(
        stats, changed_products, unchanged_skus, out_of_stock_skus,
        failed_skus, mode, duration_display, timestamp, retried_ok,
    )
    print(f"{C_CYAN}Report saved to: {report_path}{C_RESET}")

    # Send completion email \u2014 DEVELOPER ONLY.
    # Stakeholder email is intentionally NOT sent from here. Use
    # `python -m scripts.send_combined_report --report <path>` after
    # reviewing the .txt report to send the production-grade stakeholder
    # email (same template as the auto-sync cycle report).
    print(f"{C_CYAN}Sending completion email (developer only)...{C_RESET}")
    completion_html = build_completion_email(
        stats, changed_products, failed_skus, out_of_stock_skus,
        duration_display, timestamp,
    )
    send_email(
        settings,
        f"Manual Sync Complete [{mode_label}] -- {date_str}",
        completion_html,
    )

    # \u2500\u2500 Stakeholder email send is disabled. Trigger it manually via: \u2500\u2500\u2500\u2500\u2500
    #   python -m scripts.send_combined_report --report <report.txt> --dry-run
    #   python -m scripts.send_combined_report --report <report.txt>
    # See send_combined_report.py for the production-grade stakeholder
    # template (reuses ReportService._build_* methods directly).

    print(f"\n{C_GREEN}Done.{C_RESET}\n")


# ═════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════


def main() -> None:
    # Force UTF-8 on Windows consoles
    import io, os as _os
    if _os.name == "nt":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    print(f"\n{C_BOLD}Boeing Data Hub -- Manual Sync Script{C_RESET}")
    print(f"{C_DIM}{'-' * 40}{C_RESET}\n")
    print("Select mode:\n")
    print(f"  {C_GREEN}[1]{C_RESET}  Testing     — Real Boeing API calls, NO Shopify/DB writes")
    print(f"  {C_RED}[2]{C_RESET}  Production  — Full sync: Boeing + Shopify + Database")
    print()

    while True:
        choice = input(f"Enter choice (1 or 2): ").strip()
        if choice == "1":
            mode = "testing"
            break
        elif choice == "2":
            mode = "production"
            # Double confirmation for production
            confirm = input(
                f"\n{C_RED}WARNING: This will update Shopify products and "
                f"database records.{C_RESET}\n"
                f"Type 'CONFIRM' to proceed: "
            ).strip()
            if confirm == "CONFIRM":
                break
            else:
                print("Aborted.\n")
                sys.exit(0)
        else:
            print("Invalid choice. Enter 1 or 2.")

    print()
    asyncio.run(run_sync(mode))


if __name__ == "__main__":
    main()
