"""
Fix & verify script — Boeing-authoritative location/pricing repair.

Makes REAL Boeing API calls in batches of 10, uses the response as the
sole source of truth for location mapping and pricing. Publishes correct
inventory to Shopify with SHOPIFY_LOCATION_MAP, then verifies and fixes
any database hash/pricing discrepancies.

Usage:
  cd backend/
  python -m scripts.fix_locations

Version: 2.0.0
"""

import asyncio
import hashlib
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from dotenv import load_dotenv

load_dotenv()

from app.core.config import Settings  # noqa: E402
from app.core.constants.pricing import MARKUP_FACTOR  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("fix_locations")

BATCH_SIZE = 10
BOEING_DELAY_SECONDS = 30
SHOPIFY_DELAY_SECONDS = 2

C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_RED = "\033[91m"
C_CYAN = "\033[96m"
C_DIM = "\033[2m"


# ═════════════════════════════════════════════════════════════════════════════
#  HELPERS (same logic as manual_sync / hash_utils)
# ═════════════════════════════════════════════════════════════════════════════


def compute_boeing_hash(data: Dict[str, Any]) -> str:
    relevant = {
        "price": data.get("list_price") or data.get("net_price"),
        "quantity": data.get("inventory_quantity", 0),
        "status": data.get("inventory_status"),
        "locations": data.get("location_summary"),
    }
    return hashlib.sha256(
        json.dumps(relevant, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def extract_boeing_product_data(
    response: Dict[str, Any], sku: str
) -> Optional[Dict[str, Any]]:
    line_items = response.get("lineItems", [])
    currency = response.get("currency", "USD")
    for item in line_items:
        if (
            item.get("aviallPartNumber", "").upper() != sku.upper()
            and item.get("productCode", "").upper() != sku.upper()
        ):
            continue

        list_price = _to_float(item.get("listPrice"))
        net_price = _to_float(item.get("netPrice"))
        loc_avails = item.get("locationAvailabilities") or []
        total_qty = 0
        location_quantities = []
        for loc in loc_avails:
            name = loc.get("location", "")
            qty = _to_int(loc.get("availQuantity")) or 0
            total_qty += qty
            if name:
                location_quantities.append({"location": name, "quantity": qty})

        in_stock = item.get("inStock")
        status = None
        if in_stock is True or total_qty > 0:
            status = "in_stock"
        elif in_stock is False:
            status = "out_of_stock"

        loc_summary = None
        if loc_avails:
            parts = []
            for loc in loc_avails:
                n = loc.get("location")
                q = _to_int(loc.get("availQuantity"))
                if n:
                    parts.append(f"{n}: {q if q is not None else 0}")
            loc_summary = "; ".join(parts) if parts else None

        return {
            "sku": sku,
            "list_price": list_price,
            "net_price": net_price,
            "currency": currency,
            "inventory_quantity": total_qty,
            "inventory_status": status,
            "location_quantities": location_quantities,
            "location_summary": loc_summary,
            "is_missing_sku": False,
        }
    return None


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _to_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def strip_variant_suffix(sku: str) -> str:
    return sku.split("=", 1)[0] if sku else ""


# ═════════════════════════════════════════════════════════════════════════════
#  SUPABASE (PostgREST direct)
# ═════════════════════════════════════════════════════════════════════════════


class SupabaseDirect:
    def __init__(self, url: str, key: str) -> None:
        from postgrest import SyncPostgrestClient

        self._pg = SyncPostgrestClient(
            f"{url}/rest/v1",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
        )

    def table(self, name: str):
        return self._pg.from_(name)


# ═════════════════════════════════════════════════════════════════════════════
#  BOEING API
# ═════════════════════════════════════════════════════════════════════════════


class BoeingAPI:
    def __init__(self, settings: Settings) -> None:
        self._oauth_url = settings.boeing_oauth_token_url
        self._client_id = settings.boeing_client_id
        self._client_secret = settings.boeing_client_secret
        self._scope = settings.boeing_scope
        self._pna_oauth_url = settings.boeing_pna_oauth_url
        self._pna_price_url = settings.boeing_pna_price_url
        self._username = settings.boeing_username
        self._password = settings.boeing_password
        self._access_token: Optional[str] = None
        self._part_token: Optional[str] = None

    async def authenticate(self) -> None:
        logger.info("Authenticating with Boeing API...")
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
            raise RuntimeError("Missing x-part-access-token")
        logger.info("Boeing authentication successful")

    async def fetch_batch(self, part_numbers: List[str], _retry: int = 0) -> Dict[str, Any]:
        if not self._access_token or not self._part_token:
            await self.authenticate()
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                self._pna_price_url,
                json={"showNoStock": True, "showLocation": True, "productCodes": part_numbers},
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._access_token}",
                    "x-boeing-parts-authorization": self._part_token or "",
                },
            )
        if resp.status_code == 401 and _retry == 0:
            await self.authenticate()
            return await self.fetch_batch(part_numbers, _retry=1)
        if resp.status_code != 200:
            raise RuntimeError(f"Boeing API error ({resp.status_code}): {resp.text[:500]}")
        return resp.json()


