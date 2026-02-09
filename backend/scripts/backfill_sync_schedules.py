"""
Backfill Sync Schedules for Existing Products.

This script creates sync schedule entries for all existing published products
that don't already have one. Uses the least-loaded packing algorithm to
distribute products across hour slots efficiently.

Usage:
    # Dry run (no changes)
    python -m scripts.backfill_sync_schedules --dry-run

    # Execute backfill
    python -m scripts.backfill_sync_schedules

    # Limit to specific user
    python -m scripts.backfill_sync_schedules --user-id "user_123"

    # Show distribution after backfill
    python -m scripts.backfill_sync_schedules --show-distribution
"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from typing import Dict, List, Any

# Add parent directory to path for imports
sys.path.insert(0, str(__file__).replace("\\scripts\\backfill_sync_schedules.py", ""))

from app.db.supabase_client import get_supabase_client
from app.db.sync_store import SyncStore, get_sync_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def get_published_products(user_id: str = None) -> List[Dict[str, Any]]:
    """
    Get all products with shopify_product_id (published to Shopify).

    Args:
        user_id: Optional filter by user_id

    Returns:
        List of product records
    """
    client = get_supabase_client()

    query = client.table("products") \
        .select("sku, user_id, shopify_product_id, price, inventory_quantity") \
        .not_.is_("shopify_product_id", "null")

    if user_id:
        query = query.eq("user_id", user_id)

    result = query.execute()
    return result.data or []


def get_existing_schedules() -> set:
    """Get set of SKUs that already have sync schedules."""
    client = get_supabase_client()

    result = client.table("product_sync_schedule") \
        .select("sku") \
        .execute()

    return {r["sku"] for r in (result.data or [])}


def backfill_sync_schedules(
    products: List[Dict[str, Any]],
    existing_skus: set,
    dry_run: bool = True
) -> Dict[str, Any]:
    """
    Create sync schedules for products that don't have one.

    Args:
        products: List of published products
        existing_skus: Set of SKUs that already have schedules
        dry_run: If True, don't actually create schedules

    Returns:
        Summary of backfill operation
    """
    sync_store = get_sync_store()

    # Filter to products needing schedules
    needs_schedule = [
        p for p in products
        if p["sku"] not in existing_skus
    ]

    logger.info(f"Total published products: {len(products)}")
    logger.info(f"Already have schedules: {len(existing_skus)}")
    logger.info(f"Need schedules: {len(needs_schedule)}")

    if dry_run:
        logger.info("DRY RUN - No changes will be made")

        # Simulate distribution
        slot_counts = sync_store.get_slot_counts()
        total = sum(slot_counts.values())

        for i, product in enumerate(needs_schedule):
            # Simulate least-loaded allocation
            total += 1
            min_slots = min(24, max(1, (total + 9) // 10))

            min_count = float('inf')
            best_slot = 0
            for slot in range(min_slots):
                count = slot_counts.get(slot, 0)
                if count < min_count:
                    min_count = count
                    best_slot = slot

            slot_counts[best_slot] = slot_counts.get(best_slot, 0) + 1

            if (i + 1) % 100 == 0:
                logger.info(f"Simulated {i + 1}/{len(needs_schedule)} allocations...")

        logger.info("Simulated final distribution:")
        for hour in range(24):
            count = slot_counts.get(hour, 0)
            if count > 0:
                status = "ACTIVE" if count >= 10 else "filling"
                logger.info(f"  Hour {hour:02d}: {count:4d} products ({status})")

        return {
            "status": "dry_run",
            "products_found": len(products),
            "already_scheduled": len(existing_skus),
            "would_create": len(needs_schedule),
        }

    # Actually create schedules
    created = 0
    errors = 0

    for i, product in enumerate(needs_schedule):
        try:
            sync_store.create_sync_schedule(
                sku=product["sku"],
                user_id=product["user_id"],
                initial_price=product.get("price"),
                initial_quantity=product.get("inventory_quantity"),
            )
            created += 1

            if (i + 1) % 50 == 0:
                logger.info(f"Created {created}/{len(needs_schedule)} schedules...")

        except Exception as e:
            logger.error(f"Failed to create schedule for {product['sku']}: {e}")
            errors += 1

    logger.info(f"Backfill complete: {created} created, {errors} errors")

    return {
        "status": "completed",
        "products_found": len(products),
        "already_scheduled": len(existing_skus),
        "created": created,
        "errors": errors,
    }


def show_distribution():
    """Display current slot distribution."""
    sync_store = get_sync_store()
    summary = sync_store.get_slot_distribution_summary()

    logger.info("=== Current Slot Distribution ===")
    logger.info(f"Total products: {summary['total_products']}")
    logger.info(f"Active slots (10+): {summary['active_count']}")
    logger.info(f"Filling slots (1-9): {summary['filling_count']}")
    logger.info(f"Dormant slots (0): {summary['dormant_count']}")
    logger.info(f"Optimal slots needed: {summary['optimal_slots_needed']}")
    logger.info(f"Efficiency: {summary['efficiency_percent']}%")

    logger.info("\nSlot breakdown:")
    for hour in range(24):
        count = summary['slot_counts'].get(hour, 0)
        if count > 0:
            bar = "█" * min(count // 2, 20)
            status = "ACTIVE" if count >= 10 else "filling"
            logger.info(f"  Hour {hour:02d}: {count:4d} {bar} ({status})")
        else:
            logger.info(f"  Hour {hour:02d}:    0 (dormant)")


def main():
    parser = argparse.ArgumentParser(
        description="Backfill sync schedules for existing published products"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate backfill without making changes"
    )
    parser.add_argument(
        "--user-id",
        type=str,
        help="Filter to specific user ID"
    )
    parser.add_argument(
        "--show-distribution",
        action="store_true",
        help="Show current slot distribution"
    )

    args = parser.parse_args()

    if args.show_distribution:
        show_distribution()
        return

    logger.info("=== Sync Schedule Backfill ===")
    logger.info(f"Started at: {datetime.now(timezone.utc).isoformat()}")

    # Get data
    products = get_published_products(args.user_id)
    existing = get_existing_schedules()

    if not products:
        logger.info("No published products found")
        return

    # Run backfill
    result = backfill_sync_schedules(
        products,
        existing,
        dry_run=args.dry_run
    )

    logger.info(f"\nResult: {result}")

    # Show distribution after backfill
    if not args.dry_run:
        logger.info("\n")
        show_distribution()


if __name__ == "__main__":
    main()
