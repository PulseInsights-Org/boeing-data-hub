"""
backfill_shopify_cost.py — production-grade, one-time backfill of
Shopify's `inventory_item.cost` field for every Boeing-sourced PN.

WHY:
  Both sync paths (hourly Celery + manual_sync) only push variant.price
  to Shopify; they never update inventory_item.cost. The cost shown in
  Shopify Admin is therefore frozen at the value set during the original
  product seeding. This script aligns Shopify's cost with Boeing's
  current list_price across all 79 active PNs.

FLOW:
  Phase 1: load SKUs from product_sync_schedule
  Phase 2: send START email
  Phase 3: fetch Boeing in batches of 10 (30s delay) — mirrors manual_sync.py
           Raw response (listPrice, netPrice, qty, locations) written to
           scripts/reports/backfill_shopify_cost_<ts>.txt
  Phase 4: first-pass — push Boeing list_price -> Shopify inventory_item.cost
           + align DB (product_sync_schedule.last_price, product.cost_per_item)
  Phase 5: retry failed SKUs once after 60s cooldown
  Phase 6: persist final report file
  Phase 7: send COMPLETION email
  Failure: any uncaught exception sends a FAILURE email with traceback

OPTIONS:
  --dry-run         Snapshot Boeing + show plan; no Shopify/DB writes, no emails.
  --skus A B C ...  Backfill only this subset (default: all sync_schedule).
  --yes             Skip the typed CONFIRM prompt (for unattended runs).
  --stakeholders    Send completion email to STAKEHOLDER_RECIPIENTS too
                    (default: REPORT_RECIPIENTS only).

USAGE (from backend/):
  python -m scripts.backfill_shopify_cost --dry-run
  python -m scripts.backfill_shopify_cost --skus 2D2019-5=E9
  python -m scripts.backfill_shopify_cost
  python -m scripts.backfill_shopify_cost --yes --stakeholders

CREDENTIALS:
  Loaded via Settings from .env. Never printed, never written to disk.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

from app.core.config import Settings  # noqa: E402
from app.core.constants.pricing import MARKUP_FACTOR  # noqa: E402
from scripts.manual_sync import (  # noqa: E402
    BATCH_SIZE,
    BOEING_DELAY_SECONDS,
    MAX_RETRIES,
    RETRY_DELAY_SECONDS,
    SHOPIFY_DELAY_SECONDS,
    BoeingAPI,
    ShopifyAPI,
    fetch_all_sync_products,
    fetch_product_records,
    get_stakeholder_recipients,
    get_supabase_client,
    send_email,
    strip_variant_suffix,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_shopify_cost")

REPORTS_DIR = Path(__file__).resolve().parent / "reports"

C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_RED = "\033[91m"
C_CYAN = "\033[96m"


# ═════════════════════════════════════════════════════════════════════════════
#  EMAIL BUILDERS
# ═════════════════════════════════════════════════════════════════════════════


def _row(label: str, value: str) -> str:
    return (
        f'<tr><td style="font-size:14px;color:#888;padding:8px 12px;'
        f'border-bottom:1px solid #f0f0f0;width:220px;">{label}</td>'
        f'<td style="font-size:14px;color:#333;font-weight:600;padding:8px 12px;'
        f'border-bottom:1px solid #f0f0f0;">{value}</td></tr>'
    )


def _card(value: str, label: str, color: str) -> str:
    return (
        f'<td style="padding:12px 8px;text-align:center;width:25%;">'
        f'<div style="font-size:28px;font-weight:bold;color:{color};">{value}</div>'
        f'<div style="font-size:12px;color:#888;margin-top:4px;">{label}</div></td>'
    )


def build_backfill_start_email(
    sku_count: int,
    batch_count: int,
    eta_minutes: int,
    timestamp: str,
) -> str:
    hours, mins = divmod(eta_minutes, 60)
    eta_display = f"{hours}h {mins}m" if hours > 0 else f"{mins}m"

    return f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:650px;margin:0 auto;background:#ffffff;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#1a3a5c;border-radius:8px 8px 0 0;">
<tr><td style="padding:20px 24px;">
  <div style="color:#ffffff;font-size:20px;font-weight:bold;">
    Shopify Cost Backfill — Starting
    <span style="background:#27ae60;color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:bold;margin-left:8px;">BACKFILL</span>
  </div>
  <div style="color:#ffffffcc;font-size:13px;margin-top:4px;">{timestamp}</div>
</td></tr>
</table>
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f7ff;padding:16px 24px;">
<tr><td style="padding:12px 0;">
  <div style="font-size:15px;color:#1a3a5c;line-height:1.5;">
    A one-time backfill is running to align Shopify Admin's
    <strong>Cost per item</strong> field with Boeing's current
    <code>list_price</code>. The storefront selling price and customer-facing
    flow are not affected — only the internal admin cost label.
  </div>
</td></tr>
</table>
<table width="100%" cellpadding="0" cellspacing="0" style="padding:16px 24px;">
  {_row("SKUs to backfill", f"{sku_count:,}")}
  {_row("Boeing API batches", str(batch_count))}
  {_row("Batch delay", f"{BOEING_DELAY_SECONDS}s between batches")}
  {_row("Estimated duration", f"~{eta_display}")}
  {_row("Retry policy", f"{MAX_RETRIES} retry after {RETRY_DELAY_SECONDS}s cooldown")}
</table>
<table width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #e9ecef;">
<tr><td style="padding:14px 24px;text-align:center;font-size:11px;color:#999;">
  Triggered via scripts/backfill_shopify_cost.py
</td></tr>
</table>
</div>"""


