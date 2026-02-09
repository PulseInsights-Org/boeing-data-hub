#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║          SHOPIFY VENDOR → PART NAME METAFIELD MIGRATION SCRIPT             ║
║                                                                            ║
║  Purpose : Reads the current "Vendor" field from every product and writes  ║
║            that value into a new custom metafield  (custom.part_name).     ║
║                                                                            ║
║  Author  : Auto-generated for Skynet International Inc.                    ║
║  API     : Shopify GraphQL Admin API (2025-10)                             ║
║  Runtime : Python 3.10+                                                    ║
╚══════════════════════════════════════════════════════════════════════════════╝

PREREQUISITES
─────────────
1.  Create the metafield definition in Shopify Admin first:
      Settings → Custom data → Products → Add definition
        • Name          : Part Name
        • Namespace/Key : custom.part_name
        • Type          : Single line text
2.  Create a Custom App (or use an existing one) with the following
    Admin API scopes:
        • read_products
        • write_products
3.  Copy the Admin API access token into this script's configuration
    section below (or supply it via environment variables).

USAGE
─────
    # Option A – hardcode credentials below, then:
    python3 shopify_vendor_to_partname_migration.py

    # Option B – use environment variables:
    export SHOPIFY_STORE_NAME="your-store-name"
    export SHOPIFY_ACCESS_TOKEN="shpat_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    python3 shopify_vendor_to_partname_migration.py

    # Option C – Dry-run mode (no writes, just logs what WOULD happen):
    python3 shopify_vendor_to_partname_migration.py --dry-run

    # Option D – Resume from a saved cursor after interruption:
    python3 shopify_vendor_to_partname_migration.py --resume

FEATURES
────────
•  Paginated GraphQL fetching of ALL products (cursor-based).
•  Respects Shopify's rate-limit bucket (auto-throttle on low points).
•  Skips products whose Vendor field is empty/blank.
•  Skips products that already have the part_name metafield populated.
•  Writes a detailed CSV audit log for every processed product.
•  Saves cursor state to disk so interrupted runs can be resumed.
•  Beautiful, colour-coded terminal output with real-time progress.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import requests
except ImportError:
    print("ERROR: 'requests' library is required. Install with:  pip install requests")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION — edit these values, or supply via environment variables
# ═══════════════════════════════════════════════════════════════════════════════

SHOPIFY_STORE_NAME: str = os.getenv("SHOPIFY_STORE_DOMAIN")
SHOPIFY_ACCESS_TOKEN: str = os.getenv("SHOPIFY_ADMIN_API_TOKEN")
API_VERSION: str = "2025-10"

# Metafield target configuration
METAFIELD_NAMESPACE: str = "custom"
METAFIELD_KEY: str = "part_name"
METAFIELD_TYPE: str = "single_line_text_field"

# Performance tuning (based on Shopify GraphQL rate limits)
# Shopify bucket: 1000 points max, restore rate ~50 points/second
# Standard plan: 100 pts/sec, Plus: 1000 pts/sec
PAGE_SIZE: int = 50                   # Products per GraphQL page (max 250)
RATE_LIMIT_SAFETY_MARGIN: int = 300   # Pause when available points drop below this
RATE_LIMIT_PAUSE_SECONDS: float = 2.0 # Seconds to sleep when throttled
RETRY_MAX_ATTEMPTS: int = 5           # Retries per failed request (increased for reliability)
RETRY_BACKOFF_SECONDS: float = 5.0    # Initial backoff (doubles each retry)

# Sleep between API calls to respect rate limits
# With ~50 pts/sec restore rate, sleeping 0.1s between mutations is safe
SLEEP_BETWEEN_MUTATIONS: float = 0.15  # Sleep between each product update
SLEEP_BETWEEN_PAGES: float = 0.5       # Sleep between page fetches

# File paths (all outputs go to app_data directory)
CURSOR_STATE_FILE: str = "app_data/migration_cursor_state.json"
AUDIT_LOG_CSV: str = "app_data/migration_audit_log_prod.csv"
ERROR_LOG_FILE: str = "app_data/migration_errors_prod.log"
MIGRATION_REPORT_FILE: str = "app_data/migration_report_prod.txt"
ERROR_PRODUCTS_FILE: str = "app_data/failed_products_prod.csv"


# ═══════════════════════════════════════════════════════════════════════════════
#  ANSI COLOUR HELPERS (for rich terminal output)
# ═══════════════════════════════════════════════════════════════════════════════

class Clr:
    """ANSI escape codes for terminal colouring."""
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"
    WHITE   = "\033[97m"
    BG_GREEN  = "\033[42m"
    BG_RED    = "\033[41m"
    BG_YELLOW = "\033[43m"
    BG_BLUE   = "\033[44m"


def _ts() -> str:
    """Return a compact timestamp for log lines."""
    return datetime.now().strftime("%H:%M:%S")


# ═══════════════════════════════════════════════════════════════════════════════
#  DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ProductRecord:
    """Represents one product fetched from Shopify."""
    gid: str            # e.g. "gid://shopify/Product/123456789"
    title: str
    handle: str
    vendor: str
    status: str
    sku: str = ""
    existing_part_name: Optional[str] = None

    @property
    def numeric_id(self) -> str:
        return self.gid.split("/")[-1]


