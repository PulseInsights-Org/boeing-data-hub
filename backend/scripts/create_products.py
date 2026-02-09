#!/usr/bin/env python3
"""
================================================================================
          SHOPIFY PRODUCT CREATION SCRIPT (FROM CSV)

  Purpose : Creates new products in Shopify from a CSV file with full
            metafield support, rate limiting, and resume capability.

  Author  : Auto-generated for Skynet International Inc.
  API     : Shopify GraphQL Admin API (2025-10)
  Runtime : Python 3.10+
================================================================================

USAGE
-----
    # Dry-run mode (no products created, just validation):
    python create_products.py --dry-run

    # Live run (creates products):
    python create_products.py

    # Resume from last saved position:
    python create_products.py --resume

    # Specify custom CSV file:
    python create_products.py --csv "path/to/products.csv"

FEATURES
--------
-  Reads products from Shopify-format CSV file
-  Creates products with variants, images, and metafields
-  Respects Shopify's rate-limit bucket (auto-throttle)
-  Skips products that already exist (by handle)
-  Writes detailed audit logs and error reports
-  Saves progress state for resume capability
-  Beautiful, colour-coded terminal output
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


# ===============================================================================
#  CONFIGURATION
# ===============================================================================

# Target store for product creation (your dev store)
SHOPIFY_STORE_NAME: str = os.getenv("SHOPIFY_STORE_DOMAIN")
SHOPIFY_ACCESS_TOKEN: str = os.getenv("SHOPIFY_ADMIN_API_TOKEN")

# You can also pass these via command line:
#   python create_products.py --store "store-name" --token "shpat_xxx"
API_VERSION: str = "2025-10"

# Default CSV file path
DEFAULT_CSV_FILE: str = r"C:\Users\91948\Desktop\SkynetParts - Projects\product-list.csv"

# Performance tuning (based on Shopify GraphQL rate limits)
# Shopify bucket: 1000 points max, restore rate ~50 points/second
RATE_LIMIT_SAFETY_MARGIN: int = 300
RATE_LIMIT_PAUSE_SECONDS: float = 2.0
RETRY_MAX_ATTEMPTS: int = 5
RETRY_BACKOFF_SECONDS: float = 5.0

# Sleep between API calls to respect rate limits
SLEEP_BETWEEN_CREATES: float = 0.25  # Sleep between each product creation
SLEEP_BETWEEN_BATCHES: float = 1.0   # Sleep every N products

# Batch size for progress saves
BATCH_SIZE: int = 50

# File paths (all outputs go to app_data directory)
PROGRESS_STATE_FILE: str = "app_data/create_products_state.json"
AUDIT_LOG_CSV: str = "app_data/create_products_audit.csv"
ERROR_LOG_FILE: str = "app_data/create_products_errors.log"
REPORT_FILE: str = "app_data/create_products_report.txt"
FAILED_PRODUCTS_FILE: str = "app_data/create_products_failed.csv"


# ===============================================================================
#  ANSI COLOUR HELPERS
# ===============================================================================

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


# ===============================================================================
#  DATA CLASSES
# ===============================================================================

@dataclass
class CSVProduct:
    """Represents a product row from the CSV file."""
    row_number: int
    handle: str
    title: str
    body_html: str
    vendor: str
    product_category: str
    product_type: str
    tags: str
    published: bool
    variant_sku: str
    variant_grams: float
    variant_price: str
    variant_compare_at_price: str
    variant_requires_shipping: bool
    variant_taxable: bool
    variant_weight_unit: str
    image_src: str
    status: str
    # Metafields
    alternate_part_number: str = ""
    condition: str = ""
    estimated_lead_time: str = ""
    expiration_date: str = ""
    inventory_location: str = ""
    manufacturer: str = ""
    notes_internal: str = ""
    part_number: str = ""
    pma: str = ""
    cert: str = ""
    trace: str = ""
    unit_of_measure: str = ""
    cost_per_item: str = ""


@dataclass
class CreationStats:
    """Tracks running totals for the creation process."""
    total_in_csv: int = 0
    total_processed: int = 0
    total_created: int = 0
    total_skipped_exists: int = 0
    total_skipped_invalid: int = 0
    total_errors: int = 0
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    last_processed_row: int = 0

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
        return max(0, self.total_in_csv - self.total_processed)

    @property
    def products_yet_to_create(self) -> int:
        return self.products_remaining + self.total_errors

    @property
    def progress_pct(self) -> float:
        if self.total_in_csv == 0:
            return 0.0
        return (self.total_processed / self.total_in_csv) * 100

    def add_failed_product(self, product: CSVProduct, error_msg: str) -> None:
        self.failed_products.append({
            "row_number": product.row_number,
            "handle": product.handle,
            "title": product.title,
            "sku": product.variant_sku,
            "error": error_msg,
            "timestamp": datetime.now().isoformat()
        })


# ===============================================================================
#  LOGGING SETUP
# ===============================================================================

def setup_logging() -> logging.Logger:
    """Configure file + stream logging."""
    logger = logging.getLogger("shopify_create_products")
    logger.setLevel(logging.DEBUG)

    Path(ERROR_LOG_FILE).parent.mkdir(parents=True, exist_ok=True)

    fh = logging.FileHandler(ERROR_LOG_FILE, mode="a", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)

    return logger


logger = setup_logging()


# ===============================================================================
#  GRAPHQL MUTATIONS
# ===============================================================================

# Using productSet mutation (recommended for API 2024-01+)
# This mutation handles both product and variant creation in one call
MUTATION_CREATE_PRODUCT = """
mutation ProductSet($input: ProductSetInput!, $synchronous: Boolean!) {
  productSet(input: $input, synchronous: $synchronous) {
    product {
      id
      handle
      title
      status
      variants(first: 1) {
        edges {
          node {
            id
            sku
          }
        }
      }
    }
    userErrors {
      field
      message
      code
    }
  }
}
"""

QUERY_PRODUCT_BY_HANDLE = """
query GetProductByHandle($handle: String!) {
  productByHandle(handle: $handle) {
    id
    handle
    title
  }
}
"""


# ===============================================================================
#  SHOPIFY API CLIENT
# ===============================================================================

class ShopifyGraphQLClient:
    """Thin wrapper around Shopify's GraphQL Admin API with rate-limit handling."""

    def __init__(self, store_name: str, access_token: str, api_version: str):
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
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
            try:
                resp = self.session.post(self.url, json=payload, timeout=60)

                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", RATE_LIMIT_PAUSE_SECONDS))
                    print(f"  {Clr.YELLOW}>>> HTTP 429 - throttled. Sleeping {retry_after:.1f}s ...{Clr.RESET}")
                    logger.warning(f"HTTP 429 throttle. Sleeping {retry_after}s (attempt {attempt})")
                    time.sleep(retry_after)
                    continue

                resp.raise_for_status()
                data = resp.json()

                if "errors" in data:
                    for err in data["errors"]:
                        if "Throttled" in str(err.get("message", "")):
                            print(f"  {Clr.YELLOW}>>> GraphQL throttle. Sleeping {RATE_LIMIT_PAUSE_SECONDS}s ...{Clr.RESET}")
                            logger.warning(f"GraphQL throttle: {err['message']}")
                            time.sleep(RATE_LIMIT_PAUSE_SECONDS)
                            continue
                    err_msgs = "; ".join(e.get("message", "") for e in data["errors"])
                    raise RuntimeError(f"GraphQL errors: {err_msgs}")

                extensions = data.get("extensions", {})
                throttle_status = extensions.get("cost", {}).get("throttleStatus", {})
                currently_available = throttle_status.get("currentlyAvailable", 1000)

                if currently_available < RATE_LIMIT_SAFETY_MARGIN:
                    sleep_time = RATE_LIMIT_PAUSE_SECONDS
                    print(f"  {Clr.DIM}>>> Rate-limit budget low ({currently_available} pts). "
                          f"Pausing {sleep_time:.1f}s ...{Clr.RESET}")
                    logger.debug(f"Rate-limit budget low: {currently_available}. Sleeping {sleep_time}s.")
                    time.sleep(sleep_time)

                return data

            except requests.exceptions.ConnectionError as e:
                wait = RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
                print(f"  {Clr.RED}>>> Connection error (attempt {attempt}/{RETRY_MAX_ATTEMPTS}). "
                      f"Retrying in {wait:.0f}s ...{Clr.RESET}")
                logger.error(f"Connection error: {e}. Retrying in {wait}s.")
                time.sleep(wait)

            except requests.exceptions.Timeout:
                wait = RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
                print(f"  {Clr.RED}>>> Timeout (attempt {attempt}/{RETRY_MAX_ATTEMPTS}). "
                      f"Retrying in {wait:.0f}s ...{Clr.RESET}")
                logger.error(f"Request timeout. Retrying in {wait}s.")
                time.sleep(wait)

        raise RuntimeError(f"Failed after {RETRY_MAX_ATTEMPTS} attempts.")


