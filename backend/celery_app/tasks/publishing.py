"""
Shopify publishing tasks with transaction compensation.

Tasks:
- publish_batch: Orchestrates publishing a batch of products
- publish_product: Publishes a single product with rollback on failure

IMPORTANT: This implements the Saga pattern for transaction safety.
If Shopify publish succeeds but DB save fails, we rollback by deleting
the orphaned Shopify product.
"""
import logging
from typing import List, Dict, Any

import httpx
from fastapi import HTTPException

from celery_app.celery_config import celery_app
from celery_app.tasks.base import (
    BaseTask,
    run_async,
    get_shopify_client,
    get_supabase_store,
    get_batch_store
)
from app.core.exceptions import RetryableError, NonRetryableError
from app.db.sync_store import get_sync_store

logger = logging.getLogger(__name__)

# Fallback image when Boeing doesn't provide one
FALLBACK_IMAGE = "https://placehold.co/800x600/e8e8e8/666666/png?text=Image+Not+Available&font=roboto"


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="celery_app.tasks.publishing.publish_batch",
    max_retries=0  # Orchestrator doesn't retry
)
def publish_batch(self, batch_id: str, part_numbers: List[str], user_id: str = "system"):
    """
    Orchestrate publishing a batch of products to Shopify.

    Queues individual publish_product tasks for each part number.
    Rate limiting is handled at the task level (30/min for Shopify).

    Args:
        batch_id: Batch identifier for tracking
        part_numbers: List of part numbers to publish
        user_id: User ID who initiated the publish

    Returns:
        dict: Summary of queued tasks
    """
    logger.info(f"Starting publish batch {batch_id} with {len(part_numbers)} products for user {user_id}")
    batch_store = get_batch_store()

    try:
        # Update status to processing
        batch_store.update_status(batch_id, "processing")

        # Queue individual publish tasks with user_id
        for pn in part_numbers:
            publish_product.delay(batch_id, pn, user_id)

        logger.info(f"Queued {len(part_numbers)} publish tasks for batch {batch_id}")

        return {
            "batch_id": batch_id,
            "products_queued": len(part_numbers)
        }

    except Exception as e:
        logger.error(f"Publish batch failed: {e}")
        batch_store.update_status(batch_id, "failed", str(e))
        raise


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="celery_app.tasks.publishing.publish_product",
    autoretry_for=(RetryableError, ConnectionError, TimeoutError, httpx.ConnectError, httpx.ReadTimeout),
    dont_autoretry_for=(NonRetryableError,),
    retry_backoff=True,
    max_retries=3,
    rate_limit="30/m"  # Shopify rate limit
)
def publish_product(self, batch_id: str, part_number: str, user_id: str = "system"):
    """
    Publish a single product to Shopify with transaction compensation.

    IDEMPOTENCY:
    1. Check if product already has shopify_product_id → UPDATE Shopify
    2. Check if SKU exists in Shopify → UPDATE Shopify and save ID
    3. Otherwise → CREATE in Shopify

    TRANSACTION SAFETY:
    If DB save fails after Shopify CREATE succeeds, we attempt to delete
    the orphaned Shopify product to maintain consistency.
    (Updates don't need rollback - data just stays as it was)

    Args:
        batch_id: Batch identifier for tracking
        part_number: Part number to publish
        user_id: User ID who initiated the publish

    Returns:
        dict: Result including Shopify product ID
    """
    logger.info(f"Publishing {part_number} to Shopify for user {user_id}")

    shopify_client = get_shopify_client()
    supabase_store = get_supabase_store()
    batch_store = get_batch_store()

    # Track Shopify product ID for potential rollback
    shopify_product_id = None
    is_new_product = False  # Track if we created a new product (for rollback logic)

    try:
        # 1. Get product from staging (filtered by user_id for user-specific data)
        record = run_async(
            supabase_store.get_product_staging_by_part_number(part_number, user_id=user_id)
        )
        if not record:
            raise NonRetryableError(f"Product {part_number} not found in staging for user {user_id}")

        # 2. Validate price and inventory - reject products with zero or missing values
        price = record.get("price") or record.get("list_price") or record.get("net_price") or record.get("cost_per_item")
        inventory = record.get("inventory_quantity")

        if price is None or price == 0:
            raise NonRetryableError(
                f"Product {part_number} has no valid price (price=0 or missing). Cannot publish to Shopify."
            )

        if inventory is None or inventory == 0:
            raise NonRetryableError(
                f"Product {part_number} has no inventory (quantity=0 or missing). Cannot publish to Shopify."
            )

        # Check if already published (has shopify_product_id)
        existing_shopify_id = record.get("shopify_product_id")
        if existing_shopify_id:
            logger.info(f"Product {part_number} already published, will update shopify_id={existing_shopify_id}")

        # 2. Handle image upload
        boeing_image_url = record.get("boeing_image_url") or record.get("boeing_thumbnail_url")
        if boeing_image_url:
            try:
                image_url, image_path = run_async(
                    supabase_store.upload_image_from_url(boeing_image_url, part_number)
                )
                record["image_url"] = image_url
                record["image_path"] = image_path
                run_async(
                    supabase_store.update_product_staging_image(part_number, image_url, image_path)
                )
            except Exception as img_err:
                logger.warning(f"Image upload failed, using placeholder: {img_err}")
                record["image_url"] = FALLBACK_IMAGE
        else:
            record["image_url"] = FALLBACK_IMAGE

        # 3. Prepare Shopify payload
        record = _prepare_shopify_record(record)

        # 4. Publish or update in Shopify (idempotent)
        if existing_shopify_id:
            # Product was already published - UPDATE Shopify
            logger.info(f"Updating existing Shopify product {existing_shopify_id} for {part_number}")
            result = run_async(shopify_client.update_product(existing_shopify_id, record))
            shopify_product_id = result.get("product", {}).get("id") or existing_shopify_id
            is_new_product = False
        else:
            # Check if SKU already exists in Shopify
            sku = record.get("sku") or part_number
            found_shopify_id = run_async(shopify_client.find_product_by_sku(sku))

            if found_shopify_id:
                # SKU exists in Shopify - UPDATE instead of CREATE
                logger.info(f"Found existing Shopify product by SKU {sku}, updating {found_shopify_id}")
                result = run_async(shopify_client.update_product(found_shopify_id, record))
                shopify_product_id = result.get("product", {}).get("id") or found_shopify_id
                is_new_product = False
            else:
                # No existing product - CREATE new
                logger.info(f"Creating new Shopify product for {part_number}")
                result = run_async(shopify_client.publish_product(record))
                shopify_product_id = result.get("product", {}).get("id")
                is_new_product = True

        if not shopify_product_id:
            raise ValueError("Shopify did not return product ID")

        # 5. CRITICAL: Save to database with compensation on failure
        try:
            run_async(
                supabase_store.upsert_product(record, shopify_product_id=str(shopify_product_id), user_id=user_id)
            )
            # Also update staging with shopify_product_id
            run_async(
                supabase_store.update_product_staging_shopify_id(part_number, str(shopify_product_id), user_id=user_id)
            )
        except Exception as db_err:
            # COMPENSATION: Only delete if we created a NEW product
            # Updates don't need rollback - data just stays as it was
            if is_new_product:
                logger.error(
                    f"DB save failed after Shopify CREATE. "
                    f"Compensating by deleting Shopify product {shopify_product_id}"
                )
                try:
                    run_async(shopify_client.delete_product(shopify_product_id))
                    logger.info(f"Compensation successful: deleted orphaned Shopify product {shopify_product_id}")
                except Exception as rollback_err:
                    # CRITICAL: Manual reconciliation needed
                    logger.critical(
                        f"ORPHANED PRODUCT: Shopify ID {shopify_product_id} for part {part_number}. "
                        f"DB error: {db_err}, Rollback error: {rollback_err}"
                    )
                    batch_store.record_failure(
                        batch_id,
                        part_number,
                        f"ORPHANED: Shopify {shopify_product_id} - needs manual cleanup"
                    )
            else:
                logger.error(f"DB save failed after Shopify UPDATE for {part_number}: {db_err}")
            raise db_err

        # 6. Note: published_count is updated by database trigger on products table upsert

        # 7. Create/update sync schedule for daily Boeing sync (for ALL published products)
        try:
            sync_store = get_sync_store()
            initial_price = record.get("list_price") or record.get("net_price")
            initial_quantity = record.get("inventory_quantity")
            # Use sku from record which now stores full SKU with variant suffix
            full_sku = record.get("sku") or record.get("aviall_part_number") or part_number
            sync_store.upsert_sync_schedule(
                sku=full_sku,  # Full SKU for Boeing API queries
                user_id=user_id,
                initial_price=initial_price,
                initial_quantity=initial_quantity,
                shopify_product_id=str(shopify_product_id)
            )
            logger.info(f"{'Created' if is_new_product else 'Updated'} sync schedule for {full_sku}")
        except Exception as sync_err:
            # Don't fail the publish if sync schedule creation fails
            logger.warning(f"Failed to upsert sync schedule for {part_number}: {sync_err}")

        # 8. Check if batch is complete
        from celery_app.tasks.batch import check_batch_completion
        check_batch_completion.delay(batch_id)

        action = "updated" if not is_new_product else "created"
        logger.info(f"Published {part_number} -> Shopify ID: {shopify_product_id} ({action})")

        return {
            "success": True,
            "part_number": part_number,
            "shopify_product_id": str(shopify_product_id),
            "action": action
        }

    except NonRetryableError as e:
        # Don't retry validation errors
        batch_store.record_failure(batch_id, part_number, str(e))
        from celery_app.tasks.batch import check_batch_completion
        check_batch_completion.delay(batch_id)
        raise

    except HTTPException as e:
        # HTTP errors from Shopify API - 4xx errors are non-retryable
        error_msg = f"Shopify API error {e.status_code}: {e.detail}"
        logger.error(f"Failed to publish {part_number}: {error_msg}")

        if 400 <= e.status_code < 500:
            # Client errors (4xx) are not retryable - record failure immediately
            batch_store.record_failure(batch_id, part_number, error_msg)
            from celery_app.tasks.batch import check_batch_completion
            check_batch_completion.delay(batch_id)
            # Don't re-raise HTTPException as it can't be pickled by Celery
            raise NonRetryableError(error_msg)
        else:
            # Server errors (5xx) might be transient - let Celery retry
            if self.request.retries >= self.max_retries:
                batch_store.record_failure(batch_id, part_number, error_msg)
                from celery_app.tasks.batch import check_batch_completion
                check_batch_completion.delay(batch_id)
            raise RetryableError(error_msg)

    except Exception as e:
        logger.error(f"Failed to publish {part_number}: {e}")

        # Record failure if max retries exceeded
        if self.request.retries >= self.max_retries:
            batch_store.record_failure(batch_id, part_number, str(e))
            from celery_app.tasks.batch import check_batch_completion
            check_batch_completion.delay(batch_id)
        raise


