"""
Unit tests for _trigger_deferred_catchup_if_needed in batch.py.

Tests cover:
- Triggers catch-up when all batches done and deferred buckets exist
- Skips catch-up when other batches still active
- Skips catch-up when no deferred buckets
- Fail-safe on exception (non-fatal)

Version: 1.0.0
"""
import pytest
from unittest.mock import patch, MagicMock

BATCH_MODULE = "app.celery_app.tasks.batch"


pytestmark = pytest.mark.unit


class TestTriggerDeferredCatchupIfNeeded:

    @patch(f"{BATCH_MODULE}.get_deferred_buckets", return_value={5, 6})
    def test_triggers_when_no_active_batches_and_deferred_exist(self, mock_deferred):
        from app.celery_app.tasks.batch import _trigger_deferred_catchup_if_needed

        mock_bs = MagicMock()
        mock_bs.get_active_batches.return_value = []

        with patch(
            f"{BATCH_MODULE}.dispatch_deferred_catchup",
            create=True,
        ) as mock_catchup:
            # Need to mock the lazy import inside the function
            with patch(
                "app.celery_app.tasks.sync_dispatch.dispatch_deferred_catchup"
            ) as mock_task:
                result = _trigger_deferred_catchup_if_needed(mock_bs)

        assert result is True

    @patch(f"{BATCH_MODULE}.get_deferred_buckets")
    def test_skips_when_active_batches_exist(self, mock_deferred):
        from app.celery_app.tasks.batch import _trigger_deferred_catchup_if_needed

        mock_bs = MagicMock()
        mock_bs.get_active_batches.return_value = [
            {"id": "batch-002", "status": "processing"},
        ]

        result = _trigger_deferred_catchup_if_needed(mock_bs)

        assert result is False
        mock_deferred.assert_not_called()

    @patch(f"{BATCH_MODULE}.get_deferred_buckets", return_value=set())
    def test_skips_when_no_deferred_buckets(self, mock_deferred):
        from app.celery_app.tasks.batch import _trigger_deferred_catchup_if_needed

        mock_bs = MagicMock()
        mock_bs.get_active_batches.return_value = []

        result = _trigger_deferred_catchup_if_needed(mock_bs)

        assert result is False

    def test_returns_false_on_exception(self):
        from app.celery_app.tasks.batch import _trigger_deferred_catchup_if_needed

        mock_bs = MagicMock()
        mock_bs.get_active_batches.side_effect = Exception("DB error")

        result = _trigger_deferred_catchup_if_needed(mock_bs)

        assert result is False

    @patch(f"{BATCH_MODULE}.get_deferred_buckets", return_value={5})
    def test_calls_dispatch_deferred_catchup_delay(self, mock_deferred):
        from app.celery_app.tasks.batch import _trigger_deferred_catchup_if_needed

        mock_bs = MagicMock()
        mock_bs.get_active_batches.return_value = []

        with patch(
            "app.celery_app.tasks.sync_dispatch.dispatch_deferred_catchup"
        ) as mock_task:
            _trigger_deferred_catchup_if_needed(mock_bs)
            mock_task.delay.assert_called_once()