# ===============================================================================
#  CSV PARSING
# ===============================================================================

def parse_csv_file(csv_path: str, start_row: int = 0) -> list[CSVProduct]:
    """Parse the CSV file and return a list of CSVProduct objects."""
    products = []

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        for row_num, row in enumerate(reader, start=2):  # Start at 2 (1 for header, 1-indexed)
            if row_num <= start_row:
                continue

            # Skip rows without a handle (continuation rows for variants)
            handle = row.get("Handle", "").strip()
            if not handle:
                continue

            # Parse boolean fields
            published = row.get("Published", "").upper() == "TRUE"
            requires_shipping = row.get("Variant Requires Shipping", "").upper() == "TRUE"
            taxable = row.get("Variant Taxable", "").upper() == "TRUE"

            # Parse numeric fields
            try:
                grams = float(row.get("Variant Grams", "0") or "0")
            except ValueError:
                grams = 0.0

            product = CSVProduct(
                row_number=row_num,
                handle=handle,
                title=row.get("Title", "").strip(),
                body_html=row.get("Body (HTML)", "").strip(),
                vendor=row.get("Vendor", "").strip(),
                product_category=row.get("Product Category", "").strip(),
                product_type=row.get("Type", "").strip(),
                tags=row.get("Tags", "").strip(),
                published=published,
                variant_sku=row.get("Variant SKU", "").strip(),
                variant_grams=grams,
                variant_price=row.get("Variant Price", "").strip(),
                variant_compare_at_price=row.get("Variant Compare At Price", "").strip(),
                variant_requires_shipping=requires_shipping,
                variant_taxable=taxable,
                variant_weight_unit=row.get("Variant Weight Unit", "lb").strip() or "lb",
                image_src=row.get("Image Src", "").strip(),
                status=row.get("Status", "draft").strip().upper(),
                # Metafields
                alternate_part_number=row.get("Alternate part number (product.metafields.custom.alternate_part_number)", "").strip(),
                condition=row.get("Condition (product.metafields.custom.condition)", "").strip(),
                estimated_lead_time=row.get("Estimated lead time (in days) (product.metafields.custom.estimated_lead_time)", "").strip(),
                expiration_date=row.get("Expiration date (product.metafields.custom.expiration_date)", "").strip(),
                inventory_location=row.get("Inventory location (product.metafields.custom.inventory_location)", "").strip(),
                manufacturer=row.get("Manufacturer (product.metafields.custom.manufacturer)", "").strip(),
                notes_internal=row.get("Notes (Skynet internal) (product.metafields.custom.notes_skynet_internal_)", "").strip(),
                part_number=row.get("Part number (product.metafields.custom.part_number)", "").strip(),
                pma=row.get("PMA (product.metafields.custom.pma)", "").strip(),
                cert=row.get("Cert (product.metafields.custom.trace)", "").strip(),
                trace=row.get("Trace (product.metafields.custom.tracedoc)", "").strip(),
                unit_of_measure=row.get("Unit of measure (product.metafields.custom.unit_of_measure)", "").strip(),
                cost_per_item=row.get("Cost per item", "").strip(),
            )

            products.append(product)

    return products