def build_backfill_complete_email(
    total: int,
    updated: int,
    already_aligned: int,
    skipped: int,
    failed: int,
    duration_display: str,
    timestamp: str,
    report_filename: str,
    sample_updated: List[Dict[str, Any]],
    failures: List[Dict[str, Any]],
) -> str:
    sample_rows = "".join(
        f'<tr><td style="font-size:13px;color:#333;padding:6px 12px;border-bottom:1px solid #f0f0f0;">'
        f'{r["sku"]}</td>'
        f'<td style="font-size:13px;color:#888;padding:6px 12px;border-bottom:1px solid #f0f0f0;">'
        f'{r.get("shopify_cost_before", "—")} &rarr; {r["cost_pushed"]}</td></tr>'
        for r in sample_updated[:12]
    ) or '<tr><td colspan="2" style="font-size:13px;color:#888;padding:6px 12px;">(no updates)</td></tr>'

    fail_rows = "".join(
        f'<tr><td style="font-size:13px;color:#333;padding:6px 12px;border-bottom:1px solid #f0f0f0;">'
        f'{f["sku"]}</td>'
        f'<td style="font-size:13px;color:#c0392b;padding:6px 12px;border-bottom:1px solid #f0f0f0;">'
        f'{(f.get("error") or "")[:160]}</td></tr>'
        for f in failures[:20]
    )
    failures_section = (
        f"""<table width="100%" cellpadding="0" cellspacing="0" style="padding:0 24px 16px;">
<tr><td colspan="2" style="padding:0 0 8px;font-size:14px;font-weight:bold;color:#c0392b;">
  Failures ({len(failures)})</td></tr>
{fail_rows}
</table>"""
        if failures
        else ""
    )

    status_color = "#27ae60" if failed == 0 else "#e67e22"
    status_text = "All cost updates applied" if failed == 0 else f"{failed} failed after retry"

    return f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:650px;margin:0 auto;background:#ffffff;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#1a3a5c;border-radius:8px 8px 0 0;">
<tr><td style="padding:20px 24px;">
  <div style="color:#ffffff;font-size:20px;font-weight:bold;">Shopify Cost Backfill — Complete</div>
  <div style="color:#ffffffcc;font-size:13px;margin-top:4px;">{timestamp}</div>
</td></tr>
</table>
<table width="100%" cellpadding="0" cellspacing="0" style="background:{status_color};">
<tr><td style="padding:10px 24px;color:#fff;font-size:13px;font-weight:600;">{status_text}</td></tr>
</table>
<table width="100%" cellpadding="0" cellspacing="0" style="padding:16px 12px;">
<tr>
  {_card(str(updated), "Updated", "#27ae60")}
  {_card(str(already_aligned), "Already aligned", "#95a5a6")}
  {_card(str(skipped), "Skipped", "#f39c12")}
  {_card(str(failed), "Failed", "#c0392b")}
</tr>
</table>
<table width="100%" cellpadding="0" cellspacing="0" style="padding:0 24px;">
  {_row("Total SKUs processed", str(total))}
  {_row("Duration", duration_display)}
  {_row("Report file", report_filename)}
</table>
<table width="100%" cellpadding="0" cellspacing="0" style="padding:16px 24px;">
<tr><td colspan="2" style="padding:0 0 8px;font-size:14px;font-weight:bold;color:#1a3a5c;">
  Sample updates</td></tr>
{sample_rows}
</table>
{failures_section}
<table width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #e9ecef;">
<tr><td style="padding:14px 24px;text-align:center;font-size:11px;color:#999;">
  Generated by scripts/backfill_shopify_cost.py
</td></tr>
</table>
</div>"""


def build_backfill_failure_email(
    timestamp: str,
    phase: str,
    error_summary: str,
    traceback_excerpt: str,
    partial_stats: Optional[Dict[str, int]] = None,
) -> str:
    stats_html = ""
    if partial_stats:
        stats_html = (
            f"<table width='100%' cellpadding='0' cellspacing='0' style='padding:16px 12px;'>"
            f"<tr>"
            f"{_card(str(partial_stats.get('updated', 0)), 'Updated before failure', '#27ae60')}"
            f"{_card(str(partial_stats.get('failed', 0)), 'Failed', '#c0392b')}"
            f"{_card(str(partial_stats.get('remaining', 0)), 'Not attempted', '#95a5a6')}"
            f"</tr></table>"
        )

    return f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:650px;margin:0 auto;background:#ffffff;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#c0392b;border-radius:8px 8px 0 0;">
<tr><td style="padding:20px 24px;">
  <div style="color:#ffffff;font-size:20px;font-weight:bold;">Shopify Cost Backfill — FAILED</div>
  <div style="color:#ffffffcc;font-size:13px;margin-top:4px;">{timestamp}</div>
</td></tr>
</table>
<table width="100%" cellpadding="0" cellspacing="0" style="padding:16px 24px;">
  {_row("Failed phase", phase)}
  {_row("Error", error_summary[:300])}
</table>
{stats_html}
<table width="100%" cellpadding="0" cellspacing="0" style="padding:0 24px 16px;">
<tr><td style="padding:0 0 8px;font-size:14px;font-weight:bold;color:#c0392b;">Traceback</td></tr>
<tr><td style="background:#fdf2f0;padding:12px;font-family:monospace;font-size:12px;color:#444;white-space:pre-wrap;border-radius:4px;">
{traceback_excerpt[:3000]}
</td></tr>
</table>
<table width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #e9ecef;">
<tr><td style="padding:14px 24px;text-align:center;font-size:11px;color:#999;">
  scripts/backfill_shopify_cost.py
</td></tr>
</table>
</div>"""


# ═════════════════════════════════════════════════════════════════════════════
#  BOEING SNAPSHOT
# ═════════════════════════════════════════════════════════════════════════════


