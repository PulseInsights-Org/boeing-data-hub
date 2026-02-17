"""
Integration tests for Celery task registration and configuration.

Verifies all 12 tasks are registered with correct names in the Celery app,
task function signatures exist, and queue routing is properly configured.
Version: 1.0.0
"""
import os
import pytest

os.environ.setdefault("AUTO_START_CELERY", "false")


# The 12 expected task names from the codebase
EXPECTED_TASK_NAMES = [
    # Extraction tasks (2)
    "tasks.extraction.process_bulk_search",
    "tasks.extraction.extract_chunk",
    # Normalization tasks (1)
    "tasks.normalization.normalize_chunk",
    # Publishing tasks (2)
    "tasks.publishing.publish_batch",
    "tasks.publishing.publish_product",
    # Batch tasks (3)
    "tasks.batch.check_batch_completion",
    "tasks.batch.cancel_batch",
    "tasks.batch.cleanup_stale_batches",
    # Sync dispatch tasks (3)
    "tasks.sync_dispatch.dispatch_hourly",
    "tasks.sync_dispatch.dispatch_retry",
    "tasks.sync_dispatch.end_of_day_cleanup",
    # Sync boeing tasks (1)
    "tasks.sync_boeing.process_boeing_batch",
    # Sync shopify tasks (2)
    "tasks.sync_shopify.update_shopify_product",
    "tasks.sync_shopify.sync_single_product_immediate",
]


@pytest.fixture(scope="module")
def celery_tasks():
    """Load the Celery app, force-import task modules, and return registered tasks."""
    from app.celery_app.celery_config import celery_app
    # Force-import all task modules to trigger @celery_app.task registration.
    # Celery's `include` only auto-imports when a worker boots; tests must do it explicitly.
    celery_app.loader.import_default_modules()
    return celery_app.tasks


@pytest.fixture(scope="module")
def celery_conf():
    """Load the Celery app and return its conf."""
    from app.celery_app.celery_config import celery_app
    return celery_app.conf


@pytest.mark.integration
class TestCeleryTaskRegistration:
    """Tests for Celery task registration."""

    def test_all_expected_tasks_registered(self, celery_tasks):
        """All 14 expected task names should be present in celery_app.tasks."""
        for task_name in EXPECTED_TASK_NAMES:
            assert task_name in celery_tasks, (
                f"Task '{task_name}' not found in registered tasks. "
                f"Available custom tasks: {[t for t in celery_tasks if not t.startswith('celery.')]}"
            )

    def test_extraction_task_count(self, celery_tasks):
        """Extraction module should have 2 tasks."""
        extraction_tasks = [
            name for name in celery_tasks if name.startswith("tasks.extraction.")
        ]
        assert len(extraction_tasks) == 2

    def test_normalization_task_count(self, celery_tasks):
        """Normalization module should have 1 task."""
        normalization_tasks = [
            name for name in celery_tasks if name.startswith("tasks.normalization.")
        ]
        assert len(normalization_tasks) == 1

    def test_publishing_task_count(self, celery_tasks):
        """Publishing module should have 2 tasks."""
        publishing_tasks = [
            name for name in celery_tasks if name.startswith("tasks.publishing.")
        ]
        assert len(publishing_tasks) == 2

    def test_batch_task_count(self, celery_tasks):
        """Batch module should have 3 tasks."""
        batch_tasks = [
            name for name in celery_tasks if name.startswith("tasks.batch.")
        ]
        assert len(batch_tasks) == 3

    def test_sync_dispatch_task_count(self, celery_tasks):
        """Sync dispatch module should have 3 tasks."""
        dispatch_tasks = [
            name for name in celery_tasks if name.startswith("tasks.sync_dispatch.")
        ]
        assert len(dispatch_tasks) == 3

    def test_sync_boeing_task_count(self, celery_tasks):
        """Sync boeing module should have 1 task."""
        boeing_tasks = [
            name for name in celery_tasks if name.startswith("tasks.sync_boeing.")
        ]
        assert len(boeing_tasks) == 1

    def test_sync_shopify_task_count(self, celery_tasks):
        """Sync shopify module should have 2 tasks."""
        shopify_tasks = [
            name for name in celery_tasks if name.startswith("tasks.sync_shopify.")
        ]
        assert len(shopify_tasks) == 2

    def test_total_custom_task_count(self, celery_tasks):
        """There should be exactly 14 custom tasks (not counting celery builtins)."""
        custom_tasks = [
            name for name in celery_tasks if not name.startswith("celery.")
        ]
        assert len(custom_tasks) == len(EXPECTED_TASK_NAMES), (
            f"Expected {len(EXPECTED_TASK_NAMES)} custom tasks, "
            f"found {len(custom_tasks)}: {custom_tasks}"
        )


