"""
Publishing tasks — Shopify product create/update with saga pattern.

Shopify publishing tasks with transaction compensation.

Tasks:
- publish_batch: Orchestrates publishing a batch of products
- publish_product: Publishes a single product with rollback on failure

Implements the Saga pattern for transaction safety.
If Shopify publish succeeds but DB save fails, we rollback by deleting
the orphaned Shopify product.
Version: 1.0.0
"""
import logging
from typing import List, Dict, Any

import httpx
from fastapi import HTTPException

from app.celery_app.celery_config import celery_app
from app.celery_app.tasks.base import (
    BaseTask,
    run_async,
    get_shopify_orchestrator,
    get_staging_store,
    get_product_store,
    get_image_store,
    get_batch_store,
    get_settings,
)
from app.core.exceptions import RetryableError, NonRetryableError
from app.core.constants.pricing import FALLBACK_IMAGE_URL
from app.celery_app.tasks.batch import check_batch_completion, reconcile_batch
from app.db.sync_store import get_sync_store
from app.utils.slot_manager import precompute_slot_assignments

logger = logging.getLogger(__name__)


class PublishTask(BaseTask):
    """Custom base for publish_product — adds last-resort failure recording."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """
        Last-resort failure handler invoked by Celery AFTER all retries are
        exhausted and the task's own except blocks have run.

        This catches the case where the in-task error handler itself fails
        (double failure), ensuring the part is never silently lost.
        """
        super().on_failure(exc, task_id, args, kwargs, einfo)
        batch_id = args[0] if args and len(args) > 0 else kwargs.get("batch_id")
        part_number = args[1] if args and len(args) > 1 else kwargs.get("part_number")
        user_id = args[2] if args and len(args) > 2 else kwargs.get("user_id", "system")

        if not batch_id or not part_number:
            return

        try:
            _bs = get_batch_store()
            # Check if failure was already recorded by the in-task handler
            batch = _bs.get_batch(batch_id)
            if batch:
                already_recorded = any(
                    item.get("part_number") == part_number
                    for item in (batch.get("failed_items") or [])
                )
                if not already_recorded:
                    logger.warning(
                        f"on_failure safety net: recording missed failure for "
                        f"{part_number} in batch {batch_id}"
                    )
                    _bs.record_failure(
                        batch_id, part_number,
                        f"Task failed (on_failure safety net): {exc}",
                        stage="publishing",
                    )
            # Always try to update staging status to 'failed'
            try:
                _ss = get_staging_store()
                run_async(_ss.update_product_staging_status(part_number, "failed", user_id))
            except Exception:
                pass
            check_batch_completion.delay(batch_id)
        except Exception as e:
            logger.critical(
                f"on_failure safety net ALSO failed for {part_number} in "
                f"batch {batch_id}: {e}"
            )


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.publishing.publish_batch",
    max_retries=0  # Orchestrator doesn't retry
)
def publish_batch(self, batch_id: str, part_numbers: List[str], user_id: str = "system"):
    """
    Orchestrate publishing a batch of products to Shopify.

    Queues individual publish_product tasks for each part number.
    Rate limiting is handled at the task level (30/min for Shopify).
    """
    logger.info(f"Starting publish batch {batch_id} with {len(part_numbers)} products for user {user_id}")
    batch_store = get_batch_store()

    try:
        batch_store.update_status(batch_id, "processing")

        # Pre-compute sync-schedule slot assignments for the entire batch.
        # This eliminates the read-compute-write race condition that occurs
        # when concurrent publish_product workers each independently call
        # get_least_loaded_slot() against the same DB snapshot.
        try:
            sync_store = get_sync_store()
            slot_counts = sync_store.get_slot_counts()
            slot_assignments = precompute_slot_assignments(slot_counts, len(part_numbers))
            logger.info(
                f"Pre-computed slot assignments for {len(part_numbers)} products "
                f"(current distribution: {slot_counts})"
            )
        except Exception as slot_err:
            logger.warning(
                f"Could not pre-compute slot assignments, "
                f"falling back to per-task allocation: {slot_err}"
            )
            slot_assignments = [None] * len(part_numbers)

        for i, pn in enumerate(part_numbers):
            publish_product.delay(batch_id, pn, user_id, assigned_slot=slot_assignments[i])

        logger.info(f"Queued {len(part_numbers)} publish tasks for batch {batch_id}")

        # Schedule deferred reconciliation as a safety net.
        # If any publish task silently fails (task lost, worker crash, etc.),
        # this will detect the missing parts and record them as failed.
        # Delay = 3 min per 10 products (min 5 min, max 30 min) to allow
        # for Shopify rate limiting (30/min).
        reconcile_delay = min(max((len(part_numbers) // 10) * 3 * 60, 300), 1800)
        reconcile_batch.apply_async(args=[batch_id], countdown=reconcile_delay)
        logger.info(f"Scheduled reconciliation for batch {batch_id} in {reconcile_delay}s")

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
    base=PublishTask,
    name="tasks.publishing.publish_product",
    autoretry_for=(RetryableError, ConnectionError, TimeoutError, httpx.ConnectError, httpx.ReadTimeout),
    dont_autoretry_for=(NonRetryableError,),
    retry_backoff=True,
    max_retries=3,
    rate_limit="30/m"  # Shopify rate limit
)
def publish_product(self, batch_id: str, part_number: str, user_id: str = "system", assigned_slot: int = None):
    """
    Publish a single product to Shopify with transaction compensation.

    IDEMPOTENCY:
    1. Check if product already has shopify_product_id -> UPDATE Shopify
    2. Check if SKU exists in Shopify -> UPDATE Shopify and save ID
    3. Otherwise -> CREATE in Shopify

    TRANSACTION SAFETY (Saga pattern):
    If DB save fails after Shopify CREATE succeeds, we attempt to delete
    the orphaned Shopify product to maintain consistency.
    """
    logger.info(f"Publishing {part_number} to Shopify for user {user_id}")

    shopify_product_id = None
    is_new_product = False
    # Initialize dependency references so except blocks can safely use them.
    # If init fails, fallback via get_*() in except blocks.
    staging_store = None
    batch_store = None

    try:
        shopify = get_shopify_orchestrator()
        staging_store = get_staging_store()
        product_store = get_product_store()
        image_store = get_image_store()
        batch_store = get_batch_store()
        # 1. Get product from staging
        record = run_async(
            staging_store.get_product_staging_by_part_number(part_number, user_id=user_id)
        )
        if not record:
            raise NonRetryableError(f"Product {part_number} not found in staging for user {user_id}")

        # 2. Validate price and inventory
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

        # 3. Validate location mapping
        settings = get_settings()
        location_map = settings.shopify_location_map or {}
        location_summary = record.get("location_summary") or ""
        location_availabilities = record.get("location_availabilities") or []

        parsed_locations = []
        if location_summary:
            for part in location_summary.split(";"):
                part = part.strip()
                if ":" in part:
                    loc_name, qty_str = part.rsplit(":", 1)
                    loc_name = loc_name.strip()
                    try:
                        qty = int(qty_str.strip())
                    except ValueError:
                        qty = 0
                    if loc_name:
                        parsed_locations.append({"location": loc_name, "quantity": qty})

        locations_to_check = location_availabilities if location_availabilities else parsed_locations

        if locations_to_check:
            mapped_locations = []
            skipped_locations = []
            for loc in locations_to_check:
                loc_name = loc.get("location")
                if not loc_name:
                    continue
                if loc_name in location_map:
                    mapped_locations.append(loc)
                else:
                    skipped_locations.append(loc_name)

            if skipped_locations:
                logger.warning(
                    f"Product {part_number} has non-mapped locations that will be skipped: {skipped_locations}. "
                    f"Only publishing to mapped locations: {[l.get('location') for l in mapped_locations]}"
                )

            if not mapped_locations:
                raise NonRetryableError(
                    f"Product {part_number} is only available at non-mapped locations {skipped_locations}. "
                    f"No publishable US inventory. Cannot publish."
                )

            record.setdefault("shopify", {})
            record["shopify"]["location_quantities"] = mapped_locations
            logger.info(f"Location quantities for {part_number}: {len(mapped_locations)} mapped, {len(skipped_locations)} skipped")
        else:
            logger.warning(f"Product {part_number} has no location data. Inventory will use default Shopify location.")

        existing_shopify_id = record.get("shopify_product_id")
        if existing_shopify_id:
            logger.info(f"Product {part_number} already published, will update shopify_id={existing_shopify_id}")

        # 4. Handle image upload
        boeing_image_url = record.get("boeing_image_url") or record.get("boeing_thumbnail_url")
        if boeing_image_url:
            try:
                image_url, image_path = run_async(
                    image_store.upload_image_from_url(boeing_image_url, part_number)
                )
                record["image_url"] = image_url
                record["image_path"] = image_path
                run_async(
                    staging_store.update_product_staging_image(part_number, image_url, image_path)
                )
            except Exception as img_err:
                logger.warning(f"Image upload failed, using placeholder: {img_err}")
                record["image_url"] = FALLBACK_IMAGE_URL
        else:
            record["image_url"] = FALLBACK_IMAGE_URL

        # 5. Prepare Shopify payload
        record = _prepare_shopify_record(record)

        # 6. Publish or update in Shopify (idempotent)
        if existing_shopify_id:
            logger.info(f"Updating existing Shopify product {existing_shopify_id} for {part_number}")
            result = run_async(shopify.update_product(existing_shopify_id, record))
            shopify_product_id = result.get("product", {}).get("id") or existing_shopify_id
            is_new_product = False
        else:
            sku = record.get("sku") or part_number
            found_shopify_id = run_async(shopify.find_product_by_sku(sku))

            if found_shopify_id:
                logger.info(f"Found existing Shopify product by SKU {sku}, updating {found_shopify_id}")
                result = run_async(shopify.update_product(found_shopify_id, record))
                shopify_product_id = result.get("product", {}).get("id") or found_shopify_id
                is_new_product = False
            else:
                logger.info(f"Creating new Shopify product for {part_number}")
                result = run_async(shopify.publish_product(record))
                shopify_product_id = result.get("product", {}).get("id")
                is_new_product = True

        if not shopify_product_id:
            raise ValueError("Shopify did not return product ID")

        # 6.5 Immediately save Shopify ID to staging for traceability.
        # Even if the full DB save (step 7) fails, this ensures we can
        # trace which Shopify products were created and avoid ghost products.
        if is_new_product:
            try:
                run_async(staging_store._update(
                    "product_staging",
                    {"sku": part_number, "user_id": user_id},
                    {"shopify_product_id": str(shopify_product_id)},
                ))
                logger.info(f"Saved Shopify ID {shopify_product_id} to staging for {part_number}")
            except Exception as e:
                logger.warning(f"Could not pre-save Shopify ID to staging for {part_number}: {e}")

        # 7. CRITICAL: Save to database with compensation on failure
        try:
            run_async(
                product_store.upsert_product(record, shopify_product_id=str(shopify_product_id), user_id=user_id)
            )
            run_async(
                staging_store.update_product_staging_shopify_id(part_number, str(shopify_product_id), user_id=user_id)
            )
        except Exception as db_err:
            if is_new_product:
                logger.error(
                    f"DB save failed after Shopify CREATE. "
                    f"Compensating by deleting Shopify product {shopify_product_id}"
                )
                try:
                    run_async(shopify.delete_product(shopify_product_id))
                    logger.info(f"Compensation successful: deleted orphaned Shopify product {shopify_product_id}")
                except Exception as rollback_err:
                    logger.critical(
                        f"ORPHANED PRODUCT: Shopify ID {shopify_product_id} for part {part_number}. "
                        f"DB error: {db_err}, Rollback error: {rollback_err}"
                    )
                    try:
                        run_async(staging_store.update_product_staging_status(part_number, "failed", user_id))
                    except Exception:
                        logger.warning(f"Could not update staging status to 'failed' for {part_number}")
                    batch_store.record_failure(
                        batch_id,
                        part_number,
                        f"ORPHANED: Shopify {shopify_product_id} - needs manual cleanup",
                        stage="publishing"
                    )
            else:
                logger.error(f"DB save failed after Shopify UPDATE for {part_number}: {db_err}")
            raise db_err

        # 8. Create/update sync schedule
        #    assigned_slot (pre-computed by publish_batch) is passed as
        #    hour_bucket so new products land in the planned bucket without
        #    a per-task DB read — avoiding the concurrent-worker race condition.
        #    For existing products upsert_sync_schedule keeps the current bucket.
        try:
            sync_store = get_sync_store()
            initial_price = record.get("list_price") or record.get("net_price")
            initial_quantity = record.get("inventory_quantity")
            full_sku = record.get("sku") or record.get("aviall_part_number") or part_number
            sync_store.upsert_sync_schedule(
                sku=full_sku,
                user_id=user_id,
                initial_price=initial_price,
                initial_quantity=initial_quantity,
                shopify_product_id=str(shopify_product_id),
                hour_bucket=assigned_slot,
            )
            logger.info(f"{'Created' if is_new_product else 'Updated'} sync schedule for {full_sku}")
        except Exception as sync_err:
            logger.warning(f"Failed to upsert sync schedule for {part_number}: {sync_err}")

        # 9. Check batch completion
        # Note: published_count is updated by trg_update_batch_stats trigger
        # when staging_store.update_product_staging_shopify_id sets status='published'
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
        try:
            _ss = staging_store or get_staging_store()
            run_async(_ss.update_product_staging_status(part_number, "blocked", user_id))
        except Exception:
            logger.warning(f"Could not update staging status to 'blocked' for {part_number}")
        try:
            _bs = batch_store or get_batch_store()
            _bs.record_failure(batch_id, part_number, str(e), stage="publishing")
            check_batch_completion.delay(batch_id)
        except Exception as inner:
            logger.critical(f"CANNOT record failure for {part_number} in batch {batch_id}: {inner}")
        raise

    except HTTPException as e:
        error_msg = f"Shopify API error {e.status_code}: {e.detail}"
        logger.error(f"Failed to publish {part_number}: {error_msg}")

        if 400 <= e.status_code < 500:
            try:
                _ss = staging_store or get_staging_store()
                run_async(_ss.update_product_staging_status(part_number, "blocked", user_id))
            except Exception:
                logger.warning(f"Could not update staging status to 'blocked' for {part_number}")
            try:
                _bs = batch_store or get_batch_store()
                _bs.record_failure(batch_id, part_number, error_msg, stage="publishing")
                check_batch_completion.delay(batch_id)
            except Exception as inner:
                logger.critical(f"CANNOT record failure for {part_number} in batch {batch_id}: {inner}")
            raise NonRetryableError(error_msg)
        else:
            if self.request.retries >= self.max_retries:
                try:
                    _ss = staging_store or get_staging_store()
                    run_async(_ss.update_product_staging_status(part_number, "failed", user_id))
                except Exception:
                    logger.warning(f"Could not update staging status to 'failed' for {part_number}")
                try:
                    _bs = batch_store or get_batch_store()
                    _bs.record_failure(batch_id, part_number, error_msg, stage="publishing")
                    check_batch_completion.delay(batch_id)
                except Exception as inner:
                    logger.critical(f"CANNOT record failure for {part_number} in batch {batch_id}: {inner}")
            raise RetryableError(error_msg)

    except Exception as e:
        logger.error(f"Failed to publish {part_number}: {e}")

        # Determine if Celery will retry this task
        is_retryable = isinstance(e, (RetryableError, ConnectionError, TimeoutError, httpx.ConnectError, httpx.ReadTimeout))
        is_last_attempt = self.request.retries >= self.max_retries

        if not is_retryable or is_last_attempt:
            # Non-retryable error OR final retry exhausted — record failure
            # so this part is not silently lost
            try:
                _ss = staging_store or get_staging_store()
                run_async(_ss.update_product_staging_status(part_number, "failed", user_id))
            except Exception:
                logger.warning(f"Could not update staging status to 'failed' for {part_number}")
            try:
                _bs = batch_store or get_batch_store()
                _bs.record_failure(batch_id, part_number, str(e), stage="publishing")
                check_batch_completion.delay(batch_id)
            except Exception as inner:
                logger.critical(f"CANNOT record failure for {part_number} in batch {batch_id}: {inner}")
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
    """
    record.setdefault("shopify", {})

    list_price = record.get("list_price")
    net_price = record.get("net_price")
    base_cost = list_price if list_price is not None else net_price
    shop_price = (base_cost * 1.1) if base_cost is not None else record.get("price")

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
        "estimated_lead_time_days": record.get("estimated_lead_time_days"),
        "trace": record.get("trace"),
        "expiration_date": record.get("expiration_date"),
        "notes": record.get("notes"),
    })

    record["name"] = record.get("boeing_name") or shopify_title
    record["description"] = record.get("boeing_description") or ""

    return record
