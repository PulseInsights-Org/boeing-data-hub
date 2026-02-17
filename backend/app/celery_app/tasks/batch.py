"""
Batch completion check task — monitors batch progress.

Batch orchestration tasks.

Tasks:
- check_batch_completion: Check if a batch has completed
- reconcile_batch: Safety net — detect and record missing parts
- cancel_batch: Cancel a batch and revoke tasks
- cleanup_stale_batches: Mark stuck batches as failed
Version: 1.0.0
"""
import logging
from datetime import datetime, timedelta, timezone

from app.celery_app.celery_config import celery_app
from app.celery_app.tasks.base import BaseTask, get_batch_store

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.batch.check_batch_completion"
)
def check_batch_completion(self, batch_id: str):
    """
    Check if a batch has completed its current stage and update status.

    Pipeline stages (single batch progresses through all):
    1. "extract" -> extraction & normalization in progress
    2. "normalize" -> normalization complete, ready for publishing
    3. "publish" -> publishing in progress
    4. "completed" -> all publishing done
    """
    logger.info(f"Checking completion for batch {batch_id}")

    batch_store = get_batch_store()
    batch = batch_store.get_batch(batch_id)

    if not batch:
        logger.warning(f"Batch {batch_id} not found")
        return {"batch_id": batch_id, "error": "not_found"}

    if batch["status"] in ("completed", "failed", "cancelled"):
        return {
            "batch_id": batch_id,
            "status": batch["status"],
            "already_finalized": True
        }

    total = batch["total_items"]
    extracted = batch.get("extracted_count", 0)
    normalized = batch["normalized_count"]
    published = batch["published_count"]
    failed = batch["failed_count"]
    batch_type = batch["batch_type"]

    # For publish stage, query product_staging for the real published count
    # instead of trusting the batches counter which can be stale due to
    # DB trigger race conditions under concurrent workers.
    if batch_type == "publish":
        try:
            actual_result = (
                batch_store.client.table("product_staging")
                .select("*", count="exact")
                .eq("batch_id", batch_id)
                .eq("status", "published")
                .execute()
            )
            actual_published = actual_result.count or 0
            if actual_published != published:
                logger.warning(
                    f"Batch {batch_id} counter drift: batches.published_count={published}, "
                    f"actual product_staging published={actual_published}. Correcting."
                )
                batch_store.client.table("batches").update(
                    {"published_count": actual_published}
                ).eq("id", batch_id).execute()
                published = actual_published
        except Exception as e:
            logger.warning(f"Could not verify actual published count for batch {batch_id}: {e}")

    logger.debug(
        f"Batch {batch_id} progress: type={batch_type}, total={total}, "
        f"extracted={extracted}, normalized={normalized}, published={published}, failed={failed}"
    )

    is_stage_complete = False
    accounted = 0

    if batch_type == "extract":
        # Use extracted_count (all staging rows) + failed_count (parts with no staging row).
        # Blocked products are in staging (counted in extracted), NOT in failed_count.
        # This avoids double-counting blocked products.
        accounted = extracted + failed
        if accounted > total:
            logger.error(
                f"Batch {batch_id} OVERCOUNT: extracted({extracted}) + failed({failed}) "
                f"= {accounted} > total({total}). Possible double-counting bug."
            )
        is_stage_complete = accounted == total

    elif batch_type == "publish":
        accounted = published + failed
        if accounted > total:
            logger.error(
                f"Batch {batch_id} OVERCOUNT: published({published}) + failed({failed}) "
                f"= {accounted} > total({total}). Possible double-counting bug."
            )
        is_stage_complete = accounted == total
    elif batch_type == "normalize":
        return {
            "batch_id": batch_id,
            "status": "completed",
            "batch_type": "normalize",
            "message": "Normalization complete. Ready for publishing."
        }

    if is_stage_complete:
        if batch_type == "extract":
            if extracted == 0:
                # Nothing was extracted at all — true failure
                batch_store.update_status(batch_id, "failed", "All items failed during extraction/normalization")
                logger.warning(f"Batch {batch_id} failed (0 items extracted)")
                return {
                    "batch_id": batch_id,
                    "status": "failed",
                    "batch_type": "extract",
                    "total": total,
                    "succeeded": 0,
                    "failed": failed
                }

            batch_store.update_batch_type(batch_id, "normalize")
            batch_store.update_status(batch_id, "completed")
            # Status set to "completed" for this phase. When publishing starts,
            # start_bulk_publish() resets status back to "processing".
            logger.info(f"Batch {batch_id} normalization complete ({extracted}/{total} items extracted, {normalized} publishable). Ready for publishing.")

            return {
                "batch_id": batch_id,
                "status": "completed",
                "batch_type": "normalize",
                "total": total,
                "succeeded": normalized,
                "failed": failed
            }

        elif batch_type == "publish":
            succeeded = published

            if failed == total or succeeded == 0:
                batch_store.update_status(batch_id, "failed", f"Publishing failed ({failed} failures)")
                logger.warning(f"Batch {batch_id} publishing failed ({failed} failures)")
                return {
                    "batch_id": batch_id,
                    "status": "failed",
                    "batch_type": "publish",
                    "total": total,
                    "succeeded": succeeded,
                    "failed": failed
                }
            elif failed > 0:
                batch_store.update_status(batch_id, "completed")
                logger.warning(f"Batch {batch_id} publishing completed with {failed} failures")
            else:
                batch_store.update_status(batch_id, "completed")
                logger.info(f"Batch {batch_id} publishing completed successfully ({succeeded} items)")

            return {
                "batch_id": batch_id,
                "status": "completed",
                "batch_type": "publish",
                "total": total,
                "succeeded": succeeded,
                "failed": failed
            }

    logger.debug(
        f"Batch {batch_id} still processing: "
        f"extracted+failed ({extracted}+{failed}={extracted+failed}) < total ({total})"
    )

    return {
        "batch_id": batch_id,
        "status": "processing",
        "batch_type": batch_type,
        "progress": {
            "total": total,
            "extracted": extracted,
            "normalized": normalized,
            "published": published,
            "failed": failed
        }
    }


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.batch.reconcile_batch"
)
def reconcile_batch(self, batch_id: str):
    """
    Safety-net reconciliation — detect and record missing parts.

    Scheduled by orchestrators with a countdown delay. If the batch is
    still stuck (processing with accounted < total), this task:
    1. Queries product_staging to find which parts made it through
    2. Cross-references with batch.part_numbers to identify missing ones
    3. Records missing parts as failed so the batch can complete
    """
    logger.info(f"Reconciling batch {batch_id}")

    batch_store = get_batch_store()
    batch = batch_store.get_batch(batch_id)

    if not batch:
        return {"batch_id": batch_id, "error": "not_found"}

    if batch["status"] in ("completed", "failed", "cancelled"):
        return {"batch_id": batch_id, "status": batch["status"], "already_finalized": True}

    batch_type = batch["batch_type"]
    total = batch["total_items"]
    extracted = batch.get("extracted_count", 0)
    published = batch["published_count"]
    failed = batch["failed_count"]

    # Determine if batch is stuck
    if batch_type == "extract":
        accounted = extracted + failed
    elif batch_type == "publish":
        accounted = published + failed
    else:
        # normalize type — nothing to reconcile
        return {"batch_id": batch_id, "status": "ok", "batch_type": batch_type}

    if accounted >= total:
        # Batch is complete or will be completed by check_batch_completion
        check_batch_completion.delay(batch_id)
        return {"batch_id": batch_id, "status": "ok", "accounted": accounted, "total": total}

    missing_count = total - accounted
    logger.warning(
        f"Batch {batch_id} STUCK: {accounted}/{total} accounted "
        f"({missing_count} parts missing). Attempting reconciliation."
    )

    # Find which parts are accounted for
    all_pns = set(batch.get("part_numbers") or [])
    if batch_type == "publish":
        all_pns = set(batch.get("publish_part_numbers") or []) or all_pns

    failed_pns = set(
        item.get("part_number", "")
        for item in (batch.get("failed_items") or [])
    )

    # Query staging to find parts and their statuses.
    # CRITICAL: For publish reconciliation we must check the actual STATUS
    # of each part, not just whether it exists in staging. Parts are inserted
    # into staging during normalization (before publishing), so a part whose
    # publish task was lost will still exist in staging with status='fetched'.
    try:
        result = (
            batch_store.client.table("product_staging")
            .select("sku, status")
            .eq("batch_id", batch_id)
            .execute()
        )
        staged_rows = result.data or []
    except Exception as e:
        logger.error(f"Cannot query staging for batch {batch_id}: {e}")
        staged_rows = []

    if batch_type == "publish":
        # For publishing, only parts with status='published' are truly done.
        # Parts with status='failed' in staging are also accounted for.
        # Everything else (fetched, normalized, blocked) had its task lost.
        published_pns = set(
            row["sku"] for row in staged_rows
            if row.get("sku") and row.get("status") == "published"
        )
        staging_failed_pns = set(
            row["sku"] for row in staged_rows
            if row.get("sku") and row.get("status") == "failed"
        )
        accounted_pns = published_pns | staging_failed_pns | failed_pns
        missing_pns = all_pns - accounted_pns

        # Also detect parts stuck in non-terminal status (fetched/normalized)
        # whose publish task was silently lost
        stuck_pns = set()
        for row in staged_rows:
            sku = row.get("sku")
            status = row.get("status")
            if (sku and sku in all_pns and
                    status not in ("published", "failed", "blocked") and
                    sku not in failed_pns):
                stuck_pns.add(sku)

        if stuck_pns:
            logger.warning(
                f"Batch {batch_id}: {len(stuck_pns)} parts STUCK in staging "
                f"(publish task lost): {list(stuck_pns)[:20]}"
                f"{'...' if len(stuck_pns) > 20 else ''}"
            )
            missing_pns = missing_pns | stuck_pns
    else:
        # For extraction: parts not in staging at all are missing
        staged_pns = set(row["sku"] for row in staged_rows if row.get("sku"))
        accounted_pns = staged_pns | failed_pns
        missing_pns = all_pns - accounted_pns

    if not missing_pns:
        # All parts are in a terminal state — force a recount to fix any
        # counter drift from DB trigger race conditions
        if batch_type == "publish":
            try:
                actual_published = len(published_pns)
                batch_store.client.table("batches").update(
                    {"published_count": actual_published}
                ).eq("id", batch_id).execute()
                logger.info(
                    f"Batch {batch_id} reconciliation: synced published_count={actual_published}"
                )
            except Exception as e:
                logger.warning(f"Could not sync published_count for batch {batch_id}: {e}")

        logger.info(
            f"Batch {batch_id} reconciliation: all parts accounted for in staging/failures "
            f"(triggering completion check)"
        )
        check_batch_completion.delay(batch_id)
        return {"batch_id": batch_id, "status": "counter_synced", "missing": 0}

    logger.warning(
        f"Batch {batch_id}: {len(missing_pns)} parts MISSING/STUCK — recording as failed: "
        f"{list(missing_pns)[:20]}{'...' if len(missing_pns) > 20 else ''}"
    )

    stage = "extraction" if batch_type == "extract" else "publishing"
    for pn in missing_pns:
        batch_store.record_failure(
            batch_id, pn,
            f"Part lost during {stage} (detected by reconciliation)",
            stage=stage,
        )

    check_batch_completion.delay(batch_id)

    return {
        "batch_id": batch_id,
        "status": "reconciled",
        "missing_parts": len(missing_pns),
        "missing_list": list(missing_pns),
    }


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.batch.cancel_batch"
)
def cancel_batch(self, batch_id: str):
    """Cancel a batch and revoke all in-flight Celery tasks."""
    logger.info(f"Cancelling batch {batch_id}")

    batch_store = get_batch_store()
    batch = batch_store.get_batch(batch_id)

    if not batch:
        logger.warning(f"Batch {batch_id} not found for cancellation")
        return {"success": False, "error": "Batch not found"}

    if batch["status"] in ("completed", "failed", "cancelled"):
        logger.info(f"Batch {batch_id} already finalized: {batch['status']}")
        return {"success": False, "error": f"Batch already {batch['status']}"}

    celery_task_id = batch.get("celery_task_id")
    if celery_task_id:
        try:
            celery_app.control.revoke(celery_task_id, terminate=True, signal='SIGTERM')
            logger.info(f"Revoked main task {celery_task_id} for batch {batch_id}")
        except Exception as e:
            logger.warning(f"Failed to revoke task {celery_task_id}: {e}")

    batch_store.update_status(batch_id, "cancelled", "Cancelled by user request")
    logger.info(f"Batch {batch_id} marked as cancelled")

    return {
        "success": True,
        "batch_id": batch_id,
        "status": "cancelled"
    }


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="tasks.batch.cleanup_stale_batches"
)
def cleanup_stale_batches(self, max_age_hours: int = 24):
    """Mark stuck batches as failed after timeout."""
    logger.info(f"Cleaning up stale batches older than {max_age_hours} hours")

    batch_store = get_batch_store()
    active_batches = batch_store.get_active_batches()
    cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)

    cleaned_count = 0

    for batch in active_batches:
        created_at_str = batch["created_at"]
        if isinstance(created_at_str, str):
            created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        else:
            created_at = created_at_str

        if created_at.replace(tzinfo=None) < cutoff:
            batch_store.update_status(
                batch["id"],
                "failed",
                f"Timed out after {max_age_hours} hours"
            )
            logger.warning(f"Marked batch {batch['id']} as failed (timed out)")
            cleaned_count += 1

    return {
        "batches_checked": len(active_batches),
        "batches_cleaned": cleaned_count
    }