def _extract_boeing_item(payload: Dict[str, Any], sku: str) -> Optional[Dict[str, Any]]:
    for item in payload.get("lineItems") or []:
        if item.get("aviallPartNumber") == sku:
            return item
    return None


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
        return f if f != 0 else None
    except (TypeError, ValueError):
        return None


def _pick_boeing_price(item: Dict[str, Any]) -> Optional[float]:
    """Mirror manual_sync's `list_price or net_price` semantics."""
    list_price = _to_float(item.get("listPrice"))
    net_price = _to_float(item.get("netPrice"))
    return list_price if list_price is not None else net_price


async def snapshot_boeing(
    skus: List[str],
) -> Tuple[Dict[str, Dict[str, Any]], List[str], List[Dict[str, str]], str]:
    """Fetch every SKU from Boeing in batches.

    Returns (per_sku, missing, batch_errors, raw_block_text). The
    raw_block_text is the verbose per-SKU dump intended to be appended
    to the final report as an audit appendix.
    """
    settings = Settings()
    if not (settings.boeing_client_id and settings.boeing_client_secret):
        raise RuntimeError("BOEING_CLIENT_ID / BOEING_CLIENT_SECRET not set")

    boeing = BoeingAPI(settings)
    await boeing.authenticate()

    batches = [skus[i : i + BATCH_SIZE] for i in range(0, len(skus), BATCH_SIZE)]
    per_sku: Dict[str, Dict[str, Any]] = {}
    missing: List[str] = []
    errors: List[Dict[str, str]] = []
    raw_lines: List[str] = []

    header = (
        f"  BOEING RAW SNAPSHOT (audit appendix)\n"
        f"  {datetime.now(timezone.utc).strftime('%b %d, %Y — %H:%M UTC')}\n"
        f"  Total SKUs: {len(skus)}   Batches: {len(batches)}\n"
    )
    print("=" * 70)
    print(header)
    print("=" * 70)
    raw_lines.append(header)

    for batch_idx, batch in enumerate(batches, start=1):
        logger.info(f"Boeing batch {batch_idx}/{len(batches)} ({len(batch)} SKUs)")
        try:
            payload = await boeing.fetch_batch(batch)
        except Exception as exc:
            msg = str(exc)[:300]
            logger.error(f"Batch {batch_idx} failed: {msg}")
            raw_lines.append(f"[BATCH {batch_idx} FAILED] {msg}\n")
            for sku in batch:
                errors.append({"sku": sku, "error": msg})
            if batch_idx < len(batches):
                await asyncio.sleep(BOEING_DELAY_SECONDS)
            continue

        batch_header = f"--- Batch {batch_idx}/{len(batches)} -----------------------------"
        raw_lines.append(batch_header)
        print(batch_header)

        for sku in batch:
            item = _extract_boeing_item(payload, sku)
            if not item:
                missing.append(sku)
                raw_lines.append(f"SKU: {sku}\n  (NOT IN BOEING RESPONSE)\n")
                print(f"  {C_YELLOW}MISSING{C_RESET}  {sku}")
                continue

            chosen_price = _pick_boeing_price(item)
            per_sku[sku] = {
                "list_price": item.get("listPrice"),
                "net_price": item.get("netPrice"),
                "chosen_price_for_cost": chosen_price,
                "quantity": item.get("quantity"),
                "in_stock": item.get("inStock"),
                "locations": item.get("locationAvailabilities") or [],
                "raw_item": item,
            }

            loc_lines = "\n".join(
                f"      - {l.get('location')}: {l.get('availQuantity')}"
                for l in (item.get("locationAvailabilities") or [])
            ) or "      (none)"
            raw_lines.append(
                f"SKU: {sku}\n"
                f"  listPrice:           {item.get('listPrice')}\n"
                f"  netPrice:            {item.get('netPrice')}\n"
                f"  chosen for cost:     {chosen_price}\n"
                f"  quantity:            {item.get('quantity')}\n"
                f"  inStock:             {item.get('inStock')}\n"
                f"  locations:\n{loc_lines}\n"
            )
            print(
                f"  {C_GREEN}OK{C_RESET}       {sku}: "
                f"list={item.get('listPrice')} net={item.get('netPrice')} "
                f"→ cost={chosen_price}"
            )

        if batch_idx < len(batches):
            print(f"  {C_DIM}  sleeping {BOEING_DELAY_SECONDS}s...{C_RESET}")
            await asyncio.sleep(BOEING_DELAY_SECONDS)

    return per_sku, missing, errors, "\n".join(raw_lines)


# ═════════════════════════════════════════════════════════════════════════════
#  SHOPIFY HELPERS
# ═════════════════════════════════════════════════════════════════════════════


_TRANSIENT_HTTP_TOKENS = ("(500)", "(502)", "(503)", "(504)", "(408)", "(429)")


def _is_transient_shopify_error(exc: BaseException) -> bool:
    msg = str(exc)
    return any(tok in msg for tok in _TRANSIENT_HTTP_TOKENS)