def _strip_variant_suffix(value: str) -> str:
    """Strip variant suffix from SKU (e.g., 'WF338109=K3' -> 'WF338109')."""
    if not value:
        return ""
    return value.split("=", 1)[0]


def _prepare_shopify_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Prepare record with Shopify-specific fields.

    Transforms normalized Boeing data to Shopify product format.
    Includes pricing calculation (10% markup on list price).

    IMPORTANT: Strips variant suffix from SKU and title for Shopify display.
    Database stores full SKU (e.g., "WF338109=K3"), but Shopify shows
    stripped version (e.g., "WF338109").

    Args:
        record: Normalized product record from staging

    Returns:
        dict: Record with shopify field populated
    """
    record.setdefault("shopify", {})

    # Calculate pricing with 10% markup
    list_price = record.get("list_price")
    net_price = record.get("net_price")
    base_cost = list_price if list_price is not None else net_price
    shop_price = (base_cost * 1.1) if base_cost is not None else record.get("price")

    # Strip variant suffix from SKU and title for Shopify display
    shopify_sku = _strip_variant_suffix(record.get("sku") or "")
    shopify_title = _strip_variant_suffix(record.get("title") or "")

    record["shopify"].update({
        "title": shopify_title,
        "sku": shopify_sku,
        "description": record.get("boeing_name") or shopify_title,
        "body_html": record.get("body_html") or "",
        "vendor": record.get("vendor"),
        "manufacturer": record.get("supplier_name") or record.get("vendor"),
        "price": shop_price,
        "cost_per_item": base_cost,
        "currency": record.get("currency"),
        "unit_of_measure": record.get("base_uom"),
        "country_of_origin": record.get("country_of_origin"),
        "length": record.get("dim_length"),
        "width": record.get("dim_width"),
        "height": record.get("dim_height"),
        "dim_uom": record.get("dim_uom"),
        "weight": record.get("weight"),
        "weight_uom": record.get("weight_unit"),
        "inventory_quantity": record.get("inventory_quantity"),
        "location_summary": record.get("location_summary"),
        "product_image": record.get("image_url"),
        "thumbnail_image": record.get("boeing_thumbnail_url"),
        "cert": "FAA 8130-3",
        "condition": record.get("condition") or "NE",
        "pma": record.get("pma"),
        "estimated_lead_time_days": record.get("estimated_lead_time_days") or 3,
        "trace": record.get("trace"),
        "expiration_date": record.get("expiration_date"),
        "notes": record.get("notes"),
    })

    # Use stripped title for display name
    record["name"] = record.get("boeing_name") or shopify_title
    record["description"] = record.get("boeing_description") or ""

    return record