@dataclass
class MigrationStats:
    """Tracks running totals for the migration."""
    total_products_in_store: int = 0
    total_fetched: int = 0
    total_updated: int = 0
    total_skipped_empty_vendor: int = 0
    total_skipped_already_set: int = 0
    total_errors: int = 0
    total_pending: int = 0  # Products that need update but not yet processed
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None

    # Track failed products for detailed reporting
    failed_products: list = field(default_factory=list)

    @property
    def elapsed(self) -> str:
        end = self.end_time if self.end_time else time.time()
        secs = int(end - self.start_time)
        m, s = divmod(secs, 60)
        h, m = divmod(m, 60)
        return f"{h:02d}h {m:02d}m {s:02d}s"

    @property
    def products_remaining(self) -> int:
        return max(0, self.total_products_in_store - self.total_fetched)

    @property
    def products_yet_to_update(self) -> int:
        """Products that still need updating (remaining + errors)."""
        return self.products_remaining + self.total_errors

    @property
    def progress_pct(self) -> float:
        if self.total_products_in_store == 0:
            return 0.0
        return (self.total_fetched / self.total_products_in_store) * 100

    def add_failed_product(self, product: "ProductRecord", error_msg: str) -> None:
        """Track a failed product for reporting."""
        self.failed_products.append({
            "gid": product.gid,
            "numeric_id": product.numeric_id,
            "title": product.title,
            "sku": product.sku,
            "handle": product.handle,
            "vendor": product.vendor,
            "error": error_msg,
            "timestamp": datetime.now().isoformat()
        })


# ═══════════════════════════════════════════════════════════════════════════════
#  LOGGING SETUP
# ═══════════════════════════════════════════════════════════════════════════════

def setup_logging() -> logging.Logger:
    """Configure file + stream logging."""
    logger = logging.getLogger("shopify_migration")
    logger.setLevel(logging.DEBUG)

    # Ensure app_data directory exists
    Path(ERROR_LOG_FILE).parent.mkdir(parents=True, exist_ok=True)

    # File handler – captures everything (DEBUG+)
    fh = logging.FileHandler(ERROR_LOG_FILE, mode="a", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)

    return logger


logger = setup_logging()


# ═══════════════════════════════════════════════════════════════════════════════
#  GRAPHQL QUERIES & MUTATIONS
# ═══════════════════════════════════════════════════════════════════════════════

QUERY_PRODUCT_COUNT = """
query {
  productsCount {
    count
  }
}
"""

QUERY_PRODUCTS_PAGE = """
query FetchProducts($first: Int!, $after: String) {
  products(first: $first, after: $after) {
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node {
        id
        title
        handle
        vendor
        status
        variants(first: 1) {
          edges {
            node {
              sku
            }
          }
        }
        metafield(namespace: "custom", key: "part_name") {
          value
        }
      }
    }
  }
}
"""

