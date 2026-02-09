"""
Sync Scheduler Celery Tasks.

Main tasks for the Boeing-Shopify sync scheduler:
- dispatch_hourly_sync: Runs periodically, dispatches batches for current time bucket
- dispatch_retry_sync: Runs every 4 hours, retries failed products
- end_of_day_cleanup: Daily cleanup at midnight UTC
- sync_boeing_batch: Fetches data from Boeing API (rate limited)
- sync_shopify_product: Updates a single product in Shopify

CRITICAL: All Boeing API calls go through the global rate limiter.
This ensures we NEVER exceed 2 requests/minute across all workers.

SYNC MODES:
- production: Uses hour buckets (0-23), dispatch at :45 of each hour
- testing: Uses minute buckets (0-5), dispatch every 10 minutes for faster testing
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

from celery_app.celery_config import celery_app, SYNC_MODE, SYNC_TEST_BUCKET_COUNT
from celery_app.tasks.base import (
    BaseTask,
    run_async,
    get_shopify_client,
    get_settings,
)
from app.core.exceptions import RetryableError, NonRetryableError
from app.db.sync_store import get_sync_store
from app.utils.rate_limiter import get_boeing_rate_limiter
from app.utils.sync_helpers import (
    compute_boeing_hash,
    compute_sync_hash,
    extract_boeing_product_data,
    create_out_of_stock_data,
    should_update_shopify,
    get_slot_distribution,
    calculate_batch_groups,
    aggregate_filling_slots,
    get_current_hour_utc,
    MAX_SKUS_PER_API_CALL,
)
from app.clients.boeing_client import BoeingClient

logger = logging.getLogger(__name__)

# Configuration
BATCH_SIZE = MAX_SKUS_PER_API_CALL  # 10 SKUs per Boeing API call
MIN_PRODUCTS_FOR_ACTIVE_SLOT = 10


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="celery_app.tasks.sync_dispatcher.dispatch_hourly_sync",
    max_retries=0  # Dispatcher doesn't retry
)
def dispatch_hourly_sync(self):
    """
    Main sync dispatcher.

    PRODUCTION MODE (SYNC_MODE=production):
    - Called every 15 minutes by Celery Beat
    - Only processes at :45 mark (sync window)
    - Uses hour buckets (0-23)

    TESTING MODE (SYNC_MODE=testing):
    - Called every 10 minutes by Celery Beat
    - Processes immediately (no sync window check)
    - Uses minute buckets (0-5 for 10-min intervals)

    Algorithm:
    1. Get products for current time bucket
    2. Categorize: active slots (10+) vs filling slots (<10)
    3. Active slots: dispatch normally
    4. Filling slots: aggregate and borrow to make complete batches

    Returns:
        dict: Summary of dispatched batches
    """
    now = datetime.now(timezone.utc)

    # =========================================================================
    # TESTING MODE: Use minute buckets, no sync window check
    # =========================================================================
    if SYNC_MODE == "testing":
        # Calculate minute bucket (0-5 for 10-min intervals)
        # Bucket 0 = minutes 0-9, Bucket 1 = minutes 10-19, etc.
        current_bucket = now.minute // 10
        logger.info("=" * 60)
        logger.info(f"🧪 [TESTING MODE] Sync Dispatch Started")
        logger.info(f"   • Minute bucket: {current_bucket} (of {SYNC_TEST_BUCKET_COUNT})")
        logger.info(f"   • Current time: {now.strftime('%H:%M:%S')} UTC")
        logger.info(f"   • Processing immediately (no sync window)")
        logger.info("=" * 60)
    # =========================================================================
    # PRODUCTION MODE: Use hour buckets, check sync window
    # =========================================================================
    else:
        current_bucket = get_current_hour_utc()
        # Only run at :45 mark (sync window)
        if now.minute < 45:
            logger.debug(f"Not in sync window (minute={now.minute}), skipping")
            return {"status": "skipped", "reason": "not_in_sync_window"}
        logger.info("=" * 60)
        logger.info(f"🚀 [PRODUCTION MODE] Sync Dispatch Started")
        logger.info(f"   • Hour bucket: {current_bucket} (of 24)")
        logger.info(f"   • Current time: {now.strftime('%H:%M:%S')} UTC")
        logger.info(f"   • Sync window: Active (minute >= 45)")
        logger.info("=" * 60)

    sync_store = get_sync_store()

    try:
        # Get slot distribution
        slot_counts = sync_store.get_slot_counts()
        distribution = get_slot_distribution(slot_counts)

        logger.info(
            f"Slot distribution: {distribution['active_count']} active, "
            f"{distribution['filling_count']} filling, "
            f"{distribution['dormant_count']} dormant"
        )

        batches_dispatched = 0
        products_dispatched = 0

        # Check if current bucket is an active or filling slot
        current_count = slot_counts.get(current_bucket, 0)

        if current_count >= MIN_PRODUCTS_FOR_ACTIVE_SLOT:
            # ACTIVE SLOT: Process normally
            products = sync_store.get_products_for_hour(
                current_bucket,
                status_filter=["pending", "success"]  # Exclude currently syncing
            )

            if products:
                # Create batches of 10
                batches = calculate_batch_groups(products, BATCH_SIZE)

                for batch in batches:
                    # sku field now stores full SKU with variant suffix (e.g., "WF338109=K3")
                    skus = [p["sku"] for p in batch]
                    user_id = batch[0].get("user_id", "system")

                    # Mark as syncing and dispatch
                    sync_store.mark_products_syncing(skus)
                    sync_boeing_batch.delay(skus, user_id, current_bucket)

                    batches_dispatched += 1
                    products_dispatched += len(skus)

                logger.info(
                    f"Active slot {current_bucket}: dispatched {batches_dispatched} batches, "
                    f"{products_dispatched} products"
                )

        elif current_count > 0:
            # FILLING SLOT: Need to aggregate with other filling slots
            logger.info(f"Filling slot {current_bucket} ({current_count} products)")

            # Get all products from all filling slots
            all_filling_products = []
            for slot in distribution['filling_slots']:
                slot_products = sync_store.get_products_for_hour(
                    slot,
                    status_filter=["pending", "success"]
                )
                all_filling_products.extend(slot_products)

            if all_filling_products:
                # Create complete batches from aggregated products
                batches = calculate_batch_groups(all_filling_products, BATCH_SIZE)

                for batch in batches:
                    # sku field now stores full SKU with variant suffix
                    skus = [p["sku"] for p in batch]
                    # Use first product's user_id
                    user_id = batch[0].get("user_id", "system")

                    sync_store.mark_products_syncing(skus)
                    sync_boeing_batch.delay(skus, user_id, current_bucket)

                    batches_dispatched += 1
                    products_dispatched += len(skus)

                logger.info(
                    f"Filling slots aggregated: {len(distribution['filling_slots'])} slots, "
                    f"{batches_dispatched} batches, {products_dispatched} products"
                )

        else:
            # DORMANT SLOT: Nothing to do
            logger.debug(f"Dormant slot {current_bucket}, no products to sync")

        # Reset any stuck products (from crashed workers)
        stuck_reset = sync_store.reset_stuck_products(stuck_threshold_minutes=30)
        if stuck_reset > 0:
            logger.warning(f"Reset {stuck_reset} stuck products")

        return {
            "status": "completed",
            "mode": SYNC_MODE,
            "bucket": current_bucket,
            "bucket_type": "minute" if SYNC_MODE == "testing" else "hour",
            "batches_dispatched": batches_dispatched,
            "products_dispatched": products_dispatched,
            "stuck_reset": stuck_reset,
        }

    except Exception as e:
        logger.error(f"Sync dispatch failed (mode={SYNC_MODE}): {e}")
        raise


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="celery_app.tasks.sync_dispatcher.dispatch_retry_sync",
    max_retries=0
)
def dispatch_retry_sync(self):
    """
    Retry dispatcher for failed products.

    Called every 4 hours by Celery Beat.
    Collects failed products from ALL slots and batches them.

    Returns:
        dict: Summary of retried batches
    """
    logger.info("=== Retry Sync Dispatch ===")

    sync_store = get_sync_store()

    try:
        # Get all failed products ready for retry
        failed_products = sync_store.get_failed_products_for_retry(limit=200)

        if not failed_products:
            logger.info("No failed products to retry")
            return {"status": "completed", "retries": 0}

        logger.info(f"Found {len(failed_products)} products for retry")

        # Group by user_id and create batches
        user_batches: Dict[str, List[Dict]] = {}
        for product in failed_products:
            user_id = product.get("user_id", "system")
            if user_id not in user_batches:
                user_batches[user_id] = []
            user_batches[user_id].append(product)

        batches_dispatched = 0
        products_dispatched = 0

        for user_id, products in user_batches.items():
            batches = calculate_batch_groups(products, BATCH_SIZE)

            for batch in batches:
                # sku field now stores full SKU with variant suffix
                skus = [p["sku"] for p in batch]
                sync_store.mark_products_syncing(skus)
                sync_boeing_batch.delay(skus, user_id, -1)  # -1 indicates retry

                batches_dispatched += 1
                products_dispatched += len(skus)

        logger.info(
            f"Retry dispatch: {batches_dispatched} batches, {products_dispatched} products"
        )

        return {
            "status": "completed",
            "batches_dispatched": batches_dispatched,
            "products_retried": products_dispatched,
        }

    except Exception as e:
        logger.error(f"Retry sync dispatch failed: {e}")
        raise


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="celery_app.tasks.sync_dispatcher.end_of_day_cleanup",
    max_retries=0
)
def end_of_day_cleanup(self):
    """
    Daily cleanup task at midnight UTC.

    Tasks:
    1. Reset any stuck 'syncing' products
    2. Log daily statistics
    3. Identify products needing attention

    Returns:
        dict: Cleanup summary
    """
    logger.info("=== End of Day Cleanup ===")

    sync_store = get_sync_store()

    try:
        # Reset stuck products
        stuck_reset = sync_store.reset_stuck_products(stuck_threshold_minutes=60)

        # Get status summary for logging
        status_summary = sync_store.get_sync_status_summary()
        distribution = sync_store.get_slot_distribution_summary()

        logger.info(f"Daily Stats: {status_summary}")
        logger.info(f"Slot Distribution: efficiency={distribution['efficiency_percent']}%")

        # Log high-failure products
        if status_summary.get("high_failure_count", 0) > 0:
            logger.warning(
                f"Products with 3+ failures: {status_summary['high_failure_count']}"
            )

        return {
            "status": "completed",
            "stuck_reset": stuck_reset,
            "summary": status_summary,
            "efficiency": distribution["efficiency_percent"],
        }

    except Exception as e:
        logger.error(f"End of day cleanup failed: {e}")
        raise


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="celery_app.tasks.sync_dispatcher.sync_boeing_batch",
    autoretry_for=(RetryableError, ConnectionError, TimeoutError, httpx.ConnectError, httpx.ReadTimeout),
    dont_autoretry_for=(NonRetryableError,),
    retry_backoff=True,
    max_retries=2,
)
def sync_boeing_batch(self, skus: List[str], user_id: str, source_hour: int):
    """
    Fetch price/availability from Boeing API for a batch of SKUs.

    CRITICAL: Uses global rate limiter to ensure we never exceed 2 req/min.

    Args:
        skus: List of SKUs with full variant suffix (max 10, e.g., "WF338109=K3")
        user_id: User ID for context
        source_hour: Source hour bucket (-1 for retries)

    Returns:
        dict: Summary of sync results
    """
    if not skus:
        return {"status": "skipped", "reason": "empty_batch"}

    logger.info(f"Boeing batch sync: {len(skus)} SKUs from hour={source_hour}")

    sync_store = get_sync_store()
    rate_limiter = get_boeing_rate_limiter()

    try:
        # CRITICAL: Wait for rate limiter token before calling Boeing API
        logger.debug("Acquiring rate limiter token...")
        token_acquired = rate_limiter.wait_for_token(timeout=120)

        if not token_acquired:
            logger.warning("Rate limiter timeout - requeueing batch")
            raise RetryableError("Rate limiter timeout")

        logger.debug("Token acquired, calling Boeing API...")

        # Call Boeing API with full SKUs (includes variant suffix like "WF338109=K3")
        settings = get_settings()
        boeing_client = BoeingClient(settings)

        boeing_response = run_async(
            boeing_client.fetch_price_availability_batch(skus)
        )

        # Process each SKU from response
        success_count = 0
        failure_count = 0
        no_change_count = 0
        out_of_stock_count = 0

        for sku in skus:
            try:
                # Extract data for this SKU (full SKU with variant suffix)
                product_data = extract_boeing_product_data(boeing_response, sku)

                if not product_data:
                    # SKU not found in Boeing response with showNoStock=false
                    # This means the product is OUT OF STOCK
                    logger.info(f"SKU {sku} not in Boeing response - treating as out of stock")
                    product_data = create_out_of_stock_data(sku)
                    out_of_stock_count += 1

                # Get current sync record for comparison
                records = sync_store.get_products_by_skus([sku])
                record = records[0] if records else {}

                # Check if update needed
                should_update, reason = should_update_shopify(
                    product_data,
                    record.get("last_boeing_hash"),
                    record.get("last_price"),
                    record.get("last_quantity"),
                )

                if should_update:
                    # Queue Shopify update
                    sync_shopify_product.delay(sku, user_id, product_data)
                    success_count += 1
                    logger.debug(f"Queued Shopify update for {sku}: {reason}")
                else:
                    # No change - just update sync timestamp
                    new_hash = compute_boeing_hash(product_data)
                    sync_store.update_sync_success(
                        sku,
                        new_hash,
                        product_data.get("list_price"),
                        product_data.get("inventory_quantity"),
                        inventory_status=product_data.get("inventory_status"),
                        locations=product_data.get("location_quantities"),  # JSONB list
                    )
                    no_change_count += 1
                    logger.debug(f"No change for {sku}")

            except Exception as sku_err:
                logger.error(f"Error processing SKU {sku}: {sku_err}")
                sync_store.update_sync_failure(sku, str(sku_err))
                failure_count += 1

        logger.info(
            f"Boeing batch complete: {success_count} updates queued, "
            f"{no_change_count} unchanged, {out_of_stock_count} out-of-stock, {failure_count} failed"
        )

        return {
            "status": "completed",
            "skus_processed": len(skus),
            "updates_queued": success_count,
            "no_change": no_change_count,
            "out_of_stock": out_of_stock_count,
            "failures": failure_count,
        }

    except (RetryableError, ConnectionError, TimeoutError) as e:
        # Mark all SKUs as needing retry
        for sku in skus:
            sync_store.update_sync_failure(sku, str(e))
        raise

    except Exception as e:
        logger.error(f"Boeing batch sync failed: {e}")
        for sku in skus:
            sync_store.update_sync_failure(sku, str(e))
        raise


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="celery_app.tasks.sync_dispatcher.sync_shopify_product",
    autoretry_for=(RetryableError, ConnectionError, TimeoutError, httpx.ConnectError, httpx.ReadTimeout),
    dont_autoretry_for=(NonRetryableError,),
    retry_backoff=True,
    max_retries=3,
    rate_limit="30/m"  # Shopify rate limit
)
def sync_shopify_product(self, sku: str, user_id: str, boeing_data: Dict[str, Any]):
    """
    Update a single product in Shopify with new Boeing data.

    Args:
        sku: Product SKU
        user_id: User ID
        boeing_data: Normalized data from Boeing API

    Returns:
        dict: Update result
    """
    logger.info(f"Updating Shopify for {sku}")

    sync_store = get_sync_store()
    shopify_client = get_shopify_client()

    try:
        # Get Shopify product ID from our database
        from app.db.supabase_store import get_supabase_store
        supabase_store = get_supabase_store()

        product_record = run_async(
            supabase_store.get_product_by_sku(sku, user_id)
        )

        if not product_record:
            logger.error(f"Product {sku} not found in database")
            sync_store.update_sync_failure(sku, "Product not found in database")
            raise NonRetryableError(f"Product {sku} not found")

        shopify_product_id = product_record.get("shopify_product_id")
        if not shopify_product_id:
            logger.error(f"Product {sku} has no Shopify product ID")
            sync_store.update_sync_failure(sku, "No Shopify product ID")
            raise NonRetryableError(f"Product {sku} has no Shopify ID")

        # Prepare update data
        new_price = boeing_data.get("list_price") or boeing_data.get("net_price")
        new_quantity = boeing_data.get("inventory_quantity", 0)
        inventory_status = boeing_data.get("inventory_status")
        location_quantities = boeing_data.get("location_quantities") or []
        location_summary = boeing_data.get("location_summary")
        is_out_of_stock = boeing_data.get("is_missing_sku", False) or inventory_status == "out_of_stock"

        # Apply 10% markup for Shopify price (only if we have a price)
        shopify_price = round(new_price * 1.1, 2) if new_price else None

        # Build metafields if we have location summary
        metafields = None
        if location_summary:
            metafields = [{
                "namespace": "boeing",
                "key": "location_summary",
                "value": location_summary,
                "type": "single_line_text_field"
            }]

        # Update Shopify - use location-based inventory if available
        if location_quantities and not is_out_of_stock:
            # Update inventory per location
            result = run_async(
                shopify_client.update_product_pricing(
                    shopify_product_id,
                    price=shopify_price,
                    metafields=metafields,
                )
            )
            # Set inventory levels per location
            run_async(
                shopify_client.update_inventory_by_location(
                    shopify_product_id,
                    location_quantities,
                )
            )
        else:
            # No location data or out of stock - set total quantity
            result = run_async(
                shopify_client.update_product_pricing(
                    shopify_product_id,
                    price=shopify_price,
                    quantity=new_quantity,
                    metafields=metafields,
                )
            )

        # Update sync record on success - CRITICAL: must succeed or task should fail
        new_hash = compute_boeing_hash(boeing_data)
        try:
            sync_store.update_sync_success(
                sku,
                new_hash,
                new_price,
                new_quantity,
                inventory_status=inventory_status,
                locations=location_quantities,  # JSONB list, not string
            )
        except Exception as db_err:
            # DB update failed - this is critical, task should fail
            error_msg = f"Shopify updated but DB sync record failed: {db_err}"
            logger.error(f"CRITICAL: {error_msg}")
            # Don't call update_sync_failure here as it might also fail
            # Let the task retry
            raise RetryableError(error_msg)

        # Also update our products table
        run_async(
            supabase_store.update_product_pricing(
                sku,
                user_id,
                price=shopify_price,
                cost=new_price,
                inventory=new_quantity,
            )
        )

        logger.info(
            f"Shopify updated for {sku}: price=${shopify_price}, qty={new_quantity}, hash={new_hash}"
        )

        return {
            "status": "success",
            "sku": sku,
            "shopify_product_id": shopify_product_id,
            "new_price": shopify_price,
            "new_quantity": new_quantity,
            "hash": new_hash,
        }

    except HTTPException as e:
        error_msg = f"Shopify API error {e.status_code}: {e.detail}"
        logger.error(f"Failed to update Shopify for {sku}: {error_msg}")

        if 400 <= e.status_code < 500:
            sync_store.update_sync_failure(sku, error_msg)
            raise NonRetryableError(error_msg)
        else:
            raise RetryableError(error_msg)

    except NonRetryableError:
        raise

    except Exception as e:
        logger.error(f"Failed to update Shopify for {sku}: {e}")
        sync_store.update_sync_failure(sku, str(e))
        raise


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="celery_app.tasks.sync_dispatcher.sync_single_product_immediate",
    max_retries=2,
)
def sync_single_product_immediate(self, sku: str, user_id: str):
    """
    Immediately sync a single product (used after initial publish).

    This bypasses the hourly scheduler for immediate sync.
    Still respects rate limiter.

    Args:
        sku: Product SKU
        user_id: User ID

    Returns:
        dict: Sync result
    """
    logger.info(f"Immediate sync requested for {sku}")

    # Just dispatch as a single-item batch
    sync_boeing_batch.delay([sku], user_id, -2)  # -2 indicates immediate

    return {
        "status": "queued",
        "sku": sku,
    }