# ═════════════════════════════════════════════════════════════════════════════
#  SHOPIFY API
# ═════════════════════════════════════════════════════════════════════════════


class ShopifyAPI:
    def __init__(self, settings: Settings) -> None:
        domain = (settings.shopify_store_domain or "").replace("https://", "").replace("http://", "").rstrip("/")
        if not domain.endswith(".myshopify.com"):
            domain = f"{domain}.myshopify.com"
        self._base_url = f"https://{domain}/admin/api/{settings.shopify_api_version}"
        self._token = settings.shopify_admin_api_token or ""
        self._location_cache: Optional[Dict[str, int]] = None

    async def _call(
        self, method: str, path: str,
        json_data: Optional[Dict[str, Any]] = None,
        _retry: int = 0,
    ) -> Dict[str, Any]:
        headers = {
            "X-Shopify-Access-Token": self._token,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method=method, url=f"{self._base_url}{path}",
                headers=headers, json=json_data,
            )
        if resp.status_code == 429 and _retry < 3:
            wait = float(resp.headers.get("Retry-After", "2.0"))
            logger.warning(f"Shopify 429, waiting {min(wait, 10)}s...")
            await asyncio.sleep(min(wait, 10.0))
            return await self._call(method, path, json_data, _retry + 1)
        if resp.status_code >= 400:
            raise RuntimeError(f"Shopify {resp.status_code}: {resp.text[:300]}")
        return resp.json() if resp.text else {}

    async def get_locations(self) -> Dict[str, int]:
        if self._location_cache:
            return self._location_cache
        data = await self._call("GET", "/locations.json")
        self._location_cache = {
            loc["name"]: loc["id"]
            for loc in (data.get("locations") or [])
            if loc.get("name") and loc.get("id")
        }
        return self._location_cache

    async def find_product_by_sku(self, sku: str) -> Optional[str]:
        query = (
            'query($q:String!){productVariants(first:5,query:$q)'
            '{edges{node{sku product{id}}}}}'
        )
        try:
            data = await self._call(
                "POST", "/graphql.json",
                json_data={"query": query, "variables": {"q": f"sku:{sku}"}},
            )
            for edge in (data.get("data") or {}).get("productVariants", {}).get("edges", []):
                node = edge.get("node") or {}
                if node.get("sku") == sku:
                    gid = (node.get("product") or {}).get("id", "")
                    return gid.rsplit("/", 1)[-1] if gid else None
            return None
        except Exception as e:
            logger.warning(f"GraphQL search failed for {sku}: {e}")
            return None

    async def get_inventory_item_id(self, product_id: str) -> Optional[int]:
        data = await self._call("GET", f"/products/{product_id}.json")
        variants = (data.get("product") or {}).get("variants") or []
        return variants[0].get("inventory_item_id") if variants else None

    async def update_product_pricing(
        self, product_id: str, price: float, metafields: Optional[List[Dict]] = None,
    ) -> None:
        data = await self._call("GET", f"/products/{product_id}.json")
        variants = (data.get("product") or {}).get("variants") or []
        if not variants:
            return
        payload: Dict[str, Any] = {
            "product": {
                "id": int(product_id),
                "variants": [{"id": variants[0]["id"], "price": str(price)}],
            }
        }
        if metafields:
            payload["product"]["metafields"] = metafields
        await self._call("PUT", f"/products/{product_id}.json", json_data=payload)

    async def fix_inventory(
        self,
        inv_item_id: int,
        location_quantities: List[Dict[str, Any]],
        loc_name_map: Dict[str, str],
        shopify_locs: Dict[str, int],
    ) -> Tuple[int, int]:
        """Zero all, then set correct. Returns (matched, total_qty)."""
        # Zero all
        for loc_id in shopify_locs.values():
            try:
                await self._call(
                    "POST", "/inventory_levels/set.json",
                    json_data={"location_id": loc_id, "inventory_item_id": inv_item_id, "available": 0},
                )
            except Exception:
                pass

        # Set correct
        matched = 0
        total_qty = 0
        for lq in location_quantities:
            boeing_name = lq.get("location", "")
            qty = lq.get("quantity", 0)
            total_qty += qty
            shopify_name = loc_name_map.get(boeing_name, boeing_name)
            loc_id = shopify_locs.get(shopify_name)
            if loc_id:
                await self._call(
                    "POST", "/inventory_levels/set.json",
                    json_data={"location_id": loc_id, "inventory_item_id": inv_item_id, "available": qty},
                )
                matched += 1
            else:
                logger.warning(f"    Unmapped: '{boeing_name}' -> '{shopify_name}'")

        if matched == 0 and shopify_locs and total_qty > 0:
            first_id = next(iter(shopify_locs.values()))
            await self._call(
                "POST", "/inventory_levels/set.json",
                json_data={"location_id": first_id, "inventory_item_id": inv_item_id, "available": total_qty},
            )

        return matched, total_qty


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═════════════════════════════════════════════════════════════════════════════