MUTATION_SET_METAFIELD = """
mutation SetPartNameMetafield($metafields: [MetafieldsSetInput!]!) {
  metafieldsSet(metafields: $metafields) {
    metafields {
      id
      namespace
      key
      value
    }
    userErrors {
      field
      message
    }
  }
}
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  SHOPIFY API CLIENT
# ═══════════════════════════════════════════════════════════════════════════════

class ShopifyGraphQLClient:
    """Thin wrapper around Shopify's GraphQL Admin API with rate-limit handling."""

    def __init__(self, store_name: str, access_token: str, api_version: str):
        # Handle both full domain and just store name
        if store_name.endswith(".myshopify.com"):
            domain = store_name
        else:
            domain = f"{store_name}.myshopify.com"
        self.url = f"https://{domain}/admin/api/{api_version}/graphql.json"
        self.headers = {
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": access_token,
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def execute(self, query: str, variables: dict | None = None) -> dict[str, Any]:
        """
        Execute a GraphQL query/mutation with retry logic and rate-limit awareness.
        Returns the full JSON response body.
        """
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
            try:
                resp = self.session.post(self.url, json=payload, timeout=30)

                # Hard HTTP-level throttle
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", RATE_LIMIT_PAUSE_SECONDS))
                    print(f"  {Clr.YELLOW}⏳ HTTP 429 – throttled. Sleeping {retry_after:.1f}s …{Clr.RESET}")
                    logger.warning(f"HTTP 429 throttle. Sleeping {retry_after}s (attempt {attempt})")
                    time.sleep(retry_after)
                    continue

                resp.raise_for_status()
                data = resp.json()

                # Check for GraphQL-level errors
                if "errors" in data:
                    for err in data["errors"]:
                        if "Throttled" in str(err.get("message", "")):
                            print(f"  {Clr.YELLOW}⏳ GraphQL throttle. Sleeping {RATE_LIMIT_PAUSE_SECONDS}s …{Clr.RESET}")
                            logger.warning(f"GraphQL throttle: {err['message']}")
                            time.sleep(RATE_LIMIT_PAUSE_SECONDS)
                            continue
                    # Non-throttle errors
                    err_msgs = "; ".join(e.get("message", "") for e in data["errors"])
                    raise RuntimeError(f"GraphQL errors: {err_msgs}")

                # Inspect rate-limit budget from extensions
                extensions = data.get("extensions", {})
                throttle_status = extensions.get("cost", {}).get("throttleStatus", {})
                currently_available = throttle_status.get("currentlyAvailable", 1000)

                if currently_available < RATE_LIMIT_SAFETY_MARGIN:
                    sleep_time = RATE_LIMIT_PAUSE_SECONDS
                    print(f"  {Clr.DIM}💤 Rate-limit budget low ({currently_available} pts). "
                          f"Pausing {sleep_time:.1f}s …{Clr.RESET}")
                    logger.debug(f"Rate-limit budget low: {currently_available}. Sleeping {sleep_time}s.")
                    time.sleep(sleep_time)

                return data

            except requests.exceptions.ConnectionError as e:
                wait = RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
                print(f"  {Clr.RED}🔌 Connection error (attempt {attempt}/{RETRY_MAX_ATTEMPTS}). "
                      f"Retrying in {wait:.0f}s …{Clr.RESET}")
                logger.error(f"Connection error: {e}. Retrying in {wait}s.")
                time.sleep(wait)

            except requests.exceptions.Timeout:
                wait = RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
                print(f"  {Clr.RED}⏱️  Timeout (attempt {attempt}/{RETRY_MAX_ATTEMPTS}). "
                      f"Retrying in {wait:.0f}s …{Clr.RESET}")
                logger.error(f"Request timeout. Retrying in {wait}s.")
                time.sleep(wait)

        raise RuntimeError(f"Failed after {RETRY_MAX_ATTEMPTS} attempts.")


# ═══════════════════════════════════════════════════════════════════════════════
#  CURSOR STATE MANAGEMENT (for resume capability)
# ═══════════════════════════════════════════════════════════════════════════════

def save_cursor_state(cursor: Optional[str], stats: MigrationStats) -> None:
    """Persist the last successful cursor + stats to disk."""
    Path(CURSOR_STATE_FILE).parent.mkdir(parents=True, exist_ok=True)
    state = {
        "last_cursor": cursor,
        "total_products_in_store": stats.total_products_in_store,
        "total_fetched": stats.total_fetched,
        "total_updated": stats.total_updated,
        "total_skipped_empty_vendor": stats.total_skipped_empty_vendor,
        "total_skipped_already_set": stats.total_skipped_already_set,
        "total_errors": stats.total_errors,
        "products_yet_to_update": stats.products_yet_to_update,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(CURSOR_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    logger.debug(f"Cursor state saved. Fetched: {stats.total_fetched}, "
                f"Updated: {stats.total_updated}, Errors: {stats.total_errors}")


def load_cursor_state() -> tuple[Optional[str], MigrationStats]:
    """Load a previously saved cursor state (for --resume)."""
    if not Path(CURSOR_STATE_FILE).exists():
        return None, MigrationStats()
    with open(CURSOR_STATE_FILE) as f:
        state = json.load(f)
    stats = MigrationStats()
    stats.total_products_in_store = state.get("total_products_in_store", 0)
    stats.total_fetched = state.get("total_fetched", 0)
    stats.total_updated = state.get("total_updated", 0)
    stats.total_skipped_empty_vendor = state.get("total_skipped_empty_vendor", 0)
    stats.total_skipped_already_set = state.get("total_skipped_already_set", 0)
    stats.total_errors = state.get("total_errors", 0)
    logger.info(f"Loaded cursor state from {state.get('saved_at', 'unknown time')}")
    return state.get("last_cursor"), stats


# ═══════════════════════════════════════════════════════════════════════════════
#  AUDIT CSV LOGGER
# ═══════════════════════════════════════════════════════════════════════════════

class AuditLogger:
    """Writes a CSV log of every product processed."""

    HEADERS = [
        "timestamp", "product_gid", "product_numeric_id", "title", "sku",
        "handle", "status", "vendor_value", "action", "result", "error_detail"
    ]

    def __init__(self, filepath: str):
        self.filepath = filepath
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        file_exists = Path(filepath).exists()
        self.file = open(filepath, "a", newline="", encoding="utf-8")
        self.writer = csv.writer(self.file)
        if not file_exists:
            self.writer.writerow(self.HEADERS)
            self.file.flush()

    def log(self, product: ProductRecord, action: str, result: str,
            error_detail: str = "") -> None:
        self.writer.writerow([
            datetime.now().isoformat(),
            product.gid,
            product.numeric_id,
            product.title,
            product.sku,
            product.handle,
            product.status,
            product.vendor,
            action,
            result,
            error_detail,
        ])
        self.file.flush()

    def close(self) -> None:
        self.file.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  ERROR PRODUCTS LOGGER (tracks products that failed during update)
# ═══════════════════════════════════════════════════════════════════════════════

class ErrorProductsLogger:
    """Writes a CSV log of all products that failed during migration."""

    HEADERS = [
        "timestamp", "product_gid", "product_numeric_id", "title", "sku",
        "handle", "vendor_value", "error_message"
    ]

    def __init__(self, filepath: str):
        self.filepath = filepath
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        file_exists = Path(filepath).exists()
        self.file = open(filepath, "a", newline="", encoding="utf-8")
        self.writer = csv.writer(self.file)
        if not file_exists:
            self.writer.writerow(self.HEADERS)
            self.file.flush()

    def log(self, product: ProductRecord, error_msg: str) -> None:
        self.writer.writerow([
            datetime.now().isoformat(),
            product.gid,
            product.numeric_id,
            product.title,
            product.sku,
            product.handle,
            product.vendor,
            error_msg,
        ])
        self.file.flush()

    def close(self) -> None:
        self.file.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  DETAILED MIGRATION REPORT GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

def generate_migration_report(stats: MigrationStats, dry_run: bool) -> None:
    """Generate a detailed migration report file."""
    Path(MIGRATION_REPORT_FILE).parent.mkdir(parents=True, exist_ok=True)

    report_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode = "DRY-RUN" if dry_run else "LIVE"

    report_content = f"""
================================================================================
                    SHOPIFY MIGRATION DETAILED REPORT
                         Skynet International Inc.
================================================================================

Report Generated: {report_time}
Migration Mode:   {mode}
Store:            {SHOPIFY_STORE_NAME}
API Version:      {API_VERSION}

--------------------------------------------------------------------------------
                              SUMMARY STATISTICS
--------------------------------------------------------------------------------

Total Products in Store:          {stats.total_products_in_store:,}
Total Products Fetched:           {stats.total_fetched:,}
Total Products Updated:           {stats.total_updated:,}
Total Products Skipped:           {stats.total_skipped_empty_vendor + stats.total_skipped_already_set:,}
  - Skipped (Empty Vendor):       {stats.total_skipped_empty_vendor:,}
  - Skipped (Already Set):        {stats.total_skipped_already_set:,}
Total Products with Errors:       {stats.total_errors:,}
Products Yet to be Updated:       {stats.products_yet_to_update:,}

--------------------------------------------------------------------------------
                              TIMING INFORMATION
--------------------------------------------------------------------------------

Migration Start Time:   {datetime.fromtimestamp(stats.start_time).strftime("%Y-%m-%d %H:%M:%S")}
Migration End Time:     {datetime.fromtimestamp(stats.end_time).strftime("%Y-%m-%d %H:%M:%S") if stats.end_time else "N/A"}
Total Elapsed Time:     {stats.elapsed}

--------------------------------------------------------------------------------
                              SUCCESS RATE
--------------------------------------------------------------------------------

Overall Success Rate:   {((stats.total_updated) / max(1, stats.total_fetched - stats.total_skipped_empty_vendor - stats.total_skipped_already_set)) * 100:.2f}%
Error Rate:             {(stats.total_errors / max(1, stats.total_fetched)) * 100:.2f}%

--------------------------------------------------------------------------------
                         PRODUCTS WITH ERRORS
--------------------------------------------------------------------------------
"""

    if stats.failed_products:
        report_content += f"\nTotal Failed Products: {len(stats.failed_products)}\n\n"
        for i, fp in enumerate(stats.failed_products, 1):
            report_content += f"""
  [{i}] Product ID: {fp['numeric_id']}
      Title:      {fp['title'][:60]}
      SKU:        {fp['sku'] or '(no SKU)'}
      Handle:     {fp['handle']}
      Vendor:     {fp['vendor']}
      Error:      {fp['error']}
      Timestamp:  {fp['timestamp']}
"""
    else:
        report_content += "\n  No errors encountered during migration.\n"

    report_content += f"""
--------------------------------------------------------------------------------
                              OUTPUT FILES
--------------------------------------------------------------------------------

Audit Log (all products):     {AUDIT_LOG_CSV}
Error Log (detailed):         {ERROR_LOG_FILE}
Failed Products CSV:          {ERROR_PRODUCTS_FILE}
Cursor State (for resume):    {CURSOR_STATE_FILE}
This Report:                  {MIGRATION_REPORT_FILE}

--------------------------------------------------------------------------------
                              NOTES
--------------------------------------------------------------------------------

- Use --resume flag to continue from where migration stopped
- Check {ERROR_PRODUCTS_FILE} for list of products that need manual review
- Check {ERROR_LOG_FILE} for detailed error messages and stack traces

================================================================================
                           END OF REPORT
================================================================================
"""

    with open(MIGRATION_REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report_content)

    logger.info(f"Migration report saved to {MIGRATION_REPORT_FILE}")


# ═══════════════════════════════════════════════════════════════════════════════
#  TERMINAL DISPLAY HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def print_banner() -> None:
    print(f"""
{Clr.CYAN}{Clr.BOLD}╔══════════════════════════════════════════════════════════════════════════════╗
║           SHOPIFY VENDOR → PART NAME METAFIELD MIGRATION                   ║
║                      Skynet International Inc.                             ║
╚══════════════════════════════════════════════════════════════════════════════╝{Clr.RESET}
""")


def print_config_summary(store: str, dry_run: bool, resume: bool) -> None:
    mode = f"{Clr.YELLOW}DRY-RUN (no changes will be written){Clr.RESET}" if dry_run \
        else f"{Clr.GREEN}LIVE{Clr.RESET}"
    resume_str = f"{Clr.CYAN}YES (resuming from saved state){Clr.RESET}" if resume \
        else f"{Clr.DIM}NO (fresh start){Clr.RESET}"
    display_store = store if store.endswith(".myshopify.com") else f"{store}.myshopify.com"
    print(f"  {Clr.BOLD}Store              :{Clr.RESET}  {display_store}")
    print(f"  {Clr.BOLD}API Version        :{Clr.RESET}  {API_VERSION}")
    print(f"  {Clr.BOLD}Metafield          :{Clr.RESET}  {METAFIELD_NAMESPACE}.{METAFIELD_KEY} ({METAFIELD_TYPE})")
    print(f"  {Clr.BOLD}Mode               :{Clr.RESET}  {mode}")
    print(f"  {Clr.BOLD}Resume             :{Clr.RESET}  {resume_str}")
    print(f"  {Clr.BOLD}Page Size          :{Clr.RESET}  {PAGE_SIZE}")
    print()
    print(f"  {Clr.DIM}Rate Limiting:{Clr.RESET}")
    print(f"  {Clr.DIM}  • Sleep between mutations : {SLEEP_BETWEEN_MUTATIONS}s{Clr.RESET}")
    print(f"  {Clr.DIM}  • Sleep between pages     : {SLEEP_BETWEEN_PAGES}s{Clr.RESET}")
    print(f"  {Clr.DIM}  • Safety margin           : {RATE_LIMIT_SAFETY_MARGIN} points{Clr.RESET}")
    print(f"  {Clr.DIM}  • Max retries             : {RETRY_MAX_ATTEMPTS}{Clr.RESET}")
    print()
    print(f"  {Clr.DIM}Output Files:{Clr.RESET}")
    print(f"  {Clr.DIM}  • Audit Log      : {AUDIT_LOG_CSV}{Clr.RESET}")
    print(f"  {Clr.DIM}  • Error Log      : {ERROR_LOG_FILE}{Clr.RESET}")
    print(f"  {Clr.DIM}  • Failed Products: {ERROR_PRODUCTS_FILE}{Clr.RESET}")
    print(f"  {Clr.DIM}  • Report         : {MIGRATION_REPORT_FILE}{Clr.RESET}")
    print()


def print_progress_bar(stats: MigrationStats) -> None:
    """Print a compact progress summary line."""
    pct = stats.progress_pct
    bar_len = 30
    filled = int(bar_len * pct / 100)
    bar = "█" * filled + "░" * (bar_len - filled)

    print(
        f"\r  {Clr.BOLD}[{bar}] {pct:5.1f}%{Clr.RESET}"
        f"  │  Fetched: {Clr.CYAN}{stats.total_fetched}{Clr.RESET}"
        f"  │  Updated: {Clr.GREEN}{stats.total_updated}{Clr.RESET}"
        f"  │  Skipped: {Clr.YELLOW}"
        f"{stats.total_skipped_empty_vendor + stats.total_skipped_already_set}{Clr.RESET}"
        f"  │  Errors: {Clr.RED}{stats.total_errors}{Clr.RESET}"
        f"  │  Remaining: {stats.products_remaining}"
        f"  │  ⏱ {stats.elapsed}",
        end="", flush=True
    )


def print_product_update_line(idx: int, total: int, product: ProductRecord,
                              action: str, dry_run: bool) -> None:
    """Print a detailed line for each product being processed."""
    sku_display = product.sku if product.sku else "(no SKU)"
    vendor_display = product.vendor if product.vendor else "(empty)"

    if action == "UPDATE":
        icon = "🔄" if not dry_run else "👁️ "
        colour = Clr.GREEN
        detail = (f"Vendor → part_name : {Clr.YELLOW}\"{vendor_display}\"{Clr.RESET}")
    elif action == "SKIP_EMPTY":
        icon = "⏭️ "
        colour = Clr.DIM
        detail = "Vendor is empty – nothing to migrate"
    elif action == "SKIP_EXISTS":
        icon = "✅"
        colour = Clr.DIM
        detail = f"part_name already set: \"{product.existing_part_name}\""
    elif action == "ERROR":
        icon = "❌"
        colour = Clr.RED
        detail = "Failed – see error log"
    else:
        icon = "❓"
        colour = Clr.WHITE
        detail = action

    prefix = f"{Clr.DIM}[DRY-RUN]{Clr.RESET} " if dry_run and action == "UPDATE" else ""

    print(
        f"  {prefix}{icon} {colour}[{idx}/{total}]{Clr.RESET}"
        f"  {Clr.BOLD}ID:{Clr.RESET} {product.numeric_id}"
        f"  {Clr.BOLD}SKU:{Clr.RESET} {sku_display:<25}"
        f"  {Clr.BOLD}Title:{Clr.RESET} {product.title[:35]:<35}"
        f"  → {detail}"
    )


def print_final_summary(stats: MigrationStats, dry_run: bool) -> None:
    mode = "DRY-RUN" if dry_run else "LIVE"
    print(f"""
{Clr.CYAN}{Clr.BOLD}╔══════════════════════════════════════════════════════════════════════════════╗
║                         MIGRATION COMPLETE ({mode})                        ║
╚══════════════════════════════════════════════════════════════════════════════╝{Clr.RESET}

  {Clr.BOLD}Total products in store       :{Clr.RESET}  {stats.total_products_in_store:,}
  {Clr.BOLD}Total products fetched        :{Clr.RESET}  {stats.total_fetched:,}
  {Clr.GREEN}{Clr.BOLD}Successfully updated          :{Clr.RESET}  {Clr.GREEN}{stats.total_updated:,}{Clr.RESET}
  {Clr.YELLOW}{Clr.BOLD}Skipped (empty vendor)        :{Clr.RESET}  {Clr.YELLOW}{stats.total_skipped_empty_vendor:,}{Clr.RESET}
  {Clr.YELLOW}{Clr.BOLD}Skipped (already set)         :{Clr.RESET}  {Clr.YELLOW}{stats.total_skipped_already_set:,}{Clr.RESET}
  {Clr.RED}{Clr.BOLD}Errors                        :{Clr.RESET}  {Clr.RED}{stats.total_errors:,}{Clr.RESET}
  {Clr.MAGENTA}{Clr.BOLD}Products yet to be updated    :{Clr.RESET}  {Clr.MAGENTA}{stats.products_yet_to_update:,}{Clr.RESET}
  {Clr.BOLD}Elapsed time                  :{Clr.RESET}  {stats.elapsed}

  {Clr.DIM}─────────────────────────────────────────────────────────────────────{Clr.RESET}
  {Clr.BOLD}Output Files:{Clr.RESET}
  {Clr.DIM}  • Audit log (all products) : {AUDIT_LOG_CSV}{Clr.RESET}
  {Clr.DIM}  • Error log (detailed)     : {ERROR_LOG_FILE}{Clr.RESET}
  {Clr.DIM}  • Failed products CSV      : {ERROR_PRODUCTS_FILE}{Clr.RESET}
  {Clr.DIM}  • Migration report         : {MIGRATION_REPORT_FILE}{Clr.RESET}
""")


# ═══════════════════════════════════════════════════════════════════════════════
#  CORE MIGRATION LOGIC
# ═══════════════════════════════════════════════════════════════════════════════

def get_total_product_count(client: ShopifyGraphQLClient) -> int:
    """Fetch total number of products in the store."""
    resp = client.execute(QUERY_PRODUCT_COUNT)
    count = resp["data"]["productsCount"]["count"]
    return int(count)


def fetch_products_page(client: ShopifyGraphQLClient,
                        cursor: Optional[str] = None) -> tuple[list[ProductRecord], Optional[str], bool]:
    """
    Fetch one page of products.
    Returns: (products, next_cursor, has_next_page)
    """
    variables: dict[str, Any] = {"first": PAGE_SIZE}
    if cursor:
        variables["after"] = cursor

    resp = client.execute(QUERY_PRODUCTS_PAGE, variables)
    products_data = resp["data"]["products"]
    page_info = products_data["pageInfo"]

    products: list[ProductRecord] = []
    for edge in products_data["edges"]:
        node = edge["node"]
        sku = ""
        if node.get("variants", {}).get("edges"):
            sku = node["variants"]["edges"][0]["node"].get("sku", "")

        existing_part_name = None
        if node.get("metafield") and node["metafield"].get("value"):
            existing_part_name = node["metafield"]["value"]

        products.append(ProductRecord(
            gid=node["id"],
            title=node["title"],
            handle=node["handle"],
            vendor=node.get("vendor", ""),
            status=node.get("status", "UNKNOWN"),
            sku=sku,
            existing_part_name=existing_part_name,
        ))

    return products, page_info.get("endCursor"), page_info["hasNextPage"]


def update_product_metafield(client: ShopifyGraphQLClient,
                             product: ProductRecord) -> tuple[bool, str]:
    """
    Write the vendor value into custom.part_name metafield for one product.
    Returns (success, error_message).
    """
    variables = {
        "metafields": [
            {
                "ownerId": product.gid,
                "namespace": METAFIELD_NAMESPACE,
                "key": METAFIELD_KEY,
                "value": product.vendor,
                "type": METAFIELD_TYPE,
            }
        ]
    }

    resp = client.execute(MUTATION_SET_METAFIELD, variables)
    result = resp.get("data", {}).get("metafieldsSet", {})
    user_errors = result.get("userErrors", [])

    if user_errors:
        err_msg = "; ".join(e.get("message", "") for e in user_errors)
        return False, err_msg

    metafields = result.get("metafields", [])
    if metafields:
        return True, ""

    return False, "No metafield returned in response (unknown error)"


def run_migration(client: ShopifyGraphQLClient, stats: MigrationStats,
                  audit: AuditLogger, error_logger: ErrorProductsLogger,
                  start_cursor: Optional[str], dry_run: bool) -> None:
    """Main migration loop: paginate through all products and update metafields."""

    cursor = start_cursor
    has_next = True
    page_num = 0
    mutations_this_page = 0

    while has_next:
        page_num += 1
        mutations_this_page = 0
        print(f"\n  {Clr.BLUE}{'─' * 74}{Clr.RESET}")
        print(f"  {Clr.BLUE}📦 Fetching page {page_num} (cursor: "
              f"{cursor[:20] + '…' if cursor else 'START'}) …{Clr.RESET}")

        try:
            products, next_cursor, has_next = fetch_products_page(client, cursor)
        except Exception as e:
            print(f"  {Clr.RED}❌ FATAL: Failed to fetch products page: {e}{Clr.RESET}")
            logger.critical(f"Failed to fetch page at cursor={cursor}: {e}")
            save_cursor_state(cursor, stats)
            print(f"  {Clr.YELLOW}💾 Cursor state saved. Use --resume to continue later.{Clr.RESET}")
            raise

        stats.total_fetched += len(products)

        for i, product in enumerate(products, start=1):
            global_idx = stats.total_fetched - len(products) + i

            # ── CASE 1: Vendor field is empty ────────────────────────────
            if not product.vendor or product.vendor.strip() == "":
                stats.total_skipped_empty_vendor += 1
                print_product_update_line(global_idx, stats.total_products_in_store,
                                          product, "SKIP_EMPTY", dry_run)
                audit.log(product, "SKIP", "EMPTY_VENDOR")
                logger.debug(f"SKIP (empty vendor): {product.gid} | {product.title}")
                continue

            # ── CASE 2: Metafield already has a value ────────────────────
            if product.existing_part_name:
                stats.total_skipped_already_set += 1
                print_product_update_line(global_idx, stats.total_products_in_store,
                                          product, "SKIP_EXISTS", dry_run)
                audit.log(product, "SKIP", "ALREADY_SET",
                          f"existing={product.existing_part_name}")
                logger.debug(f"SKIP (already set): {product.gid} | {product.title} "
                             f"| existing={product.existing_part_name}")
                continue

            # ── CASE 3: Perform the migration ────────────────────────────
            if dry_run:
                stats.total_updated += 1
                print_product_update_line(global_idx, stats.total_products_in_store,
                                          product, "UPDATE", dry_run)
                audit.log(product, "DRY_RUN_UPDATE", "WOULD_UPDATE",
                          f"vendor_value={product.vendor}")
                logger.info(f"DRY-RUN UPDATE: {product.gid} | {product.title} "
                            f"| vendor=\"{product.vendor}\"")
            else:
                try:
                    success, err_msg = update_product_metafield(client, product)
                    mutations_this_page += 1

                    if success:
                        stats.total_updated += 1
                        print_product_update_line(global_idx, stats.total_products_in_store,
                                                  product, "UPDATE", dry_run)
                        audit.log(product, "UPDATE", "SUCCESS",
                                  f"vendor_value={product.vendor}")
                        logger.info(f"UPDATED: {product.gid} | {product.title} "
                                    f"| vendor=\"{product.vendor}\" → part_name")
                    else:
                        stats.total_errors += 1
                        stats.add_failed_product(product, err_msg)
                        error_logger.log(product, err_msg)
                        print_product_update_line(global_idx, stats.total_products_in_store,
                                                  product, "ERROR", dry_run)
                        print(f"          {Clr.RED}↳ Error: {err_msg}{Clr.RESET}")
                        audit.log(product, "UPDATE", "FAILED", err_msg)
                        logger.error(f"FAILED: {product.gid} | {product.title} | {err_msg}")

                    # ── Rate limiting: sleep between mutations ──────────────
                    # Shopify restores ~50 points/second, sleeping prevents throttling
                    time.sleep(SLEEP_BETWEEN_MUTATIONS)

                except Exception as e:
                    stats.total_errors += 1
                    stats.add_failed_product(product, str(e))
                    error_logger.log(product, str(e))
                    print_product_update_line(global_idx, stats.total_products_in_store,
                                              product, "ERROR", dry_run)
                    print(f"          {Clr.RED}↳ Exception: {e}{Clr.RESET}")
                    audit.log(product, "UPDATE", "EXCEPTION", str(e))
                    logger.exception(f"EXCEPTION updating {product.gid} | {product.title}")

                    # Sleep even on error to prevent rapid-fire failures
                    time.sleep(SLEEP_BETWEEN_MUTATIONS)

        # Save cursor after each page for resume capability
        cursor = next_cursor
        save_cursor_state(cursor, stats)

        # Print progress bar after each page
        print()
        print_progress_bar(stats)
        print()

        # ── Rate limiting: sleep between pages ──────────────────────────
        # Additional pause between pages to let the bucket refill
        if has_next:
            logger.debug(f"Page {page_num} complete. Mutations: {mutations_this_page}. "
                        f"Sleeping {SLEEP_BETWEEN_PAGES}s before next page.")
            time.sleep(SLEEP_BETWEEN_PAGES)


# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def validate_config() -> list[str]:
    """Return a list of configuration errors (empty = OK)."""
    errors = []
    if SHOPIFY_STORE_NAME in ("your-store-name", ""):
        errors.append("SHOPIFY_STORE_NAME is not set. Edit the script or set the environment variable.")
    if SHOPIFY_ACCESS_TOKEN in ("shpat_your_token_here", ""):
        errors.append("SHOPIFY_ACCESS_TOKEN is not set. Edit the script or set the environment variable.")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate Shopify product Vendor values to a custom Part Name metafield."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run without making any changes (preview mode)."
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from the last saved cursor state."
    )
    args = parser.parse_args()

    print_banner()

    # ── Validate configuration ───────────────────────────────────────────
    config_errors = validate_config()
    if config_errors:
        print(f"  {Clr.RED}{Clr.BOLD}⛔ CONFIGURATION ERRORS:{Clr.RESET}")
        for err in config_errors:
            print(f"    {Clr.RED}• {err}{Clr.RESET}")
        print(f"\n  {Clr.DIM}Edit the script's CONFIGURATION section or set environment variables.{Clr.RESET}\n")
        sys.exit(1)

    print_config_summary(SHOPIFY_STORE_NAME, args.dry_run, args.resume)

    # ── Initialize client and loggers ──────────────────────────────────
    client = ShopifyGraphQLClient(SHOPIFY_STORE_NAME, SHOPIFY_ACCESS_TOKEN, API_VERSION)
    audit = AuditLogger(AUDIT_LOG_CSV)
    error_logger = ErrorProductsLogger(ERROR_PRODUCTS_FILE)

    # ── Handle resume logic ──────────────────────────────────────────────
    start_cursor: Optional[str] = None
    if args.resume:
        start_cursor, stats = load_cursor_state()
        if start_cursor:
            print(f"  {Clr.CYAN}🔄 Resuming from saved state:{Clr.RESET}")
            print(f"     Previously fetched : {stats.total_fetched}")
            print(f"     Previously updated : {stats.total_updated}")
            print(f"     Cursor             : {start_cursor[:30]}…")
            print()
        else:
            print(f"  {Clr.YELLOW}⚠️  No saved cursor state found. Starting fresh.{Clr.RESET}\n")
            stats = MigrationStats()
    else:
        stats = MigrationStats()

    # ── Fetch total product count ────────────────────────────────────────
    print(f"  {Clr.BLUE}📊 Fetching total product count …{Clr.RESET}", end=" ", flush=True)
    try:
        total = get_total_product_count(client)
        stats.total_products_in_store = total
        print(f"{Clr.GREEN}{Clr.BOLD}{total:,} products{Clr.RESET}")
    except Exception as e:
        print(f"\n  {Clr.RED}❌ Failed to connect to Shopify API: {e}{Clr.RESET}")
        print(f"  {Clr.DIM}Check your store name and access token.{Clr.RESET}\n")
        logger.critical(f"Failed to get product count: {e}")
        sys.exit(1)

    # ── Confirm before live run ──────────────────────────────────────────
    if not args.dry_run:
        print(f"\n  {Clr.BG_YELLOW}{Clr.BOLD} ⚠️  WARNING {Clr.RESET}"
              f"  This will write metafield data to {Clr.BOLD}{total:,}{Clr.RESET} products.")
        print(f"  {Clr.DIM}Press Enter to continue, or Ctrl+C to abort …{Clr.RESET}", end=" ")
        try:
            input()
        except KeyboardInterrupt:
            print(f"\n\n  {Clr.YELLOW}Aborted by user.{Clr.RESET}\n")
            sys.exit(0)

    # ── Run the migration ────────────────────────────────────────────────
    print(f"\n  {Clr.GREEN}{Clr.BOLD}🚀 Starting migration …{Clr.RESET}")
    logger.info(f"Migration started. Store={SHOPIFY_STORE_NAME}, "
                f"dry_run={args.dry_run}, total_products={total}")

    try:
        run_migration(client, stats, audit, error_logger, start_cursor, args.dry_run)
    except KeyboardInterrupt:
        print(f"\n\n  {Clr.YELLOW}⚠️  Interrupted by user. Cursor state saved.{Clr.RESET}")
        save_cursor_state(None, stats)
        logger.warning("Migration interrupted by user (Ctrl+C).")
    except Exception as e:
        print(f"\n  {Clr.RED}❌ Migration halted due to error: {e}{Clr.RESET}")
        logger.critical(f"Migration halted: {e}")
    finally:
        # Record end time
        stats.end_time = time.time()

        # Close all file loggers
        audit.close()
        error_logger.close()

    # ── Cleanup cursor file on successful completion ─────────────────────
    if stats.products_remaining == 0 and Path(CURSOR_STATE_FILE).exists():
        os.remove(CURSOR_STATE_FILE)

    # ── Generate detailed migration report ───────────────────────────────
    generate_migration_report(stats, args.dry_run)
    print(f"\n  {Clr.CYAN}📄 Detailed report saved to: {MIGRATION_REPORT_FILE}{Clr.RESET}")

    # ── Print final summary ──────────────────────────────────────────────
    print_final_summary(stats, args.dry_run)
    logger.info(f"Migration complete. Updated={stats.total_updated}, "
                f"Skipped={stats.total_skipped_empty_vendor + stats.total_skipped_already_set}, "
                f"Errors={stats.total_errors}, "
                f"YetToUpdate={stats.products_yet_to_update}")


if __name__ == "__main__":
    main()