async def resolve_inventory_item_id(
    shopify: ShopifyAPI,
    sku: str,
    cached_shopify_product_id: Optional[str],
) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """Resolve inventory_item_id for a SKU.

    Returns (id, product_id, err). On transient Shopify 5xx the call
    RAISES rather than returning an err string, so the outer apply_one_sku
    classifies it as 'failed' (retryable in Phase 5) instead of 'skipped'
    (permanent). Permanent 404 / no-variant / no-inventory-item keep the
    soft-skip behavior.
    """
    base_sku = strip_variant_suffix(sku)
    product_id = cached_shopify_product_id

    found = await shopify.find_product_by_sku(base_sku)
    if found:
        product_id = found
    elif not product_id:
        return None, None, f"No Shopify product found for SKU {base_sku}"

    try:
        data = await shopify._call("GET", f"/products/{product_id}.json")
    except Exception as exc:
        if _is_transient_shopify_error(exc):
            raise  # let Phase 5 retry catch this
        return None, str(product_id), f"GET product failed: {str(exc)[:200]}"

    variants = (data.get("product") or {}).get("variants") or []
    variant = next(
        (v for v in variants if v.get("sku") == base_sku),
        variants[0] if variants else None,
    )
    if not variant:
        return None, str(product_id), "Product has no variants"

    inventory_item_id = variant.get("inventory_item_id")
    if not inventory_item_id:
        return None, str(product_id), "Variant has no inventory_item_id"

    return int(inventory_item_id), str(product_id), None


async def get_current_shopify_cost(
    shopify: ShopifyAPI, inventory_item_id: int
) -> Optional[str]:
    try:
        data = await shopify._call("GET", f"/inventory_items/{inventory_item_id}.json")
        return (data.get("inventory_item") or {}).get("cost")
    except Exception as exc:
        logger.warning(f"GET inventory_item {inventory_item_id} failed: {exc}")
        return None


async def set_shopify_cost(
    shopify: ShopifyAPI, inventory_item_id: int, cost: float
) -> None:
    await shopify._call(
        "PUT",
        f"/inventory_items/{inventory_item_id}.json",
        json_data={"inventory_item": {"id": inventory_item_id, "cost": float(cost)}},
    )


def _build_location_summary(locations: List[Dict[str, Any]]) -> Optional[str]:
    """Match the format produced by manual_sync.extract_boeing_product_data."""
    if not locations:
        return None
    parts: List[str] = []
    for loc in locations:
        name = loc.get("location")
        qty = loc.get("availQuantity")
        if name is None:
            continue
        try:
            qty_int = int(qty) if qty is not None else 0
        except (TypeError, ValueError):
            qty_int = 0
        parts.append(f"{name}: {qty_int}")
    return "; ".join(parts) if parts else None


def update_all_three_tables(
    supabase,
    sku: str,
    user_id: Optional[str],
    boeing: Dict[str, Any],
) -> Dict[str, bool]:
    """Align cost/price/qty across product_sync_schedule, product, product_staging.

    All three writes are independent; one failure does not abort the others.
    Returns a per-table success flag.
    """
    results: Dict[str, bool] = {
        "sync_schedule": False,
        "product": False,
        "product_staging": False,
    }

    cost = float(boeing["chosen_price_for_cost"])
    list_price = boeing.get("list_price")
    net_price = boeing.get("net_price")
    quantity = boeing.get("quantity")
    in_stock = boeing.get("in_stock")
    locations = boeing.get("locations") or []
    location_summary = _build_location_summary(locations)
    inventory_status = (
        "in_stock"
        if (in_stock is True or (isinstance(quantity, (int, float)) and quantity > 0))
        else "out_of_stock"
    )
    shopify_price = round(cost * MARKUP_FACTOR, 2)
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. product_sync_schedule — canonical source of truth
    try:
        supabase.table("product_sync_schedule").update(
            {"last_price": cost, "last_sync_at": now_iso}
        ).eq("sku", sku).execute()
        results["sync_schedule"] = True
    except Exception as exc:
        logger.error(f"sync_schedule update failed for {sku}: {exc}")

    if not user_id:
        return results

    # 2. product — live mirror used by Shopify update pipeline
    try:
        supabase.table("product").update(
            {
                "cost_per_item": cost,
                "price": shopify_price,
                "inventory_quantity": quantity if quantity is not None else 0,
            }
        ).eq("sku", sku).eq("user_id", user_id).execute()
        results["product"] = True
    except Exception as exc:
        logger.error(f"product table update failed for {sku}: {exc}")

    # 3. product_staging — was frozen at initial extraction; align now
    staging_payload: Dict[str, Any] = {
        "cost_per_item": cost,
        "price": shopify_price,
        "inventory_quantity": quantity if quantity is not None else 0,
        "inventory_status": inventory_status,
        "updated_at": now_iso,
    }
    if list_price is not None:
        staging_payload["list_price"] = list_price
    if net_price is not None:
        staging_payload["net_price"] = net_price
    if location_summary is not None:
        staging_payload["location_summary"] = location_summary

    try:
        resp = (
            supabase.table("product_staging")
            .update(staging_payload)
            .eq("sku", sku)
            .eq("user_id", user_id)
            .execute()
        )
        # Some sync_schedule SKUs may not have a staging row (deletions,
        # legacy data). Don't treat zero-row update as a failure — flag it.
        if resp.data:
            results["product_staging"] = True
        else:
            logger.info(f"product_staging: no row for {sku} — skipped (not a failure)")
            results["product_staging"] = True  # nothing to align, consistent
    except Exception as exc:
        logger.error(f"product_staging update failed for {sku}: {exc}")
    return results


# ═════════════════════════════════════════════════════════════════════════════
#  PER-SKU APPLY (used by first pass AND retry pass)
# ═════════════════════════════════════════════════════════════════════════════


