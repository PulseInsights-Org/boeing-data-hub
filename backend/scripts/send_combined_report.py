"""
send_combined_report.py — send a stakeholder email that looks
identical to the live auto-sync cycle email.

HTML is built by reusing the production builders from
app.services.report_service (same header text, 5 metric cards,
SVG donut, bucket distribution, changes/failures tables, footer).

Two modes:

  Single report (recommended for one manual_sync run):
    python -m scripts.send_combined_report \
        --report scripts/reports/manual_sync_PROD_<ts>.txt \
        [--dry-run]

  Combined / merged (collapse two runs into one email):
    python -m scripts.send_combined_report \
        --prior   scripts/reports/manual_sync_PROD_<earlier>.txt \
        --current scripts/reports/manual_sync_PROD_<later>.txt \
        [--dry-run]

Routing:
  --dry-run   Sends to REPORT_RECIPIENTS (dev) only, no CONFIRM prompt.
  default     Requires typed CONFIRM, then sends to STAKEHOLDER_RECIPIENTS only.

A preview HTML file is always saved to scripts/reports/combined_preview_*.html
before any email is sent.
"""

import argparse
import logging
import os
import re
import sys
import types
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

# ── Stub transitive deps so we can import ReportService without the full ────
# supabase/redis/resend SDK graph. The HTML _build_* methods we use are pure
# formatting and never touch these, so empty stubs are sufficient. Same
# philosophy as manual_sync.py's SupabaseDirect workaround.
for _mod in (
    "app.clients.resend_client",
    "app.clients.supabase_client",
    "app.db.report_store",
    "app.utils.cycle_tracker",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)
sys.modules["app.clients.resend_client"].ResendClient = type("ResendClient", (), {})
sys.modules["app.clients.supabase_client"].SupabaseClient = type("SupabaseClient", (), {})
sys.modules["app.db.report_store"].ReportStore = type("ReportStore", (), {})
sys.modules["app.utils.cycle_tracker"].get_cycle_progress = lambda *a, **kw: {}
sys.modules["app.utils.cycle_tracker"].get_cycle_changes = lambda *a, **kw: {}
sys.modules["app.utils.cycle_tracker"].get_cycle_start_time = lambda *a, **kw: None

from app.core.config import Settings  # noqa: E402
from app.services.report_service import ReportService  # noqa: E402

from scripts.manual_sync import (  # noqa: E402
    get_stakeholder_recipients,
    get_supabase_client,
    send_email,
)

logger = logging.getLogger("combined_report")

# Terminal colors
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_RED = "\033[91m"
C_CYAN = "\033[96m"
C_DIM = "\033[2m"


# ═════════════════════════════════════════════════════════════════════════════
#  PARSING — consumes the format produced by manual_sync.save_report_file
# ═════════════════════════════════════════════════════════════════════════════


def parse_report(path: str) -> Dict[str, Any]:
    """Parse a manual-sync .txt report into a structured dict."""
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().split("\n")

    return {
        "summary": _parse_summary(lines),
        "changed": _parse_table(lines, "CHANGED PRODUCTS", "Reason"),
        "unchanged": _parse_sku_list(lines, "UNCHANGED PRODUCTS"),
        "out_of_stock": _parse_sku_list(lines, "OUT OF STOCK"),
        "failed": _parse_table(lines, "FAILURES", "Error"),
    }


def _parse_summary(lines: List[str]) -> Dict[str, Any]:
    """Extract the SUMMARY block + the header timestamp."""
    out: Dict[str, Any] = {}
    int_keys = {
        "total_products", "changed", "unchanged",
        "out_of_stock", "failed", "retried_ok",
    }
    key_re = re.compile(
        r"^\s+(Total Products|Changed|Unchanged|Out of Stock|Failed|"
        r"Duration|Mode|Retried OK):\s+(.+)$"
    )
    for line in lines:
        m = key_re.match(line)
        if not m:
            continue
        key = m.group(1).lower().replace(" ", "_")
        val = m.group(2).strip()
        if key in int_keys:
            try:
                out[key] = int(val)
            except ValueError:
                out[key] = val
        else:
            out[key] = val

    # Header timestamp e.g. "  Apr 17, 2026 — 19:35 UTC"
    ts_re = re.compile(
        r"^\s+([A-Z][a-z]{2}\s+\d+,\s+\d{4}\s+.\s+\d{2}:\d{2}\s+UTC)\s*$"
    )
    for line in lines[:10]:
        m = ts_re.match(line)
        if m:
            out["timestamp"] = m.group(1)
            break
    return out