def count_csv_products(csv_path: str) -> int:
    """Count total products in CSV (rows with handles)."""
    count = 0
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("Handle", "").strip():
                count += 1
    return count


# ===============================================================================
#  PRODUCT CREATION
# ===============================================================================

def check_product_exists(client: ShopifyGraphQLClient, handle: str) -> bool:
    """Check if a product with the given handle already exists."""
    try:
        resp = client.execute(QUERY_PRODUCT_BY_HANDLE, {"handle": handle})
        product = resp.get("data", {}).get("productByHandle")
        return product is not None
    except Exception as e:
        logger.warning(f"Error checking if product exists: {handle} - {e}")
        return False


def build_product_set_input(product: CSVProduct) -> dict[str, Any]:
    """Build the ProductSetInput object for the productSet mutation."""

    # Map status
    status = "ACTIVE" if product.status == "ACTIVE" else "DRAFT"

    # Build metafields list
    metafields = []

    if product.alternate_part_number:
        metafields.append({
            "namespace": "custom",
            "key": "alternate_part_number",
            "value": product.alternate_part_number,
            "type": "single_line_text_field"
        })

    if product.condition:
        metafields.append({
            "namespace": "custom",
            "key": "condition",
            "value": product.condition,
            "type": "single_line_text_field"
        })

    if product.estimated_lead_time:
        metafields.append({
            "namespace": "custom",
            "key": "estimated_lead_time",
            "value": product.estimated_lead_time,
            "type": "number_integer"
        })

    if product.inventory_location:
        metafields.append({
            "namespace": "custom",
            "key": "inventory_location",
            "value": product.inventory_location,
            "type": "single_line_text_field"
        })

    if product.manufacturer:
        metafields.append({
            "namespace": "custom",
            "key": "manufacturer",
            "value": product.manufacturer,
            "type": "single_line_text_field"
        })

    if product.notes_internal:
        metafields.append({
            "namespace": "custom",
            "key": "notes_skynet_internal_",
            "value": product.notes_internal,
            "type": "multi_line_text_field"
        })

    if product.part_number:
        metafields.append({
            "namespace": "custom",
            "key": "part_number",
            "value": product.part_number,
            "type": "single_line_text_field"
        })

    if product.pma:
        metafields.append({
            "namespace": "custom",
            "key": "pma",
            "value": product.pma,
            "type": "single_line_text_field"
        })

    if product.cert:
        metafields.append({
            "namespace": "custom",
            "key": "trace",
            "value": product.cert,
            "type": "single_line_text_field"
        })

    if product.unit_of_measure:
        metafields.append({
            "namespace": "custom",
            "key": "unit_of_measure",
            "value": product.unit_of_measure,
            "type": "single_line_text_field"
        })

    # Build variant for ProductSetInput format
    # ProductSetInput uses 'productVariants' not 'variants'
    variant = {
        "sku": product.variant_sku,
        "optionValues": [{"optionName": "Title", "name": "Default Title"}],
    }

    if product.variant_price:
        variant["price"] = product.variant_price

    if product.variant_compare_at_price:
        variant["compareAtPrice"] = product.variant_compare_at_price

    # Build product input for productSet
    product_input = {
        "handle": product.handle,
        "title": product.title,
        "descriptionHtml": product.body_html,
        "vendor": product.vendor,
        "productType": product.product_type,
        "status": status,
        "productOptions": [{"name": "Title", "values": [{"name": "Default Title"}]}],
        "variants": [variant],
    }

    if product.tags:
        product_input["tags"] = [t.strip() for t in product.tags.split(",") if t.strip()]

    if metafields:
        product_input["metafields"] = metafields

    # Add files/images if present
    if product.image_src:
        product_input["files"] = [{
            "originalSource": product.image_src,
            "contentType": "IMAGE"
        }]

    return product_input