async def apply_one_sku(
    shopify: ShopifyAPI,
    supabase,
    sku: str,
    boeing: Dict[str, Any],
    product_rec: Dict[str, Any],
) -> Dict[str, Any]:
    cost = float(boeing["chosen_price_for_cost"])
    row: Dict[str, Any] = {
        "sku": sku,
        "boeing_list_price": boeing.get("list_price"),
        "boeing_net_price": boeing.get("net_price"),
        "boeing_quantity": boeing.get("quantity"),
        "cost_pushed": cost,
        "shopify_cost_before": None,
        "shopify_status": "pending",
        "shopify_product_id": None,
        "inventory_item_id": None,
        "db_sync_schedule": False,
        "db_product": False,
        "db_product_staging": False,
        "error": None,
        "attempt": 0,
    }

    cached_shopify_id = product_rec.get("shopify_product_id")
    user_id = product_rec.get("user_id")

    def _write_db() -> None:
        db = update_all_three_tables(supabase, sku, user_id, boeing)
        row["db_sync_schedule"] = db["sync_schedule"]
        row["db_product"] = db["product"]
        row["db_product_staging"] = db["product_staging"]

    try:
        inv_id, resolved_pid, err = await resolve_inventory_item_id(
            shopify, sku, cached_shopify_id
        )
        row["shopify_product_id"] = resolved_pid
        row["inventory_item_id"] = inv_id

        if err or inv_id is None:
            row["shopify_status"] = "skipped"
            row["error"] = err or "no inventory_item_id"
            return row

        before = await get_current_shopify_cost(shopify, inv_id)
        row["shopify_cost_before"] = before

        # Idempotency: if Shopify cost already equals Boeing cost, skip the PUT.
        try:
            if before is not None and abs(float(before) - cost) < 0.005:
                row["shopify_status"] = "already_aligned"
                _write_db()  # still ensure all three tables are aligned
                return row
        except (TypeError, ValueError):
            pass  # before unparseable — fall through and PUT anyway

        await set_shopify_cost(shopify, inv_id, cost)
        row["shopify_status"] = "ok"
        _write_db()
        return row

    except Exception as exc:
        row["shopify_status"] = "failed"
        row["error"] = str(exc)[:300]
        return row


# ═════════════════════════════════════════════════════════════════════════════
#  REPORT WRITERS — mirror manual_sync's save_report_file layout
# ═════════════════════════════════════════════════════════════════════════════


def _fmt_money(v: Any) -> str:
    if v is None:
        return "(none)"
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return str(v)


def save_final_report(
    path: Path,
    mode_tag: str,
    timestamp: str,
    total_skus: int,
    results: List[Dict[str, Any]],
    missing: List[str],
    batch_errors: List[Dict[str, str]],
    duration_display: str,
    raw_boeing_block: str,
) -> Dict[str, int]:
    """Manual_sync-style structured report for an actual (write) run."""
    ok = [r for r in results if r["shopify_status"] == "ok"]
    aligned = [r for r in results if r["shopify_status"] == "already_aligned"]
    skipped = [r for r in results if r["shopify_status"] == "skipped"]
    failed = [r for r in results if r["shopify_status"] == "failed"]

    lines: List[str] = []
    lines.append("=" * 70)
    lines.append(f"  SHOPIFY COST BACKFILL REPORT  --  {mode_tag}")
    lines.append(f"  {timestamp}")
    lines.append("=" * 70)
    lines.append("")

    # SUMMARY
    lines.append("SUMMARY")
    lines.append("-" * 40)
    lines.append(f"  Total SKUs:           {total_skus}")
    lines.append(f"  Updated:              {len(ok)}")
    lines.append(f"  Already Aligned:      {len(aligned)}")
    lines.append(f"  Skipped:              {len(skipped)}")
    lines.append(f"  Failed:               {len(failed)}")
    lines.append(f"  Missing from Boeing:  {len(missing)}")
    lines.append(f"  Boeing Batch Errors:  {len(batch_errors)}")
    lines.append(f"  Duration:             {duration_display}")
    lines.append(f"  Mode:                 {mode_tag}")
    lines.append("")

    # UPDATED PRODUCTS — the headline answer to "what cost was saved per SKU"
    if ok:
        lines.append(f"UPDATED PRODUCTS ({len(ok)})")
        lines.append("-" * 70)
        lines.append(f"  {'SKU':<30}  {'cost: before -> after':<40}")
        lines.append(f"  {'-' * 28}  {'-' * 38}")
        for r in sorted(ok, key=lambda x: x["sku"]):
            before = _fmt_money(r.get("shopify_cost_before"))
            after = _fmt_money(r.get("cost_pushed"))
            tables = (
                f"  [sync={'Y' if r['db_sync_schedule'] else 'N'}"
                f" prod={'Y' if r['db_product'] else 'N'}"
                f" stag={'Y' if r.get('db_product_staging') else 'N'}]"
            )
            lines.append(f"  {r['sku']:<30}  {before} -> {after}{tables}")
        lines.append("")

    # ALREADY ALIGNED (Shopify cost already matched)
    if aligned:
        lines.append(f"ALREADY ALIGNED ({len(aligned)})")
        lines.append("-" * 70)
        lines.append(f"  {'SKU':<30}  {'cost':<10}")
        lines.append(f"  {'-' * 28}  {'-' * 10}")
        for r in sorted(aligned, key=lambda x: x["sku"]):
            lines.append(f"  {r['sku']:<30}  {_fmt_money(r['cost_pushed'])}")
        lines.append("")

    # SKIPPED (no Shopify product, no inventory_item_id, etc.)
    if skipped:
        lines.append(f"SKIPPED ({len(skipped)})")
        lines.append("-" * 70)
        lines.append(f"  {'SKU':<30}  {'Reason':<40}")
        lines.append(f"  {'-' * 28}  {'-' * 38}")
        for r in sorted(skipped, key=lambda x: x["sku"]):
            lines.append(f"  {r['sku']:<30}  {(r.get('error') or '')[:120]}")
        lines.append("")

    # FAILURES (after retry pass)
    if failed:
        lines.append(f"FAILURES ({len(failed)})")
        lines.append("-" * 70)
        lines.append(f"  {'SKU':<30}  {'Error':<40}")
        lines.append(f"  {'-' * 28}  {'-' * 38}")
        for r in sorted(failed, key=lambda x: x["sku"]):
            lines.append(f"  {r['sku']:<30}  {(r.get('error') or '')[:120]}")
        lines.append("")

    # MISSING FROM BOEING
    if missing:
        lines.append(f"MISSING FROM BOEING ({len(missing)})")
        lines.append("-" * 40)
        for sku in sorted(missing):
            lines.append(f"  {sku}")
        lines.append("")

    if batch_errors:
        lines.append(f"BOEING BATCH ERRORS ({len(batch_errors)})")
        lines.append("-" * 70)
        for e in batch_errors:
            lines.append(f"  {e['sku']:<30}  {(e.get('error') or '')[:120]}")
        lines.append("")

    # Audit appendix — raw Boeing response per SKU
    lines.append("=" * 70)
    lines.append(raw_boeing_block)
    lines.append("=" * 70)
    lines.append("")

    # JSON appendix for downstream tooling
    lines.append("JSON DETAIL (per-SKU)")
    lines.append("-" * 70)
    lines.append(json.dumps(results, indent=2, default=str))
    lines.append("")

    lines.append("=" * 70)
    lines.append("  Report generated by scripts/backfill_shopify_cost.py")
    lines.append("=" * 70)
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")

    return {
        "updated": len(ok),
        "already_aligned": len(aligned),
        "skipped": len(skipped),
        "failed": len(failed),
    }