def _parse_table(
    lines: List[str], section_name: str, value_header: str
) -> Dict[str, str]:
    """Extract a 2-column table (SKU + value) from a named section."""
    rows: Dict[str, str] = {}
    in_section = False
    seen_separator = False
    section_header = re.compile(rf"^{re.escape(section_name)} \(\d+\)")
    next_section = re.compile(r"^[A-Z][A-Z ]+\s*\(\d+\)")
    data_re = re.compile(r"^\s{2,}(\S+)\s{2,}(.+?)\s*$")

    for line in lines:
        if section_header.match(line):
            in_section = True
            seen_separator = False
            continue
        if not in_section:
            continue
        if next_section.match(line) or re.match(r"^={3,}", line):
            break
        if line.strip().startswith("-" * 10):
            seen_separator = True
            continue
        if not seen_separator:
            continue
        # Skip the "SKU  <value_header>" column-header row
        stripped = line.strip()
        if stripped.startswith("SKU") and value_header in stripped:
            continue
        m = data_re.match(line)
        if m:
            rows[m.group(1)] = m.group(2)
    return rows


def _parse_sku_list(lines: List[str], section_name: str) -> List[str]:
    """Extract a simple SKU list from a named section."""
    skus: List[str] = []
    in_section = False
    section_header = re.compile(rf"^{re.escape(section_name)} \(\d+\)")
    next_section = re.compile(r"^[A-Z][A-Z ]+\s*\(\d+\)")
    sku_re = re.compile(r"^\s{2,}(\S+)\s*$")

    for line in lines:
        if section_header.match(line):
            in_section = True
            continue
        if not in_section:
            continue
        if next_section.match(line) or re.match(r"^={3,}", line):
            break
        if line.strip().startswith("-" * 10):
            continue
        m = sku_re.match(line)
        if m:
            skus.append(m.group(1))
    return skus


# ═════════════════════════════════════════════════════════════════════════════
#  MERGE — collapse pre-prior → post-current
# ═════════════════════════════════════════════════════════════════════════════


def parse_reason(reason: str) -> Dict[str, Optional[float]]:
    """Extract price/quantity from→to values from a reason string."""
    result: Dict[str, Optional[float]] = {
        "price_from": None, "price_to": None,
        "qty_from": None, "qty_to": None,
    }
    m = re.search(r"price:\s*([\d.]+)\s*->\s*([\d.]+)", reason)
    if m:
        result["price_from"] = float(m.group(1))
        result["price_to"] = float(m.group(2))
    m = re.search(r"quantity:\s*(\d+)\s*->\s*(\d+)", reason)
    if m:
        result["qty_from"] = int(m.group(1))
        result["qty_to"] = int(m.group(2))
    return result


def _pick(
    prior: Optional[Dict[str, Optional[float]]],
    current: Optional[Dict[str, Optional[float]]],
    from_key: str,
    to_key: str,
) -> Tuple[Optional[float], Optional[float]]:
    """Pick the right from/to values across two runs.

    from = prior.from if prior present, else current.from
    to   = current.to if current present, else prior.to
    """
    if prior and prior[from_key] is not None:
        fr = prior[from_key]
    elif current:
        fr = current[from_key]
    else:
        fr = None
    if current and current[to_key] is not None:
        to = current[to_key]
    elif prior:
        to = prior[to_key]
    else:
        to = None
    return fr, to