async def run_fix() -> None:
    settings = Settings()
    start_time = time.time()

    print(f"\n{C_BOLD}{'=' * 60}{C_RESET}")
    print(f"{C_BOLD}  FIX & VERIFY — Boeing-authoritative repair{C_RESET}")
    print(f"{C_BOLD}{'=' * 60}{C_RESET}\n")

    # ── Load products ────────────────────────────────────────────────────
    db = SupabaseDirect(settings.supabase_url, settings.supabase_service_role_key)
    sync_data = db.table("product_sync_schedule").select(
        "sku,last_boeing_hash,last_price,last_quantity,last_inventory_status,last_locations"
    ).execute()
    all_products = sync_data.data or []
    skus = [p["sku"] for p in all_products]
    sync_lookup = {p["sku"]: p for p in all_products}

    # Load product table for user_id
    product_lookup: Dict[str, Dict] = {}
    for i in range(0, len(skus), 50):
        chunk = skus[i:i + 50]
        res = db.table("product").select("sku,user_id,shopify_product_id").in_("sku", chunk).execute()
        for r in (res.data or []):
            product_lookup[r["sku"]] = r

    print(f"  Products to fix & verify: {C_BOLD}{len(skus)}{C_RESET}")
    batch_count = math.ceil(len(skus) / BATCH_SIZE)
    print(f"  Boeing API batches:       {C_BOLD}{batch_count}{C_RESET}")
    print(f"  Location map entries:     {len(settings.shopify_location_map)}")
    print()

    # ── Auth ──────────────────────────────────────────────────────────────
    boeing = BoeingAPI(settings)
    await boeing.authenticate()

    shopify = ShopifyAPI(settings)
    shopify_locs = await shopify.get_locations()
    print(f"  Shopify locations: {list(shopify_locs.keys())}\n")

    loc_map = settings.shopify_location_map

    # ── Process in batches ───────────────────────────────────────────────
    batches = [skus[i:i + BATCH_SIZE] for i in range(0, len(skus), BATCH_SIZE)]

    fixed_count = 0
    verified_ok = 0
    db_mismatches: List[Dict[str, str]] = []
    shopify_fixed: List[str] = []
    failed: List[Dict[str, str]] = []

    for batch_idx, batch in enumerate(batches):
        print(
            f"  {C_DIM}[Batch {batch_idx + 1}/{len(batches)}]{C_RESET} "
            f"Fetching {len(batch)} SKUs from Boeing..."
        )

        try:
            boeing_response = await boeing.fetch_batch(batch)
        except Exception as e:
            print(f"    {C_RED}BATCH FAILED{C_RESET}: {e}")
            for sku in batch:
                failed.append({"sku": sku, "error": str(e)})
            if batch_idx < len(batches) - 1:
                await asyncio.sleep(BOEING_DELAY_SECONDS)
            continue

        for sku in batch:
            try:
                boeing_data = extract_boeing_product_data(boeing_response, sku)
                if not boeing_data:
                    boeing_data = {
                        "sku": sku, "list_price": None, "net_price": None,
                        "inventory_quantity": 0, "inventory_status": "out_of_stock",
                        "location_quantities": [], "location_summary": None,
                        "is_missing_sku": True,
                    }

                new_hash = compute_boeing_hash(boeing_data)
                new_price = boeing_data.get("list_price") or boeing_data.get("net_price")
                new_qty = boeing_data.get("inventory_quantity", 0)
                new_status = boeing_data.get("inventory_status")
                location_quantities = boeing_data.get("location_quantities") or []
                location_summary = boeing_data.get("location_summary")

                # ── Verify DB hash ───────────────────────────────────
                db_rec = sync_lookup.get(sku, {})
                db_hash = db_rec.get("last_boeing_hash")
                db_price = db_rec.get("last_price")
                db_qty = db_rec.get("last_quantity")

                hash_match = (db_hash == new_hash)
                price_match = (db_price is None and new_price is None) or (
                    db_price is not None and new_price is not None
                    and abs(float(db_price) - float(new_price)) < 0.01
                )
                qty_match = (db_qty == new_qty)

                if hash_match and price_match and qty_match:
                    verified_ok += 1
                    db_tag = f"{C_GREEN}DB OK{C_RESET}"
                else:
                    mismatches = []
                    if not hash_match:
                        mismatches.append(f"hash: {db_hash} != {new_hash}")
                    if not price_match:
                        mismatches.append(f"price: {db_price} != {new_price}")
                    if not qty_match:
                        mismatches.append(f"qty: {db_qty} != {new_qty}")
                    mismatch_str = "; ".join(mismatches)
                    db_mismatches.append({"sku": sku, "detail": mismatch_str})
                    db_tag = f"{C_YELLOW}DB MISMATCH{C_RESET} ({mismatch_str})"

                    # Fix DB
                    now_iso = datetime.now(timezone.utc).isoformat()
                    db.table("product_sync_schedule").update({
                        "last_boeing_hash": new_hash,
                        "last_price": new_price,
                        "last_quantity": new_qty,
                        "last_inventory_status": new_status,
                        "last_locations": location_quantities,
                        "sync_status": "success",
                        "last_sync_at": now_iso,
                        "consecutive_failures": 0,
                        "last_error": None,
                        "is_active": True,
                        "updated_at": now_iso,
                    }).eq("sku", sku).execute()

                    # Fix product table pricing
                    prod_rec = product_lookup.get(sku)
                    if prod_rec:
                        shopify_price = round(new_price * MARKUP_FACTOR, 2) if new_price else None
                        update_payload = {}
                        if shopify_price is not None:
                            update_payload["price"] = shopify_price
                            update_payload["cost_per_item"] = new_price
                        if new_qty is not None:
                            update_payload["inventory_quantity"] = new_qty
                        if update_payload:
                            db.table("product").update(update_payload).eq(
                                "sku", sku
                            ).eq("user_id", prod_rec.get("user_id", "system")).execute()

                # ── Fix Shopify locations & pricing ──────────────────
                base_sku = strip_variant_suffix(sku)
                shopify_id = await shopify.find_product_by_sku(base_sku)
                if not shopify_id:
                    failed.append({"sku": sku, "error": f"Not found in Shopify: {base_sku}"})
                    print(f"    {C_RED}NOT FOUND{C_RESET}  {sku} ({base_sku})")
                    continue

                inv_item_id = await shopify.get_inventory_item_id(shopify_id)
                if not inv_item_id:
                    failed.append({"sku": sku, "error": "No inventory_item_id"})
                    print(f"    {C_RED}NO INV ID{C_RESET}  {sku}")
                    continue

                # Update pricing
                if new_price:
                    shopify_price = round(new_price * MARKUP_FACTOR, 2)
                    metafields = None
                    if location_summary:
                        metafields = [{
                            "namespace": "boeing",
                            "key": "location_summary",
                            "value": location_summary,
                            "type": "single_line_text_field",
                        }]
                    await shopify.update_product_pricing(shopify_id, shopify_price, metafields)

                # Fix inventory locations
                if location_quantities:
                    matched, total = await shopify.fix_inventory(
                        inv_item_id, location_quantities, loc_map, shopify_locs
                    )
                    loc_tag = f"{matched} locations mapped"
                else:
                    # No locations — set qty on first location
                    for loc_id in shopify_locs.values():
                        try:
                            await shopify._call(
                                "POST", "/inventory_levels/set.json",
                                json_data={"location_id": loc_id, "inventory_item_id": inv_item_id, "available": 0},
                            )
                        except Exception:
                            pass
                    if shopify_locs:
                        first_id = next(iter(shopify_locs.values()))
                        await shopify._call(
                            "POST", "/inventory_levels/set.json",
                            json_data={"location_id": first_id, "inventory_item_id": inv_item_id, "available": new_qty},
                        )
                    loc_tag = f"qty={new_qty} on default"

                fixed_count += 1
                shopify_fixed.append(sku)
                print(f"    {C_GREEN}FIXED{C_RESET}    {sku}: {loc_tag} | {db_tag}")

                await asyncio.sleep(SHOPIFY_DELAY_SECONDS)

            except Exception as e:
                failed.append({"sku": sku, "error": str(e)})
                print(f"    {C_RED}ERROR{C_RESET}    {sku}: {e}")

        if batch_idx < len(batches) - 1:
            print(f"  {C_DIM}  Waiting {BOEING_DELAY_SECONDS}s...{C_RESET}")
            await asyncio.sleep(BOEING_DELAY_SECONDS)

    # ── Summary ──────────────────────────────────────────────────────────
    elapsed = time.time() - start_time
    mins, secs = int(elapsed // 60), int(elapsed % 60)

    print(f"\n{C_BOLD}{'=' * 60}{C_RESET}")
    print(f"{C_BOLD}  FIX & VERIFY COMPLETE — {mins}m {secs}s{C_RESET}")
    print(f"{C_BOLD}{'=' * 60}{C_RESET}")
    print(f"  Shopify fixed:     {C_GREEN}{fixed_count}{C_RESET}")
    print(f"  DB verified OK:    {C_GREEN}{verified_ok}{C_RESET}")
    print(f"  DB mismatches:     {C_YELLOW}{len(db_mismatches)}{C_RESET} (auto-corrected)")
    print(f"  Failed:            {C_RED}{len(failed)}{C_RESET}")

    if db_mismatches:
        print(f"\n  {C_YELLOW}DB corrections:{C_RESET}")
        for m in db_mismatches:
            print(f"    {m['sku']}: {m['detail']}")

    if failed:
        print(f"\n  {C_RED}Failures:{C_RESET}")
        for f in failed:
            print(f"    {f['sku']}: {f['error'][:80]}")

    # ── Save report ──────────────────────────────────────────────────────
    reports_dir = os.path.join(os.path.dirname(__file__), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    now_file = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    filepath = os.path.join(reports_dir, f"fix_verify_{now_file}.txt")

    lines = [
        "=" * 60,
        "  FIX & VERIFY REPORT",
        f"  {datetime.now(timezone.utc).strftime('%b %d, %Y — %H:%M UTC')}",
        "=" * 60, "",
        f"  Shopify fixed:     {fixed_count}",
        f"  DB verified OK:    {verified_ok}",
        f"  DB mismatches:     {len(db_mismatches)} (corrected)",
        f"  Failed:            {len(failed)}",
        f"  Duration:          {mins}m {secs}s", "",
    ]
    if db_mismatches:
        lines.append("DB CORRECTIONS")
        lines.append("-" * 60)
        for m in db_mismatches:
            lines.append(f"  {m['sku']:<25} {m['detail']}")
        lines.append("")
    if failed:
        lines.append("FAILURES")
        lines.append("-" * 60)
        for f in failed:
            lines.append(f"  {f['sku']:<25} {f['error'][:80]}")
        lines.append("")
    lines.append("=" * 60)

    with open(filepath, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    print(f"\n  Report saved: {filepath}")
    print()


def main() -> None:
    import io

    if os.name == "nt":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    print(f"\n{C_BOLD}  Boeing-Authoritative Fix & Verify{C_RESET}")
    print(f"  {'-' * 40}\n")
    print("  This script will:")
    print("  1. Fetch REAL Boeing data for all products (batches of 10)")
    print("  2. Fix Shopify locations + pricing using SHOPIFY_LOCATION_MAP")
    print("  3. Verify & correct any DB hash/price mismatches")
    print("  4. No emails sent\n")

    confirm = input("  Type 'FIX' to proceed: ").strip()
    if confirm != "FIX":
        print("  Aborted.")
        sys.exit(0)

    asyncio.run(run_fix())


if __name__ == "__main__":
    main()
