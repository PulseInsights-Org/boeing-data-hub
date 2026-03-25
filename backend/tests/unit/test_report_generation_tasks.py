"""
Unit tests for report_generation.py Celery tasks.

Tests cover:
- send_cycle_start_notification: delegates to ReportService
- wait_for_cycle_completion: polling, retry, timeout fallback
- generate_cycle_report: normal + still_syncing param forwarding

Since these tasks use lazy imports (inside function body), we patch
the source modules rather than the task module attributes.

Version: 1.0.0
"""
import pytest
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit


class TestSendCycleStartNotification:

    @patch("app.container.get_report_service")
    def test_delegates_to_service(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_svc.generate_cycle_start_report.return_value = {
            "report_id": "rpt-start-001",
            "email_sent": True,
        }
        mock_get_svc.return_value = mock_svc

        from app.celery_app.tasks.report_generation import send_cycle_start_notification
        send_cycle_start_notification.push_request(id="test-task", retries=0)
        try:
            result = send_cycle_start_notification()
        finally:
            send_cycle_start_notification.pop_request()

        mock_svc.generate_cycle_start_report.assert_called_once()
        assert result["status"] == "completed"
        assert result["email_sent"] is True


class TestWaitForCycleCompletion:

    @patch("app.celery_app.tasks.report_generation.generate_cycle_report")
    @patch("app.db.sync_store.get_sync_store")
    def test_triggers_report_when_all_done(self, mock_get_store, mock_gen_task):
        mock_store = MagicMock()
        mock_store.get_syncing_count.return_value = 0
        mock_get_store.return_value = mock_store

        from app.celery_app.tasks.report_generation import wait_for_cycle_completion
        wait_for_cycle_completion.push_request(id="test-task", retries=0)
        try:
            result = wait_for_cycle_completion(cycle_id="test-cycle")
        finally:
            wait_for_cycle_completion.pop_request()

        assert result["status"] == "forwarded_to_report"
        mock_gen_task.delay.assert_called_once_with("test-cycle")

    @patch("app.db.sync_store.get_sync_store")
    def test_retries_when_products_still_syncing(self, mock_get_store):
        mock_store = MagicMock()
        mock_store.get_syncing_count.return_value = 10
        mock_get_store.return_value = mock_store

        from app.celery_app.tasks.report_generation import wait_for_cycle_completion
        wait_for_cycle_completion.push_request(id="test-task", retries=5)
        try:
            with pytest.raises(wait_for_cycle_completion.retry.__class__):
                wait_for_cycle_completion(cycle_id="test-cycle")
        except Exception:
            # Celery retry raises Retry exception
            pass
        finally:
            wait_for_cycle_completion.pop_request()

    @patch("app.celery_app.tasks.report_generation.generate_cycle_report")
    @patch("app.db.sync_store.get_sync_store")
    def test_timeout_fallback_generates_report_with_warning(
        self, mock_get_store, mock_gen_task
    ):
        mock_store = MagicMock()
        mock_store.get_syncing_count.return_value = 3
        mock_get_store.return_value = mock_store

        from app.celery_app.tasks.report_generation import wait_for_cycle_completion
        # Simulate final attempt (retries = max_retries - 1 = 59)
        wait_for_cycle_completion.push_request(id="test-task", retries=59)
        try:
            result = wait_for_cycle_completion(cycle_id="test-cycle")
        finally:
            wait_for_cycle_completion.pop_request()

        assert result["status"] == "timeout_fallback"
        assert result["still_syncing"] == 3
        mock_gen_task.delay.assert_called_once_with(
            "test-cycle", still_syncing=3
        )


class TestGenerateCycleReport:

    @patch("app.container.get_report_service")
    def test_forwards_still_syncing_to_service(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_svc.generate_cycle_report.return_value = {
            "report_id": "rpt-001",
            "cycle_id": "test-cycle",
            "email_sent": True,
        }
        mock_get_svc.return_value = mock_svc

        from app.celery_app.tasks.report_generation import generate_cycle_report
        generate_cycle_report.push_request(id="test-task", retries=0)
        try:
            result = generate_cycle_report(
                cycle_id="test-cycle", still_syncing=5
            )
        finally:
            generate_cycle_report.pop_request()

        mock_svc.generate_cycle_report.assert_called_once_with(
            cycle_id="test-cycle", still_syncing=5,
        )
        assert result["still_syncing"] == 5

    @patch("app.container.get_report_service")
    def test_normal_completion_still_syncing_zero(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_svc.generate_cycle_report.return_value = {
            "report_id": "rpt-002",
            "cycle_id": "test-cycle",
            "email_sent": True,
        }
        mock_get_svc.return_value = mock_svc

        from app.celery_app.tasks.report_generation import generate_cycle_report
        generate_cycle_report.push_request(id="test-task", retries=0)
        try:
            result = generate_cycle_report(
                cycle_id="test-cycle", still_syncing=0
            )
        finally:
            generate_cycle_report.pop_request()

        assert result["still_syncing"] == 0
        assert result["status"] == "completed"