def _format_num(v: float) -> str:
    """Render 385.0 as '385' and 12.5 as '12.5' (keep real decimals)."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def single_report_to_merged(report: Dict[str, Any]) -> Dict[str, Any]:
    """Build a `merged`-shaped dict from one parsed report.

    Same shape as merge_reports() output so build_combined_html() can
    consume it unchanged. No collapsing — values pass through as-is.
    """
    all_skus = (
        set(report["changed"]) | set(report["unchanged"]) |
        set(report["failed"]) | set(report["out_of_stock"])
    )
    return {
        "total": len(all_skus),
        "changed": dict(report["changed"]),
        "unchanged": sorted(report["unchanged"]),
        "failed": dict(report["failed"]),
        "out_of_stock": sorted(report["out_of_stock"]),
    }


def merge_reports(
    prior: Dict[str, Any], current: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Rules:
      - Both runs changed the SKU  → collapse pre-prior.from → post-current.to
      - Net-zero after collapse    → drop (lands in unchanged)
      - Only prior changed         → use prior's change as-is
      - Only current changed       → use current's change as-is
      - Failures                   → current state only
      - OOS                        → current state only
    """
    merged_changes: Dict[str, str] = {}
    all_changed_skus = set(prior["changed"]) | set(current["changed"])

    for sku in all_changed_skus:
        p = parse_reason(prior["changed"][sku]) if sku in prior["changed"] else None
        c = parse_reason(current["changed"][sku]) if sku in current["changed"] else None

        pf, pt = _pick(p, c, "price_from", "price_to")
        qf, qt = _pick(p, c, "qty_from", "qty_to")

        parts: List[str] = []
        if pf is not None and pt is not None and pf != pt:
            parts.append(f"price: {_format_num(pf)} -> {_format_num(pt)}")
        if qf is not None and qt is not None and qf != qt:
            parts.append(f"quantity: {_format_num(qf)} -> {_format_num(qt)}")

        if not parts:
            # No parseable from/to (e.g. "first_sync_or_hash_mismatch") —
            # fall back to current run's raw reason if present, else prior's.
            raw = current["changed"].get(sku) or prior["changed"].get(sku, "")
            if raw and sku in current["changed"]:
                parts = [raw]

        if parts:
            merged_changes[sku] = "; ".join(parts)
        # else: collapsed to net-zero — silently drop, SKU lands in unchanged

    all_skus = (
        set(prior["changed"]) | set(prior["unchanged"]) |
        set(prior["failed"]) | set(prior["out_of_stock"]) |
        set(current["changed"]) | set(current["unchanged"]) |
        set(current["failed"]) | set(current["out_of_stock"])
    )

    failed = dict(current["failed"])
    unchanged = sorted(all_skus - set(merged_changes) - set(failed))
    oos = sorted(current["out_of_stock"])

    return {
        "total": len(all_skus),
        "changed": merged_changes,
        "unchanged": unchanged,
        "failed": failed,
        "out_of_stock": oos,
    }


# ═════════════════════════════════════════════════════════════════════════════
#  BUCKET DATA FROM SUPABASE
# ═════════════════════════════════════════════════════════════════════════════


def fetch_slot_counts(supabase, skus: List[str]) -> Dict[int, int]:
    """Count products per hour_bucket from product_sync_schedule."""
    if not skus:
        return {}
    slot_counts: Dict[int, int] = {}
    for i in range(0, len(skus), 100):
        chunk = skus[i:i + 100]
        result = (
            supabase.table("product_sync_schedule")
            .select("sku,hour_bucket")
            .in_("sku", chunk)
            .execute()
        )
        for row in result.data or []:
            bucket = row.get("hour_bucket")
            if bucket is not None:
                slot_counts[bucket] = slot_counts.get(bucket, 0) + 1
    return slot_counts


# ═════════════════════════════════════════════════════════════════════════════
#  HTML — reuse production ReportService builders verbatim
# ═════════════════════════════════════════════════════════════════════════════


def build_combined_html(
    merged: Dict[str, Any],
    slot_counts: Dict[int, int],
    timestamp: str,
) -> str:
    """
    Reuse ReportService's private HTML builders for byte-identical output
    with the live auto-sync email. We bypass __init__ (which requires
    injected clients) via __new__ — the _build_* methods are pure and
    do not touch self.
    """
    rs = ReportService.__new__(ReportService)

    summary = {
        "total_products": merged["total"],
        "success_count": merged["total"] - len(merged["failed"]),
        "failed_count": len(merged["failed"]),
        "unchanged_count": len(merged["unchanged"]),
        "out_of_stock_count": len(merged["out_of_stock"]),
    }

    failed_products = [
        {"sku": sku, "last_error": err, "consecutive_failures": 1}
        for sku, err in merged["failed"].items()
    ]
    oos_products = [{"sku": sku} for sku in merged["out_of_stock"]]

    header = rs._build_header(timestamp, None)
    warning = rs._build_incomplete_warning(0)
    metrics = rs._build_metric_cards(summary, len(merged["changed"]))
    donut = rs._build_status_donut_svg(
        summary["success_count"], summary["failed_count"]
    )
    buckets = rs._build_bucket_bars_html(slot_counts)
    changes_table = rs._build_changes_table_html(merged["changed"])
    failures_table = rs._build_failures_table_html(failed_products)
    oos_section = rs._build_out_of_stock_summary(oos_products)
    footer = rs._build_footer()

    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;'
        'max-width:650px;margin:0 auto;background:#ffffff;">\n'
        f"{header}\n{warning}\n{metrics}\n{donut}\n{buckets}\n"
        f"{changes_table}\n{failures_table}\n{oos_section}\n{footer}\n"
        "</div>"
    )