def create_product(client: ShopifyGraphQLClient, product: CSVProduct) -> tuple[bool, str, str]:
    """
    Create a product in Shopify using productSet mutation.
    Returns: (success, product_id or "", error_message)
    """
    product_input = build_product_set_input(product)

    variables = {
        "input": product_input,
        "synchronous": True  # Wait for the operation to complete
    }

    resp = client.execute(MUTATION_CREATE_PRODUCT, variables)

    result = resp.get("data", {}).get("productSet", {})
    user_errors = result.get("userErrors", [])

    if user_errors:
        err_msg = "; ".join(f"{e.get('field', '')}: {e.get('message', '')}" for e in user_errors)
        return False, "", err_msg

    created_product = result.get("product")
    if created_product:
        return True, created_product.get("id", ""), ""

    return False, "", "No product returned in response"


# ===============================================================================
#  STATE MANAGEMENT
# ===============================================================================

def save_progress_state(stats: CreationStats, last_row: int) -> None:
    """Save progress state for resume capability."""
    Path(PROGRESS_STATE_FILE).parent.mkdir(parents=True, exist_ok=True)

    state = {
        "last_processed_row": last_row,
        "total_in_csv": stats.total_in_csv,
        "total_processed": stats.total_processed,
        "total_created": stats.total_created,
        "total_skipped_exists": stats.total_skipped_exists,
        "total_skipped_invalid": stats.total_skipped_invalid,
        "total_errors": stats.total_errors,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }

    with open(PROGRESS_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

    logger.debug(f"Progress saved. Row: {last_row}, Created: {stats.total_created}")


def load_progress_state() -> tuple[int, CreationStats]:
    """Load previously saved progress state."""
    if not Path(PROGRESS_STATE_FILE).exists():
        return 0, CreationStats()

    with open(PROGRESS_STATE_FILE) as f:
        state = json.load(f)

    stats = CreationStats()
    stats.total_in_csv = state.get("total_in_csv", 0)
    stats.total_processed = state.get("total_processed", 0)
    stats.total_created = state.get("total_created", 0)
    stats.total_skipped_exists = state.get("total_skipped_exists", 0)
    stats.total_skipped_invalid = state.get("total_skipped_invalid", 0)
    stats.total_errors = state.get("total_errors", 0)
    stats.last_processed_row = state.get("last_processed_row", 0)

    logger.info(f"Loaded progress from {state.get('saved_at', 'unknown time')}")
    return state.get("last_processed_row", 0), stats


# ===============================================================================
#  AUDIT LOGGER
# ===============================================================================

class AuditLogger:
    """Writes a CSV log of every product processed."""

    HEADERS = [
        "timestamp", "row_number", "handle", "title", "sku",
        "action", "result", "product_id", "error_detail"
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

    def log(self, product: CSVProduct, action: str, result: str,
            product_id: str = "", error_detail: str = "") -> None:
        self.writer.writerow([
            datetime.now().isoformat(),
            product.row_number,
            product.handle,
            product.title,
            product.variant_sku,
            action,
            result,
            product_id,
            error_detail,
        ])
        self.file.flush()

    def close(self) -> None:
        self.file.close()


class FailedProductsLogger:
    """Logs failed products to CSV for manual review."""

    HEADERS = ["timestamp", "row_number", "handle", "title", "sku", "error_message"]

    def __init__(self, filepath: str):
        self.filepath = filepath
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        file_exists = Path(filepath).exists()
        self.file = open(filepath, "a", newline="", encoding="utf-8")
        self.writer = csv.writer(self.file)
        if not file_exists:
            self.writer.writerow(self.HEADERS)
            self.file.flush()

    def log(self, product: CSVProduct, error_msg: str) -> None:
        self.writer.writerow([
            datetime.now().isoformat(),
            product.row_number,
            product.handle,
            product.title,
            product.variant_sku,
            error_msg,
        ])
        self.file.flush()

    def close(self) -> None:
        self.file.close()


# ===============================================================================
#  REPORT GENERATOR
# ===============================================================================

def generate_report(stats: CreationStats, csv_file: str, dry_run: bool) -> None:
    """Generate a detailed report file."""
    Path(REPORT_FILE).parent.mkdir(parents=True, exist_ok=True)

    report_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode = "DRY-RUN" if dry_run else "LIVE"

    report = f"""
================================================================================
                 SHOPIFY PRODUCT CREATION REPORT
                    Skynet International Inc.
================================================================================

Report Generated: {report_time}
Mode:             {mode}
Store:            {SHOPIFY_STORE_NAME}
CSV File:         {csv_file}

--------------------------------------------------------------------------------
                           SUMMARY STATISTICS
--------------------------------------------------------------------------------

Total Products in CSV:            {stats.total_in_csv:,}
Total Products Processed:         {stats.total_processed:,}
Successfully Created:             {stats.total_created:,}
Skipped (Already Exists):         {stats.total_skipped_exists:,}
Skipped (Invalid Data):           {stats.total_skipped_invalid:,}
Failed with Errors:               {stats.total_errors:,}
Products Yet to Process:          {stats.products_yet_to_create:,}

--------------------------------------------------------------------------------
                           TIMING INFORMATION
--------------------------------------------------------------------------------

Start Time:       {datetime.fromtimestamp(stats.start_time).strftime("%Y-%m-%d %H:%M:%S")}
End Time:         {datetime.fromtimestamp(stats.end_time).strftime("%Y-%m-%d %H:%M:%S") if stats.end_time else "N/A"}
Elapsed Time:     {stats.elapsed}

--------------------------------------------------------------------------------
                           SUCCESS RATE
--------------------------------------------------------------------------------

Success Rate:     {(stats.total_created / max(1, stats.total_processed - stats.total_skipped_exists - stats.total_skipped_invalid)) * 100:.2f}%
Error Rate:       {(stats.total_errors / max(1, stats.total_processed)) * 100:.2f}%

--------------------------------------------------------------------------------
                        PRODUCTS WITH ERRORS
--------------------------------------------------------------------------------
"""

    if stats.failed_products:
        report += f"\nTotal Failed: {len(stats.failed_products)}\n\n"
        for i, fp in enumerate(stats.failed_products[:50], 1):  # Show first 50
            report += f"""
  [{i}] Row: {fp['row_number']} | Handle: {fp['handle']}
      Title: {fp['title'][:50]}
      SKU:   {fp['sku']}
      Error: {fp['error']}
"""
        if len(stats.failed_products) > 50:
            report += f"\n  ... and {len(stats.failed_products) - 50} more (see {FAILED_PRODUCTS_FILE})\n"
    else:
        report += "\n  No errors encountered.\n"

    report += f"""
--------------------------------------------------------------------------------
                           OUTPUT FILES
--------------------------------------------------------------------------------

Audit Log:          {AUDIT_LOG_CSV}
Error Log:          {ERROR_LOG_FILE}
Failed Products:    {FAILED_PRODUCTS_FILE}
Progress State:     {PROGRESS_STATE_FILE}
This Report:        {REPORT_FILE}

================================================================================
                          END OF REPORT
================================================================================
"""

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report)

    logger.info(f"Report saved to {REPORT_FILE}")


# ===============================================================================
#  TERMINAL DISPLAY
# ===============================================================================

def print_banner() -> None:
    print(f"""
{Clr.CYAN}{Clr.BOLD}================================================================================
             SHOPIFY PRODUCT CREATION FROM CSV
                  Skynet International Inc.
================================================================================{Clr.RESET}
""")


def print_config(csv_file: str, dry_run: bool, resume: bool) -> None:
    mode = f"{Clr.YELLOW}DRY-RUN{Clr.RESET}" if dry_run else f"{Clr.GREEN}LIVE{Clr.RESET}"
    resume_str = f"{Clr.CYAN}YES{Clr.RESET}" if resume else f"{Clr.DIM}NO{Clr.RESET}"
    display_store = SHOPIFY_STORE_NAME if SHOPIFY_STORE_NAME.endswith(".myshopify.com") else f"{SHOPIFY_STORE_NAME}.myshopify.com"

    print(f"  {Clr.BOLD}Store:{Clr.RESET}           {display_store}")
    print(f"  {Clr.BOLD}CSV File:{Clr.RESET}        {csv_file}")
    print(f"  {Clr.BOLD}Mode:{Clr.RESET}            {mode}")
    print(f"  {Clr.BOLD}Resume:{Clr.RESET}          {resume_str}")
    print()
    print(f"  {Clr.DIM}Rate Limiting:{Clr.RESET}")
    print(f"  {Clr.DIM}  - Sleep between creates: {SLEEP_BETWEEN_CREATES}s{Clr.RESET}")
    print(f"  {Clr.DIM}  - Sleep between batches: {SLEEP_BETWEEN_BATCHES}s (every {BATCH_SIZE} products){Clr.RESET}")
    print()


def print_progress(stats: CreationStats) -> None:
    pct = stats.progress_pct
    bar_len = 30
    filled = int(bar_len * pct / 100)
    bar = "=" * filled + "-" * (bar_len - filled)

    print(
        f"\r  [{bar}] {pct:5.1f}%"
        f"  | Created: {Clr.GREEN}{stats.total_created}{Clr.RESET}"
        f"  | Skipped: {Clr.YELLOW}{stats.total_skipped_exists}{Clr.RESET}"
        f"  | Errors: {Clr.RED}{stats.total_errors}{Clr.RESET}"
        f"  | Remaining: {stats.products_remaining}"
        f"  | {stats.elapsed}",
        end="", flush=True
    )


def print_product_line(idx: int, total: int, product: CSVProduct,
                       action: str, dry_run: bool) -> None:
    if action == "CREATE":
        icon = "[OK]" if not dry_run else "[DRY]"
        colour = Clr.GREEN
    elif action == "SKIP_EXISTS":
        icon = "[--]"
        colour = Clr.YELLOW
    elif action == "SKIP_INVALID":
        icon = "[??]"
        colour = Clr.YELLOW
    elif action == "ERROR":
        icon = "[XX]"
        colour = Clr.RED
    else:
        icon = "[??]"
        colour = Clr.WHITE

    print(
        f"  {colour}{icon}{Clr.RESET} [{idx}/{total}]"
        f"  Handle: {product.handle[:25]:<25}"
        f"  SKU: {product.variant_sku[:20]:<20}"
        f"  Title: {product.title[:30]}"
    )


def print_final_summary(stats: CreationStats, dry_run: bool) -> None:
    mode = "DRY-RUN" if dry_run else "LIVE"
    print(f"""
{Clr.CYAN}{Clr.BOLD}================================================================================
                     PRODUCT CREATION COMPLETE ({mode})
================================================================================{Clr.RESET}

  {Clr.BOLD}Total in CSV:{Clr.RESET}              {stats.total_in_csv:,}
  {Clr.BOLD}Total Processed:{Clr.RESET}           {stats.total_processed:,}
  {Clr.GREEN}{Clr.BOLD}Successfully Created:{Clr.RESET}      {Clr.GREEN}{stats.total_created:,}{Clr.RESET}
  {Clr.YELLOW}{Clr.BOLD}Skipped (Already Exists):{Clr.RESET} {Clr.YELLOW}{stats.total_skipped_exists:,}{Clr.RESET}
  {Clr.YELLOW}{Clr.BOLD}Skipped (Invalid Data):{Clr.RESET}   {Clr.YELLOW}{stats.total_skipped_invalid:,}{Clr.RESET}
  {Clr.RED}{Clr.BOLD}Errors:{Clr.RESET}                   {Clr.RED}{stats.total_errors:,}{Clr.RESET}
  {Clr.MAGENTA}{Clr.BOLD}Yet to Process:{Clr.RESET}           {Clr.MAGENTA}{stats.products_yet_to_create:,}{Clr.RESET}
  {Clr.BOLD}Elapsed Time:{Clr.RESET}              {stats.elapsed}

  {Clr.DIM}Output Files:{Clr.RESET}
  {Clr.DIM}  - Audit Log:       {AUDIT_LOG_CSV}{Clr.RESET}
  {Clr.DIM}  - Error Log:       {ERROR_LOG_FILE}{Clr.RESET}
  {Clr.DIM}  - Failed Products: {FAILED_PRODUCTS_FILE}{Clr.RESET}
  {Clr.DIM}  - Report:          {REPORT_FILE}{Clr.RESET}
""")


# ===============================================================================
#  MAIN CREATION LOOP
# ===============================================================================

def run_creation(client: ShopifyGraphQLClient, products: list[CSVProduct],
                 stats: CreationStats, audit: AuditLogger,
                 failed_logger: FailedProductsLogger, dry_run: bool) -> None:
    """Main loop to create products."""

    total = len(products)

    for i, product in enumerate(products, start=1):
        global_idx = stats.total_processed + 1

        # Validate required fields
        if not product.handle or not product.title:
            stats.total_skipped_invalid += 1
            stats.total_processed += 1
            print_product_line(global_idx, stats.total_in_csv, product, "SKIP_INVALID", dry_run)
            audit.log(product, "SKIP", "INVALID_DATA", "", "Missing handle or title")
            logger.debug(f"SKIP (invalid): Row {product.row_number} - missing handle/title")
            continue

        # Check if product already exists
        if not dry_run:
            exists = check_product_exists(client, product.handle)
            if exists:
                stats.total_skipped_exists += 1
                stats.total_processed += 1
                print_product_line(global_idx, stats.total_in_csv, product, "SKIP_EXISTS", dry_run)
                audit.log(product, "SKIP", "ALREADY_EXISTS")
                logger.debug(f"SKIP (exists): {product.handle}")
                continue

        # Create the product
        if dry_run:
            stats.total_created += 1
            stats.total_processed += 1
            print_product_line(global_idx, stats.total_in_csv, product, "CREATE", dry_run)
            audit.log(product, "DRY_RUN", "WOULD_CREATE")
            logger.info(f"DRY-RUN: Would create {product.handle}")
        else:
            try:
                success, product_id, err_msg = create_product(client, product)

                if success:
                    stats.total_created += 1
                    stats.total_processed += 1
                    print_product_line(global_idx, stats.total_in_csv, product, "CREATE", dry_run)
                    audit.log(product, "CREATE", "SUCCESS", product_id)
                    logger.info(f"CREATED: {product.handle} -> {product_id}")
                else:
                    stats.total_errors += 1
                    stats.total_processed += 1
                    stats.add_failed_product(product, err_msg)
                    failed_logger.log(product, err_msg)
                    print_product_line(global_idx, stats.total_in_csv, product, "ERROR", dry_run)
                    print(f"          {Clr.RED}Error: {err_msg}{Clr.RESET}")
                    audit.log(product, "CREATE", "FAILED", "", err_msg)
                    logger.error(f"FAILED: {product.handle} - {err_msg}")

                # Rate limiting
                time.sleep(SLEEP_BETWEEN_CREATES)

            except Exception as e:
                stats.total_errors += 1
                stats.total_processed += 1
                stats.add_failed_product(product, str(e))
                failed_logger.log(product, str(e))
                print_product_line(global_idx, stats.total_in_csv, product, "ERROR", dry_run)
                print(f"          {Clr.RED}Exception: {e}{Clr.RESET}")
                audit.log(product, "CREATE", "EXCEPTION", "", str(e))
                logger.exception(f"EXCEPTION: {product.handle}")

                time.sleep(SLEEP_BETWEEN_CREATES)

        # Save progress periodically
        if i % BATCH_SIZE == 0:
            save_progress_state(stats, product.row_number)
            print()
            print_progress(stats)
            print()

            if not dry_run:
                logger.debug(f"Batch complete. Sleeping {SLEEP_BETWEEN_BATCHES}s")
                time.sleep(SLEEP_BETWEEN_BATCHES)

    # Final progress save
    if products:
        save_progress_state(stats, products[-1].row_number)


# ===============================================================================
#  MAIN ENTRY POINT
# ===============================================================================

def main() -> None:
    global SHOPIFY_STORE_NAME, SHOPIFY_ACCESS_TOKEN

    parser = argparse.ArgumentParser(
        description="Create Shopify products from a CSV file."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run without creating products (preview mode)."
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from the last saved progress."
    )
    parser.add_argument(
        "--csv", type=str, default=DEFAULT_CSV_FILE,
        help="Path to the CSV file."
    )
    parser.add_argument(
        "--store", type=str, default=None,
        help="Shopify store name (e.g., 'my-store' or 'my-store.myshopify.com')"
    )
    parser.add_argument(
        "--token", type=str, default=None,
        help="Shopify Admin API access token"
    )
    args = parser.parse_args()

    # Override store/token if provided via command line
    if args.store:
        SHOPIFY_STORE_NAME = args.store
    if args.token:
        SHOPIFY_ACCESS_TOKEN = args.token

    print_banner()

    # Validate CSV file exists
    if not Path(args.csv).exists():
        print(f"  {Clr.RED}ERROR: CSV file not found: {args.csv}{Clr.RESET}")
        sys.exit(1)

    print_config(args.csv, args.dry_run, args.resume)

    # Initialize client and loggers
    client = ShopifyGraphQLClient(SHOPIFY_STORE_NAME, SHOPIFY_ACCESS_TOKEN, API_VERSION)
    audit = AuditLogger(AUDIT_LOG_CSV)
    failed_logger = FailedProductsLogger(FAILED_PRODUCTS_FILE)

    # Handle resume
    start_row = 0
    if args.resume:
        start_row, stats = load_progress_state()
        if start_row > 0:
            print(f"  {Clr.CYAN}Resuming from row {start_row}{Clr.RESET}")
            print(f"     Previously processed: {stats.total_processed}")
            print(f"     Previously created:   {stats.total_created}")
            print()
        else:
            print(f"  {Clr.YELLOW}No saved progress found. Starting fresh.{Clr.RESET}\n")
            stats = CreationStats()
    else:
        stats = CreationStats()

    # Count and parse products
    print(f"  {Clr.BLUE}Counting products in CSV...{Clr.RESET}", end=" ", flush=True)
    total_count = count_csv_products(args.csv)
    stats.total_in_csv = total_count
    print(f"{Clr.GREEN}{total_count:,} products{Clr.RESET}")

    print(f"  {Clr.BLUE}Parsing CSV file...{Clr.RESET}", end=" ", flush=True)
    products = parse_csv_file(args.csv, start_row)
    print(f"{Clr.GREEN}{len(products):,} products to process{Clr.RESET}")

    if not products:
        print(f"\n  {Clr.YELLOW}No products to process.{Clr.RESET}\n")
        sys.exit(0)

    # Confirm before live run
    if not args.dry_run:
        print(f"\n  {Clr.BG_YELLOW}{Clr.BOLD} WARNING {Clr.RESET}"
              f"  This will create up to {Clr.BOLD}{len(products):,}{Clr.RESET} products.")
        print(f"  {Clr.DIM}Press Enter to continue, or Ctrl+C to abort...{Clr.RESET}", end=" ")
        try:
            input()
        except KeyboardInterrupt:
            print(f"\n\n  {Clr.YELLOW}Aborted.{Clr.RESET}\n")
            sys.exit(0)

    # Run creation
    print(f"\n  {Clr.GREEN}{Clr.BOLD}Starting product creation...{Clr.RESET}\n")
    logger.info(f"Creation started. CSV={args.csv}, dry_run={args.dry_run}, products={len(products)}")

    try:
        run_creation(client, products, stats, audit, failed_logger, args.dry_run)
    except KeyboardInterrupt:
        print(f"\n\n  {Clr.YELLOW}Interrupted. Progress saved.{Clr.RESET}")
        if products:
            save_progress_state(stats, products[stats.total_processed - 1].row_number if stats.total_processed > 0 else 0)
        logger.warning("Creation interrupted by user.")
    except Exception as e:
        print(f"\n  {Clr.RED}Error: {e}{Clr.RESET}")
        logger.critical(f"Creation halted: {e}")
    finally:
        stats.end_time = time.time()
        audit.close()
        failed_logger.close()

    # Cleanup progress file on successful completion
    if stats.products_remaining == 0 and Path(PROGRESS_STATE_FILE).exists():
        os.remove(PROGRESS_STATE_FILE)

    # Generate report
    generate_report(stats, args.csv, args.dry_run)
    print(f"\n  {Clr.CYAN}Report saved to: {REPORT_FILE}{Clr.RESET}")

    # Final summary
    print_final_summary(stats, args.dry_run)
    logger.info(f"Creation complete. Created={stats.total_created}, "
                f"Skipped={stats.total_skipped_exists}, Errors={stats.total_errors}")


if __name__ == "__main__":
    main()