@pytest.mark.integration
class TestCeleryTaskFunctions:
    """Tests for Celery task function imports and signatures."""

    def test_extraction_task_functions_importable(self):
        """Extraction task functions should be importable."""
        from app.celery_app.tasks.extraction import process_bulk_search, extract_chunk
        assert callable(process_bulk_search)
        assert callable(extract_chunk)

    def test_normalization_task_function_importable(self):
        """Normalization task function should be importable."""
        from app.celery_app.tasks.normalization import normalize_chunk
        assert callable(normalize_chunk)

    def test_publishing_task_functions_importable(self):
        """Publishing task functions should be importable."""
        from app.celery_app.tasks.publishing import publish_batch, publish_product
        assert callable(publish_batch)
        assert callable(publish_product)

    def test_batch_task_functions_importable(self):
        """Batch task functions should be importable."""
        from app.celery_app.tasks.batch import (
            check_batch_completion,
            cancel_batch,
            cleanup_stale_batches,
        )
        assert callable(check_batch_completion)
        assert callable(cancel_batch)
        assert callable(cleanup_stale_batches)

    def test_sync_dispatch_task_functions_importable(self):
        """Sync dispatch task functions should be importable."""
        from app.celery_app.tasks.sync_dispatch import (
            dispatch_hourly,
            dispatch_retry,
            end_of_day_cleanup,
        )
        assert callable(dispatch_hourly)
        assert callable(dispatch_retry)
        assert callable(end_of_day_cleanup)

    def test_sync_boeing_task_function_importable(self):
        """Sync boeing task function should be importable."""
        from app.celery_app.tasks.sync_boeing import process_boeing_batch
        assert callable(process_boeing_batch)

    def test_sync_shopify_task_functions_importable(self):
        """Sync shopify task functions should be importable."""
        from app.celery_app.tasks.sync_shopify import (
            update_shopify_product,
            sync_single_product_immediate,
        )
        assert callable(update_shopify_product)
        assert callable(sync_single_product_immediate)


@pytest.mark.integration
class TestCeleryConfiguration:
    """Tests for Celery app configuration."""

    def test_celery_app_name(self):
        """Celery app should be named 'boeing_data_hub'."""
        from app.celery_app.celery_config import celery_app
        assert celery_app.main == "boeing_data_hub"

    def test_celery_has_six_queues(self, celery_conf):
        """Celery should have 6 queues configured."""
        queues = celery_conf.task_queues
        queue_names = [q.name for q in queues]
        expected_queues = [
            "extraction", "normalization", "publishing",
            "default", "sync_boeing", "sync_shopify",
        ]
        for q in expected_queues:
            assert q in queue_names, f"Queue '{q}' not found in {queue_names}"

    def test_celery_beat_schedule_has_three_entries(self, celery_conf):
        """Celery Beat should have 3 scheduled tasks."""
        beat_schedule = celery_conf.beat_schedule
        expected_schedules = [
            "dispatch-hourly-sync",
            "dispatch-retry-sync",
            "end-of-day-cleanup",
        ]
        for schedule_name in expected_schedules:
            assert schedule_name in beat_schedule, (
                f"Beat schedule '{schedule_name}' not found"
            )

    def test_celery_serialization_is_json(self, celery_conf):
        """Celery should use JSON serialization."""
        assert celery_conf.task_serializer == "json"
        assert celery_conf.result_serializer == "json"