def save_dryrun_report(
    path: Path,
    mode_tag: str,
    timestamp: str,
    total_skus: int,
    per_sku: Dict[str, Dict[str, Any]],
    missing: List[str],
    batch_errors: List[Dict[str, str]],
    duration_display: str,
    raw_boeing_block: str,
) -> Dict[str, int]:
    """Dry-run version — no Shopify/DB writes, just the planned changes."""
    actionable = [
        (sku, data) for sku, data in per_sku.items()
        if data.get("chosen_price_for_cost") is not None
    ]
    no_price = [
        sku for sku, data in per_sku.items()
        if data.get("chosen_price_for_cost") is None
    ]

    lines: List[str] = []
    lines.append("=" * 70)
    lines.append(f"  SHOPIFY COST BACKFILL REPORT  --  {mode_tag}")
    lines.append(f"  {timestamp}")
    lines.append("=" * 70)
    lines.append("")

    lines.append("SUMMARY")
    lines.append("-" * 40)
    lines.append(f"  Total SKUs:           {total_skus}")
    lines.append(f"  Would Update:         {len(actionable)}")
    lines.append(f"  No Boeing Price:      {len(no_price)}")
    lines.append(f"  Missing from Boeing:  {len(missing)}")
    lines.append(f"  Boeing Batch Errors:  {len(batch_errors)}")
    lines.append(f"  Duration:             {duration_display}")
    lines.append(f"  Mode:                 {mode_tag}  (no Shopify or DB writes)")
    lines.append("")

    if actionable:
        lines.append(f"PLANNED UPDATES ({len(actionable)})")
        lines.append("-" * 70)
        lines.append(f"  {'SKU':<30}  {'planned cost':<14}")
        lines.append(f"  {'-' * 28}  {'-' * 12}")
        for sku, data in sorted(actionable, key=lambda x: x[0]):
            lines.append(
                f"  {sku:<30}  {_fmt_money(data.get('chosen_price_for_cost'))}"
            )
        lines.append("")

    if no_price:
        lines.append(f"NO BOEING PRICE ({len(no_price)})")
        lines.append("-" * 40)
        for sku in sorted(no_price):
            lines.append(f"  {sku}")
        lines.append("")

    if missing:
        lines.append(f"MISSING FROM BOEING ({len(missing)})")
        lines.append("-" * 40)
        for sku in sorted(missing):
            lines.append(f"  {sku}")
        lines.append("")

    if batch_errors:
        lines.append(f"BOEING BATCH ERRORS ({len(batch_errors)})")
        lines.append("-" * 70)
        for e in batch_errors:
            lines.append(f"  {e['sku']:<30}  {(e.get('error') or '')[:120]}")
        lines.append("")

    lines.append("=" * 70)
    lines.append(raw_boeing_block)
    lines.append("=" * 70)
    lines.append("")

    lines.append("=" * 70)
    lines.append("  Report generated by scripts/backfill_shopify_cost.py")
    lines.append("=" * 70)
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")

    return {
        "would_update": len(actionable),
        "no_price": len(no_price),
        "missing": len(missing),
    }


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═════════════════════════════════════════════════════════════════════════════


