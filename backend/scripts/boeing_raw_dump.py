"""
Boeing API raw response dumper — no diffs, no DB writes, no Shopify writes.

Fetches all SKUs from product_sync_schedule (mirroring manual_sync.py's
fetch flow), calls Boeing PNA price API in batches of 10, and writes the
raw response fields for every SKU to a timestamped text file under
backend/scripts/reports/.

Auth credentials live in .env (BOEING_*); they are NEVER written to disk
or printed.

Usage:
  cd backend/
  python -m scripts.boeing_raw_dump                  # all sync_schedule SKUs
  python -m scripts.boeing_raw_dump 2D2019-5=E9      # one or more specific SKUs
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

from app.core.config import Settings  # noqa: E402
from scripts.manual_sync import (  # noqa: E402
    BATCH_SIZE,
    BOEING_DELAY_SECONDS,
    BoeingAPI,
    fetch_all_sync_products,
    get_supabase_client,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("boeing_raw_dump")

REPORTS_DIR = Path(__file__).resolve().parent / "reports"


def _find_item(payload: Dict[str, Any], sku: str) -> Optional[Dict[str, Any]]:
    for item in payload.get("lineItems") or []:
        if item.get("aviallPartNumber") == sku:
            return item
    return None


def _summarize(item: Dict[str, Any]) -> Dict[str, Any]:
    """Pick out only the fields we care about; keep raw_item too for completeness."""
    return {
        "aviallPartNumber": item.get("aviallPartNumber"),
        "listPrice": item.get("listPrice"),
        "netPrice": item.get("netPrice"),
        "quantity": item.get("quantity"),
        "inStock": item.get("inStock"),
        "currency": item.get("currency"),
        "locationAvailabilities": item.get("locationAvailabilities"),
        "raw_item": item,
    }


def _format_summary_block(sku: str, summary: Dict[str, Any]) -> str:
    locs = summary.get("locationAvailabilities") or []
    loc_lines = (
        "\n".join(
            f"      - {l.get('location')}: {l.get('availQuantity')}" for l in locs
        )
        if locs
        else "      (none)"
    )
    return (
        f"SKU: {sku}\n"
        f"  listPrice:    {summary.get('listPrice')}\n"
        f"  netPrice:     {summary.get('netPrice')}\n"
        f"  quantity:     {summary.get('quantity')}\n"
        f"  inStock:      {summary.get('inStock')}\n"
        f"  currency:     {summary.get('currency')}\n"
        f"  locations:\n{loc_lines}\n"
    )


async def dump_skus(skus: List[str], out_path: Path) -> Dict[str, Any]:
    settings = Settings()
    if not (settings.boeing_client_id and settings.boeing_client_secret):
        raise RuntimeError("BOEING_CLIENT_ID / BOEING_CLIENT_SECRET not set")

    boeing = BoeingAPI(settings)
    await boeing.authenticate()

    batches = [skus[i : i + BATCH_SIZE] for i in range(0, len(skus), BATCH_SIZE)]
    started = time.time()
    summaries: Dict[str, Dict[str, Any]] = {}
    missing: List[str] = []
    errors: List[Dict[str, str]] = []

    with out_path.open("w", encoding="utf-8") as fh:
        header = (
            "=" * 70
            + f"\n  BOEING RAW DUMP\n  {datetime.now(timezone.utc).strftime('%b %d, %Y — %H:%M UTC')}\n"
            f"  Total SKUs: {len(skus)}   Batches: {len(batches)}\n"
            + "=" * 70
            + "\n\n"
        )
        fh.write(header)
        print(header)

        for batch_idx, batch in enumerate(batches, start=1):
            logger.info(
                f"Batch {batch_idx}/{len(batches)} ({len(batch)} SKUs): {batch}"
            )
            try:
                payload = await boeing.fetch_batch(batch)
            except Exception as exc:
                err_msg = str(exc)[:300]
                logger.error(f"Batch {batch_idx} failed: {err_msg}")
                fh.write(f"[BATCH {batch_idx} FAILED] {err_msg}\n\n")
                for sku in batch:
                    errors.append({"sku": sku, "error": err_msg})
                if batch_idx < len(batches):
                    await asyncio.sleep(BOEING_DELAY_SECONDS)
                continue

            batch_block = (
                f"--- Batch {batch_idx}/{len(batches)} -----------------------------\n"
            )
            fh.write(batch_block)
            print(batch_block, end="")

            for sku in batch:
                item = _find_item(payload, sku)
                if not item:
                    missing.append(sku)
                    fh.write(f"SKU: {sku}\n  (NOT IN BOEING RESPONSE — out of stock?)\n\n")
                    continue
                summary = _summarize(item)
                summaries[sku] = summary
                block = _format_summary_block(sku, summary) + "\n"
                fh.write(block)
                print(block, end="")

            if batch_idx < len(batches):
                logger.info(f"Sleeping {BOEING_DELAY_SECONDS}s before next batch...")
                await asyncio.sleep(BOEING_DELAY_SECONDS)

        # Append raw JSON for full fidelity
        fh.write("\n" + "=" * 70 + "\n  RAW JSON DUMP (per SKU)\n" + "=" * 70 + "\n\n")
        fh.write(json.dumps(summaries, indent=2, default=str))
        fh.write("\n")

        if missing:
            fh.write("\n" + "=" * 70 + "\n  MISSING FROM BOEING RESPONSE\n" + "=" * 70 + "\n")
            for sku in missing:
                fh.write(f"  {sku}\n")

        if errors:
            fh.write("\n" + "=" * 70 + "\n  BATCH ERRORS\n" + "=" * 70 + "\n")
            for e in errors:
                fh.write(f"  {e['sku']}: {e['error']}\n")

        elapsed = int(time.time() - started)
        footer = (
            "\n"
            + "=" * 70
            + f"\n  Duration: {elapsed // 60}m {elapsed % 60}s\n"
            f"  Output:   {out_path}\n"
            + "=" * 70
            + "\n"
        )
        fh.write(footer)
        print(footer)

    return {
        "total": len(skus),
        "captured": len(summaries),
        "missing": len(missing),
        "errors": len(errors),
        "output_path": str(out_path),
    }


async def main() -> int:
    cli_skus = sys.argv[1:]

    if cli_skus:
        skus = cli_skus
        logger.info(f"Using {len(skus)} SKU(s) from CLI args")
    else:
        settings = Settings()
        supabase = get_supabase_client(settings)
        all_products, _ = fetch_all_sync_products(supabase)
        skus = [p["sku"] for p in all_products]
        logger.info(f"Loaded {len(skus)} SKUs from product_sync_schedule")

    if not skus:
        print("No SKUs to dump.", file=sys.stderr)
        return 1

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_path = REPORTS_DIR / f"boeing_raw_{ts}.txt"

    stats = await dump_skus(skus, out_path)
    logger.info(
        f"Done. captured={stats['captured']} missing={stats['missing']} "
        f"errors={stats['errors']} → {stats['output_path']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
