"""
Batch completion service — business logic for batch progress tracking.

Encapsulates all completion/reconciliation logic extracted from
celery_app/tasks/batch.py so that task definitions stay thin.

Tasks call these methods and act on the returned flag fields
(trigger_catchup, trigger_completion_check) to queue Celery sub-tasks
without this service ever importing task modules.

Version: 1.0.0
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from app.db.batch_store import BatchStore
from app.db.staging_store import StagingStore
from app.utils.dispatch_lock import get_deferred_buckets

logger = logging.getLogger(__name__)


class BatchCompletionService:
    def __init__(self, batch_store: BatchStore, staging_store: Optional[StagingStore] = None) -> None:
        self._store = batch_store
        self._staging = staging_store

    # ------------------------------------------------------------------
    # Deferred catch-up helper
    # ------------------------------------------------------------------

    def should_trigger_deferred_catchup(self) -> bool:
        """Return True if all batches are done and deferred sync buckets exist.

        Called after a batch transitions to a terminal state. When True,
        the task layer should fire dispatch_deferred_catchup.delay().
        """
        try:
            active_batches = self._store.get_active_batches()
            if active_batches:
                logger.debug(
                    f"Still {len(active_batches)} active batch(es), "
                    f"sync catch-up will wait"
                )
                return False

            deferred = get_deferred_buckets()
            if not deferred:
                return False

            logger.info(
                f"All sessions complete — {len(deferred)} deferred bucket(s) "
                f"ready for catch-up: {sorted(deferred)}"
            )
            return True
        except Exception as e:
            logger.warning(f"Could not check deferred catch-up status (non-fatal): {e}")
            return False

    # ------------------------------------------------------------------
    # check_batch_completion
    # ------------------------------------------------------------------

    def check_completion(self, batch_id: str) -> Dict[str, Any]:
        """
        Determine if a batch has completed its current stage and update status.

        Pipeline stages:
          extract  -> normalize  -> publish  -> completed

        Returns a result dict. If ``trigger_catchup`` is True, the task
        layer should call dispatch_deferred_catchup.delay().
        """
        batch = self._store.get_batch(batch_id)

        if not batch:
            logger.warning(f"Batch {batch_id} not found")
            return {"batch_id": batch_id, "error": "not_found"}

        if batch["status"] in ("completed", "failed", "cancelled"):
            return {
                "batch_id": batch_id,
                "status": batch["status"],
                "already_finalized": True,
            }

        total = batch["total_items"]
        extracted = batch.get("extracted_count", 0)
        normalized = batch["normalized_count"]
        published = batch["published_count"]
        failed = batch["failed_count"]
        batch_type = batch["batch_type"]

        # For publish stage, cross-check counter against actual staging rows
        # to correct race-condition drift from concurrent workers.
        if batch_type == "publish":
            try:
                actual_result = (
                    self._store.client.table("product_staging")
                    .select("*", count="exact")
                    .eq("batch_id", batch_id)
                    .eq("status", "published")
                    .execute()
                )
                actual_published = actual_result.count or 0
                if actual_published != published:
                    logger.warning(
                        f"Batch {batch_id} counter drift: "
                        f"batches.published_count={published}, "
                        f"actual product_staging published={actual_published}. Correcting."
                    )
                    self._store.client.table("batches").update(
                        {"published_count": actual_published}
                    ).eq("id", batch_id).execute()
                    published = actual_published
            except Exception as e:
                logger.warning(
                    f"Could not verify actual published count for batch {batch_id}: {e}"
                )

        logger.debug(
            f"Batch {batch_id} progress: type={batch_type}, total={total}, "
            f"extracted={extracted}, normalized={normalized}, "
            f"published={published}, failed={failed}"
        )

        is_stage_complete = False
        accounted = 0

        if batch_type == "extract":
            accounted = extracted + failed
            if accounted > total:
                logger.error(
                    f"Batch {batch_id} OVERCOUNT: extracted({extracted}) + "
                    f"failed({failed}) = {accounted} > total({total})."
                )
            is_stage_complete = accounted == total

        elif batch_type == "publish":
            accounted = published + failed
            if accounted > total:
                logger.error(
                    f"Batch {batch_id} OVERCOUNT: published({published}) + "
                    f"failed({failed}) = {accounted} > total({total})."
                )
            is_stage_complete = accounted == total

        elif batch_type == "normalize":
            return {
                "batch_id": batch_id,
                "status": "completed",
                "batch_type": "normalize",
                "message": "Normalization complete. Ready for publishing.",
            }

        if is_stage_complete:
            if batch_type == "extract":
                if extracted == 0:
                    self._store.update_status(
                        batch_id, "failed",
                        "All items failed during extraction/normalization"
                    )
                    logger.warning(f"Batch {batch_id} failed (0 items extracted)")
                    return {
                        "batch_id": batch_id,
                        "status": "failed",
                        "batch_type": "extract",
                        "total": total,
                        "succeeded": 0,
                        "failed": failed,
                        "trigger_catchup": self.should_trigger_deferred_catchup(),
                    }

                self._store.update_batch_type(batch_id, "normalize")
                self._store.update_status(batch_id, "completed")
                logger.info(
                    f"Batch {batch_id} normalization complete "
                    f"({extracted}/{total} extracted, {normalized} publishable). "
                    f"Ready for publishing."
                )
                return {
                    "batch_id": batch_id,
                    "status": "completed",
                    "batch_type": "normalize",
                    "total": total,
                    "succeeded": normalized,
                    "failed": failed,
                }

            elif batch_type == "publish":
                succeeded = published

                if failed == total or succeeded == 0:
                    self._store.update_status(
                        batch_id, "failed",
                        f"Publishing failed ({failed} failures)"
                    )
                    logger.warning(
                        f"Batch {batch_id} publishing failed ({failed} failures)"
                    )
                    return {
                        "batch_id": batch_id,
                        "status": "failed",
                        "batch_type": "publish",
                        "total": total,
                        "succeeded": succeeded,
                        "failed": failed,
                        "trigger_catchup": self.should_trigger_deferred_catchup(),
                    }
                elif failed > 0:
                    self._store.update_status(batch_id, "completed")
                    logger.warning(
                        f"Batch {batch_id} publishing completed with {failed} failures"
                    )
                else:
                    self._store.update_status(batch_id, "completed")
                    logger.info(
                        f"Batch {batch_id} publishing completed successfully "
                        f"({succeeded} items)"
                    )

                return {
                    "batch_id": batch_id,
                    "status": "completed",
                    "batch_type": "publish",
                    "total": total,
                    "succeeded": succeeded,
                    "failed": failed,
                    "trigger_catchup": self.should_trigger_deferred_catchup(),
                }

        logger.debug(
            f"Batch {batch_id} still processing: "
            f"{extracted}+{failed}={extracted + failed} < total ({total})"
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
                "failed": failed,
            },
        }

    # ------------------------------------------------------------------
    # reconcile_batch
    # ------------------------------------------------------------------

    def reconcile(self, batch_id: str) -> Dict[str, Any]:
        """
        Safety-net reconciliation — detect and record missing/stuck parts.

        Returns a result dict. If ``trigger_completion_check`` is True,
        the task layer should call check_batch_completion.delay(batch_id).
        """
        batch = self._store.get_batch(batch_id)

        if not batch:
            return {"batch_id": batch_id, "error": "not_found"}

        if batch["status"] in ("completed", "failed", "cancelled"):
            return {
                "batch_id": batch_id,
                "status": batch["status"],
                "already_finalized": True,
            }

        batch_type = batch["batch_type"]
        total = batch["total_items"]
        extracted = batch.get("extracted_count", 0)
        published = batch["published_count"]
        failed = batch["failed_count"]

        if batch_type == "extract":
            accounted = extracted + failed
        elif batch_type == "publish":
            accounted = published + failed
        else:
            return {"batch_id": batch_id, "status": "ok", "batch_type": batch_type}

        if accounted >= total:
            return {
                "batch_id": batch_id,
                "status": "ok",
                "accounted": accounted,
                "total": total,
                "trigger_completion_check": True,
            }

        missing_count = total - accounted
        logger.warning(
            f"Batch {batch_id} STUCK: {accounted}/{total} accounted "
            f"({missing_count} parts missing). Attempting reconciliation."
        )

        all_pns = set(batch.get("part_numbers") or [])
        if batch_type == "publish":
            all_pns = set(batch.get("publish_part_numbers") or []) or all_pns

        failed_pns = set(
            item.get("part_number", "")
            for item in (batch.get("failed_items") or [])
        )

        try:
            result = (
                self._store.client.table("product_staging")
                .select("sku, status")
                .eq("batch_id", batch_id)
                .execute()
            )
            staged_rows = result.data or []
        except Exception as e:
            logger.error(f"Cannot query staging for batch {batch_id}: {e}")
            staged_rows = []

        published_pns: set = set()

        if batch_type == "publish":
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

            stuck_pns = set()
            for row in staged_rows:
                sku = row.get("sku")
                status = row.get("status")
                if (
                    sku and sku in all_pns
                    and status not in ("published", "failed", "blocked")
                    and sku not in failed_pns
                ):
                    stuck_pns.add(sku)

            if stuck_pns:
                logger.warning(
                    f"Batch {batch_id}: {len(stuck_pns)} parts STUCK in staging "
                    f"(publish task lost): {list(stuck_pns)[:20]}"
                    f"{'...' if len(stuck_pns) > 20 else ''}"
                )
                missing_pns = missing_pns | stuck_pns
        else:
            staged_pns = set(row["sku"] for row in staged_rows if row.get("sku"))
            accounted_pns = staged_pns | failed_pns
            missing_pns = all_pns - accounted_pns

        if not missing_pns:
            if batch_type == "publish" and published_pns:
                try:
                    actual_published = len(published_pns)
                    self._store.client.table("batches").update(
                        {"published_count": actual_published}
                    ).eq("id", batch_id).execute()
                    logger.info(
                        f"Batch {batch_id} reconciliation: "
                        f"synced published_count={actual_published}"
                    )
                except Exception as e:
                    logger.warning(
                        f"Could not sync published_count for batch {batch_id}: {e}"
                    )

            logger.info(
                f"Batch {batch_id} reconciliation: all parts accounted for "
                f"(triggering completion check)"
            )
            return {
                "batch_id": batch_id,
                "status": "counter_synced",
                "missing": 0,
                "trigger_completion_check": True,
            }

        logger.warning(
            f"Batch {batch_id}: {len(missing_pns)} parts MISSING/STUCK — "
            f"recording as failed: {list(missing_pns)[:20]}"
            f"{'...' if len(missing_pns) > 20 else ''}"
        )

        stage = "extraction" if batch_type == "extract" else "publishing"
        for pn in missing_pns:
            self._store.record_failure(
                batch_id, pn,
                f"Part lost during {stage} (detected by reconciliation)",
                stage=stage,
            )

        return {
            "batch_id": batch_id,
            "status": "reconciled",
            "missing_parts": len(missing_pns),
            "missing_list": list(missing_pns),
            "trigger_completion_check": True,
        }

    # ------------------------------------------------------------------
    # cancel_batch
    # ------------------------------------------------------------------

    def cancel(self, batch_id: str, celery_app=None) -> Dict[str, Any]:
        """Cancel a batch and optionally revoke its Celery task."""
        batch = self._store.get_batch(batch_id)

        if not batch:
            logger.warning(f"Batch {batch_id} not found for cancellation")
            return {"success": False, "error": "Batch not found"}

        if batch["status"] in ("completed", "failed", "cancelled"):
            logger.info(f"Batch {batch_id} already finalized: {batch['status']}")
            return {"success": False, "error": f"Batch already {batch['status']}"}

        celery_task_id = batch.get("celery_task_id")
        if celery_task_id and celery_app is not None:
            try:
                celery_app.control.revoke(celery_task_id, terminate=True, signal="SIGTERM")
                logger.info(f"Revoked main task {celery_task_id} for batch {batch_id}")
            except Exception as e:
                logger.warning(f"Failed to revoke task {celery_task_id}: {e}")

        self._store.update_status(batch_id, "cancelled", "Cancelled by user request")
        logger.info(f"Batch {batch_id} marked as cancelled")

        return {"success": True, "batch_id": batch_id, "status": "cancelled"}

    # ------------------------------------------------------------------
    # cleanup_stale_batches
    # ------------------------------------------------------------------

    def cleanup_stale(self, max_age_hours: int = 24) -> Dict[str, Any]:
        """Mark stuck batches as failed after timeout."""
        logger.info(f"Cleaning up stale batches older than {max_age_hours} hours")

        active_batches = self._store.get_active_batches()
        cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
        cleaned_count = 0

        for batch in active_batches:
            created_at_str = batch["created_at"]
            if isinstance(created_at_str, str):
                created_at = datetime.fromisoformat(
                    created_at_str.replace("Z", "+00:00")
                )
            else:
                created_at = created_at_str

            if created_at.replace(tzinfo=None) < cutoff:
                self._store.update_status(
                    batch["id"], "failed",
                    f"Timed out after {max_age_hours} hours"
                )
                logger.warning(f"Marked batch {batch['id']} as failed (timed out)")
                cleaned_count += 1

        return {
            "batches_checked": len(active_batches),
            "batches_cleaned": cleaned_count,
        }