async def run_backfill(args: argparse.Namespace) -> int:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    mode_tag = "DRYRUN" if args.dry_run else "WRITE"
    report_path = REPORTS_DIR / f"backfill_shopify_cost_{mode_tag}_{ts}.txt"

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%b %d, %Y — %H:%M UTC")
    start_time = time.time()

    print(f"\n{C_BOLD}Shopify Cost Backfill — {mode_tag}{C_RESET}")
    print(f"{C_DIM}{'-' * 46}{C_RESET}\n")

    settings = Settings()
    supabase = get_supabase_client(settings)

    # Recipient strategy
    recipients_override: Optional[List[str]] = None
    if args.stakeholders:
        stake = get_stakeholder_recipients()
        recipients_override = sorted(set((settings.report_recipients or []) + stake))

    # Track state for failure email
    phase = "Phase 1: pre-flight"
    writes_started = False
    partial_results: List[Dict[str, Any]] = []
    actionable: List[str] = []

    def _send_failure_email(exc: BaseException) -> None:
        if args.dry_run:
            return
        partial_stats = None
        if writes_started:
            ok = sum(1 for r in partial_results if r["shopify_status"] in ("ok", "already_aligned"))
            failed = sum(1 for r in partial_results if r["shopify_status"] == "failed")
            partial_stats = {
                "updated": ok,
                "failed": failed,
                "remaining": max(0, len(actionable) - len(partial_results)),
            }
        try:
            html = build_backfill_failure_email(
                timestamp=datetime.now(timezone.utc).strftime("%b %d, %Y — %H:%M UTC"),
                phase=phase,
                error_summary=f"{type(exc).__name__}: {exc}",
                traceback_excerpt="".join(traceback.format_exception(exc)),
                partial_stats=partial_stats,
            )
            send_email(
                settings,
                f"Shopify Cost Backfill — FAILED ({phase})",
                html,
                recipients_override=recipients_override,
            )
        except Exception as email_exc:
            logger.error(f"Failed to send failure email: {email_exc}")

    try:
        # ── Phase 1: load SKUs ─────────────────────────────────────────────
        print(f"{C_CYAN}[Phase 1/7] Loading SKUs from product_sync_schedule...{C_RESET}")
        all_products, _ = fetch_all_sync_products(supabase)
        sync_lookup = {p["sku"]: p for p in all_products}

        if args.skus:
            skus = [s for s in args.skus if s in sync_lookup]
            unknown = sorted(set(args.skus) - set(sync_lookup))
            if unknown:
                print(f"  {C_YELLOW}Ignoring unknown SKUs:{C_RESET} {unknown}")
        else:
            skus = [p["sku"] for p in all_products]

        if not skus:
            print(f"{C_RED}No SKUs to backfill.{C_RESET}")
            return 1

        product_records = fetch_product_records(supabase, skus)
        batch_count = (len(skus) + BATCH_SIZE - 1) // BATCH_SIZE
        eta_minutes = (batch_count * BOEING_DELAY_SECONDS) // 60 + 3
        print(f"  Sync records:        {len(skus)}")
        print(f"  Product records:     {len(product_records)}")
        print(f"  Batches:             {batch_count}")
        print(f"  Estimated duration:  ~{eta_minutes}m")
        print(f"  Report file:         {report_path}\n")

        # ── Confirmation gate ──────────────────────────────────────────────
        if not args.dry_run and not args.yes:
            confirm = input(
                f"{C_RED}Type 'CONFIRM' to proceed with backfill "
                f"({len(skus)} SKUs, will send emails + mutate Shopify+DB): {C_RESET}"
            ).strip()
            if confirm != "CONFIRM":
                print(f"{C_YELLOW}Aborted. No emails, no writes.{C_RESET}\n")
                return 0
            print()

        # ── Phase 2: start email ───────────────────────────────────────────
        phase = "Phase 2: start email"
        if not args.dry_run:
            print(f"{C_CYAN}[Phase 2/7] Sending start email...{C_RESET}")
            start_html = build_backfill_start_email(
                len(skus), batch_count, eta_minutes, timestamp
            )
            date_str = now.strftime("%b %d, %Y")
            send_email(
                settings,
                f"Shopify Cost Backfill Starting — {date_str}",
                start_html,
                recipients_override=recipients_override,
            )
            print()

        # ── Phase 3: Boeing snapshot ───────────────────────────────────────
        phase = "Phase 3: Boeing snapshot"
        print(f"{C_CYAN}[Phase 3/7] Boeing API snapshot...{C_RESET}")
        per_sku, missing, batch_errors, raw_boeing_block = await snapshot_boeing(skus)
        print(
            f"\n  Captured: {len(per_sku)}   "
            f"Missing: {len(missing)}   Batch errors: {len(batch_errors)}\n"
        )

        actionable = [
            sku for sku, data in per_sku.items()
            if data.get("chosen_price_for_cost") is not None
        ]
        no_price = [
            sku for sku, data in per_sku.items()
            if data.get("chosen_price_for_cost") is None
        ]
        print(f"  Will update Shopify cost for {len(actionable)} SKUs")
        if missing or no_price or batch_errors:
            print(
                f"  {C_YELLOW}Skip:{C_RESET} {len(missing)} missing, "
                f"{len(no_price)} no-price, {len(batch_errors)} batch errors"
            )

        if args.dry_run:
            elapsed = int(time.time() - start_time)
            duration_display = f"{elapsed // 60}m {elapsed % 60}s"
            save_dryrun_report(
                report_path,
                mode_tag,
                timestamp,
                len(skus),
                per_sku,
                missing,
                batch_errors,
                duration_display,
                raw_boeing_block,
            )
            print(f"\n{C_YELLOW}--dry-run set — exiting without writes.{C_RESET}")
            print(f"Report: {report_path}\n")
            return 0

        if not actionable:
            elapsed = int(time.time() - start_time)
            duration_display = f"{elapsed // 60}m {elapsed % 60}s"
            save_final_report(
                report_path,
                mode_tag,
                timestamp,
                len(skus),
                partial_results,
                missing,
                batch_errors,
                duration_display,
                raw_boeing_block,
            )
            print(f"{C_RED}Nothing to do — no actionable SKUs.{C_RESET}\n")
            print(f"Report: {report_path}\n")
            return 1

        # ── Phase 4: first-pass writes ─────────────────────────────────────
        phase = "Phase 4: first-pass writes"
        writes_started = True
        shopify = ShopifyAPI(settings)

        print(f"\n{C_CYAN}[Phase 4/7] First pass — Shopify cost + DB align (3 tables)...{C_RESET}")
        for idx, sku in enumerate(actionable):
            data = per_sku[sku]
            row = await apply_one_sku(
                shopify,
                supabase,
                sku,
                data,
                product_records.get(sku, {}),
            )
            row["attempt"] = 1
            partial_results.append(row)

            status = row["shopify_status"]
            tag = {
                "ok": f"{C_GREEN}OK     {C_RESET}",
                "already_aligned": f"{C_DIM}ALIGNED{C_RESET}",
                "skipped": f"{C_YELLOW}SKIP   {C_RESET}",
                "failed": f"{C_RED}FAIL   {C_RESET}",
            }.get(status, status)
            print(
                f"  {tag} {sku}: {row['shopify_cost_before']} -> {row['cost_pushed']}"
                + (f"  ({row['error']})" if row["error"] else "")
            )

            if idx < len(actionable) - 1:
                await asyncio.sleep(SHOPIFY_DELAY_SECONDS)

        # ── Phase 5: retry pass ────────────────────────────────────────────
        phase = "Phase 5: retry pass"
        failed_skus = [r["sku"] for r in partial_results if r["shopify_status"] == "failed"]

        if failed_skus and MAX_RETRIES > 0:
            print(
                f"\n{C_CYAN}[Phase 5/7] Retrying {len(failed_skus)} failed SKUs "
                f"after {RETRY_DELAY_SECONDS}s cooldown...{C_RESET}"
            )
            await asyncio.sleep(RETRY_DELAY_SECONDS)

            retry_shopify = ShopifyAPI(settings)
            for idx, sku in enumerate(failed_skus):
                data = per_sku[sku]
                new_row = await apply_one_sku(
                    retry_shopify,
                    supabase,
                    sku,
                    data,
                    product_records.get(sku, {}),
                )
                new_row["attempt"] = 2

                for i, r in enumerate(partial_results):
                    if r["sku"] == sku and r["shopify_status"] == "failed":
                        partial_results[i] = new_row
                        break

                status = new_row["shopify_status"]
                tag = (
                    f"{C_GREEN}RETRY OK{C_RESET}" if status in ("ok", "already_aligned")
                    else f"{C_RED}RETRY FAIL{C_RESET}"
                )
                print(
                    f"  {tag}  {sku}"
                    + (f"  ({new_row['error']})" if new_row["error"] else "")
                )

                if idx < len(failed_skus) - 1:
                    await asyncio.sleep(SHOPIFY_DELAY_SECONDS)
        else:
            print(f"\n{C_GREEN}[Phase 5/7] No failures to retry{C_RESET}")

        # ── Phase 6: write structured report file ──────────────────────────
        phase = "Phase 6: report file"
        elapsed = int(time.time() - start_time)
        duration_display = f"{elapsed // 60}m {elapsed % 60}s"
        counts = save_final_report(
            report_path,
            mode_tag,
            timestamp,
            len(skus),
            partial_results,
            missing,
            batch_errors,
            duration_display,
            raw_boeing_block,
        )
        print()
        print("=" * 70)
        print(f"  Duration: {duration_display}")
        print(f"  Report:   {report_path}")
        print("=" * 70)

        # ── Phase 7: completion email ──────────────────────────────────────
        phase = "Phase 7: completion email"
        print(f"{C_CYAN}[Phase 7/7] Sending completion email...{C_RESET}")
        sample_updated = [
            r for r in partial_results if r["shopify_status"] == "ok"
        ]
        failures = [
            r for r in partial_results if r["shopify_status"] == "failed"
        ]
        complete_html = build_backfill_complete_email(
            total=len(skus),
            updated=counts["updated"],
            already_aligned=counts["already_aligned"],
            skipped=counts["skipped"] + len(missing) + len(no_price),
            failed=counts["failed"],
            duration_display=duration_display,
            timestamp=datetime.now(timezone.utc).strftime("%b %d, %Y — %H:%M UTC"),
            report_filename=report_path.name,
            sample_updated=sample_updated,
            failures=failures,
        )
        subject_suffix = "OK" if counts["failed"] == 0 else f"with {counts['failed']} failures"
        send_email(
            settings,
            f"Shopify Cost Backfill Complete — {subject_suffix}",
            complete_html,
            recipients_override=recipients_override,
        )
        print(f"  {C_GREEN}Sent completion email.{C_RESET}\n")

        print(f"  {C_GREEN}Updated: {counts['updated']}{C_RESET}")
        print(f"  {C_DIM}Already aligned: {counts['already_aligned']}{C_RESET}")
        print(f"  {C_YELLOW}Skipped: {counts['skipped']}{C_RESET}")
        print(f"  {C_RED}Failed:  {counts['failed']}{C_RESET}")
        print(f"\n  Report: {report_path}\n")

        return 2 if counts["failed"] > 0 else 0

    except KeyboardInterrupt:
        logger.warning("KeyboardInterrupt — sending abort email if writes had begun")
        _send_failure_email(KeyboardInterrupt("Operator aborted (Ctrl+C)"))
        raise

    except Exception as exc:
        logger.exception(f"Backfill failed in {phase}")
        _send_failure_email(exc)
        return 3


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill Shopify inventory_item.cost from Boeing list_price."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Snapshot Boeing + show plan; no Shopify/DB writes, no emails.",
    )
    parser.add_argument(
        "--skus",
        nargs="*",
        help="Optional subset of SKUs (default: all from product_sync_schedule).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the typed CONFIRM prompt.",
    )
    parser.add_argument(
        "--stakeholders",
        action="store_true",
        help="Send emails to STAKEHOLDER_RECIPIENTS too (default: REPORT_RECIPIENTS only).",
    )
    args = parser.parse_args()
    return asyncio.run(run_backfill(args))


if __name__ == "__main__":
    sys.exit(main())