# ═════════════════════════════════════════════════════════════════════════════
#  PREVIEW
# ═════════════════════════════════════════════════════════════════════════════


def save_preview(html: str) -> str:
    """Save the rendered HTML to disk for browser inspection."""
    reports_dir = os.path.join(os.path.dirname(__file__), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    path = os.path.join(reports_dir, f"combined_preview_{ts}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═════════════════════════════════════════════════════════════════════════════


def main() -> None:
    import io
    if os.name == "nt":
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace"
        )
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace"
        )

    parser = argparse.ArgumentParser(
        description=(
            "Send the production-grade stakeholder email for a manual "
            "sync run (single report) or for two merged runs."
        )
    )
    parser.add_argument(
        "--report",
        help="Path to a single .txt report (use this for one manual_sync run).",
    )
    parser.add_argument(
        "--prior",
        help="Path to the earlier .txt report (combined mode).",
    )
    parser.add_argument(
        "--current",
        help="Path to the later .txt report (combined mode).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Send to REPORT_RECIPIENTS (dev) only. No CONFIRM prompt.",
    )
    args = parser.parse_args()

    single_mode = bool(args.report)
    combined_mode = bool(args.prior or args.current)
    if single_mode and combined_mode:
        print(
            f"{C_RED}Error: --report cannot be combined with "
            f"--prior/--current.{C_RESET}"
        )
        sys.exit(1)
    if not single_mode and not combined_mode:
        print(
            f"{C_RED}Error: pass --report <path> for a single run, "
            f"or --prior <path> --current <path> for combined.{C_RESET}"
        )
        sys.exit(1)
    if combined_mode and not (args.prior and args.current):
        print(
            f"{C_RED}Error: combined mode requires BOTH --prior and "
            f"--current.{C_RESET}"
        )
        sys.exit(1)

    files_to_check = (
        [("report", args.report)] if single_mode
        else [("prior", args.prior), ("current", args.current)]
    )
    for label, path in files_to_check:
        if not os.path.isfile(path):
            print(f"{C_RED}Error: --{label} file not found: {path}{C_RESET}")
            sys.exit(1)

    title = (
        "Single-Report Stakeholder Email" if single_mode
        else "Combined Stakeholder Report"
    )
    print(f"\n{C_BOLD}Boeing Data Hub — {title}{C_RESET}")
    print(f"{C_DIM}{'-' * 46}{C_RESET}\n")

    # ── 1/5 Parse ────────────────────────────────────────────────────────
    if single_mode:
        print(f"{C_CYAN}[1/5] Parsing report...{C_RESET}")
        report = parse_report(args.report)
        ts = report["summary"].get("timestamp", "(timestamp not found)")
        print(f"  Report : {args.report}")
        print(f"           {ts}")
        print(f"           Changed={len(report['changed'])}, "
              f"Unchanged={len(report['unchanged'])}, "
              f"OOS={len(report['out_of_stock'])}, "
              f"Failed={len(report['failed'])}")
        print()

        # ── 2/5 Build merged-shape dict (no merging — passthrough) ───────
        print(f"{C_CYAN}[2/5] Preparing report data...{C_RESET}")
        merged = single_report_to_merged(report)
        report_timestamp = report["summary"].get("timestamp")
    else:
        print(f"{C_CYAN}[1/5] Parsing reports...{C_RESET}")
        prior = parse_report(args.prior)
        current = parse_report(args.current)
        for label, rep, path in (("Prior  ", prior, args.prior),
                                  ("Current", current, args.current)):
            ts = rep["summary"].get("timestamp", "(timestamp not found)")
            print(f"  {label}: {path}")
            print(f"           {ts}")
            print(f"           Changed={len(rep['changed'])}, "
                  f"Unchanged={len(rep['unchanged'])}, "
                  f"OOS={len(rep['out_of_stock'])}, "
                  f"Failed={len(rep['failed'])}")
        print()

        # ── 2/5 Merge ────────────────────────────────────────────────────
        print(f"{C_CYAN}[2/5] Merging SKU changes...{C_RESET}")
        merged = merge_reports(prior, current)
        report_timestamp = current["summary"].get("timestamp")

    print(f"  Total SKUs:        {merged['total']}")
    print(f"  Changed:           {len(merged['changed'])}")
    print(f"  Unchanged:         {len(merged['unchanged'])}")
    print(f"  Out of Stock:      {len(merged['out_of_stock'])}")
    print(f"  Failed:            {len(merged['failed'])}")
    if merged["changed"]:
        print(f"\n  {C_DIM}Sample changes (first 5):{C_RESET}")
        for sku, reason in list(merged["changed"].items())[:5]:
            print(f"    {sku:<24} {reason}")
    print()

    # ── 3/5 Bucket data ──────────────────────────────────────────────────
    print(f"{C_CYAN}[3/5] Fetching bucket distribution from Supabase...{C_RESET}")
    settings = Settings()
    supabase = get_supabase_client(settings)
    all_skus = sorted(
        set(merged["changed"]) | set(merged["unchanged"]) |
        set(merged["failed"]) | set(merged["out_of_stock"])
    )
    slot_counts = fetch_slot_counts(supabase, all_skus)
    mapped = sum(slot_counts.values())
    print(f"  Products with hour_bucket: {mapped}/{len(all_skus)}")
    for b in sorted(slot_counts):
        print(f"    B{b}: {slot_counts[b]}")
    print()

    # ── 4/5 Build HTML ───────────────────────────────────────────────────
    print(f"{C_CYAN}[4/5] Building HTML...{C_RESET}")
    current_ts = report_timestamp or \
        datetime.now(timezone.utc).strftime("%b %d, %Y — %H:%M UTC")
    html_body = build_combined_html(merged, slot_counts, current_ts)
    preview_path = save_preview(html_body)
    preview_url = "file:///" + preview_path.replace(os.sep, "/")
    print(f"  Preview saved: {preview_path}")
    print(f"  Open in browser: {preview_url}")
    print()

    # Subject — exact match to report_service.py:228
    subject_date = datetime.now(timezone.utc).strftime("%b %d, %Y")
    subject = f"Sync Cycle Complete — {subject_date}"

    # ── 5/5 Send ─────────────────────────────────────────────────────────
    if args.dry_run:
        recipients = settings.report_recipients
        if not recipients:
            print(f"{C_RED}Error: REPORT_RECIPIENTS is empty in settings.{C_RESET}")
            sys.exit(1)
        print(f"{C_YELLOW}[5/5] DRY RUN — sending to REPORT_RECIPIENTS{C_RESET}")
        print(f"  Subject:    {subject}")
        print(f"  Recipients: {recipients}")
        print()
        ok = send_email(
            settings, subject, html_body, recipients_override=recipients
        )
        if not ok:
            print(f"{C_RED}Dry-run email failed — see logs above.{C_RESET}")
            sys.exit(1)
        print(f"{C_GREEN}Dry-run email sent.{C_RESET}")
    else:
        recipients = get_stakeholder_recipients()
        if not recipients:
            print(
                f"{C_RED}Error: STAKEHOLDER_RECIPIENTS is empty. "
                f"Restore it in .env before running without --dry-run.{C_RESET}"
            )
            sys.exit(1)
        print(
            f"{C_RED}{C_BOLD}[5/5] PRODUCTION — will send to "
            f"{len(recipients)} stakeholder(s){C_RESET}"
        )
        print(f"  Subject:    {subject}")
        print(f"  Recipients:")
        for r in recipients:
            print(f"    - {r}")
        print()
        confirm = input(
            f"{C_RED}Type 'CONFIRM' to send to stakeholders: {C_RESET}"
        ).strip()
        if confirm != "CONFIRM":
            print(f"{C_YELLOW}Aborted. No email sent.{C_RESET}")
            sys.exit(0)
        ok = send_email(
            settings, subject, html_body, recipients_override=recipients
        )
        if not ok:
            print(f"{C_RED}Stakeholder email failed — see logs above.{C_RESET}")
            sys.exit(1)
        print(f"{C_GREEN}Stakeholder email sent.{C_RESET}")

    print(f"\n{C_GREEN}Done.{C_RESET}\n")


if __name__ == "__main__":
    main()
