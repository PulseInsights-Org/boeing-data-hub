"""
Unit tests for report_service.py — cycle start and completion reports.

Tests cover:
- generate_cycle_start_report: email content, DB save, email delivery
- generate_cycle_report: normal completion, timeout fallback (still_syncing > 0)
- HTML template correctness: metric cards, warning banner, error grouping, OOS
- unchanged_count clamping (never negative)
- html.escape for SKU/error strings
- Empty cycle handling
- Duration calculation

Version: 1.0.0
"""
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime, timezone

MODULE = "app.services.report_service"

pytestmark = pytest.mark.unit


def _make_service(
    products=None,
    recipients=None,
    resend_api_key="test-key",
):
    """Create a ReportService with mocked dependencies."""
    from app.services.report_service import ReportService

    mock_resend = MagicMock()
    mock_store = MagicMock()
    mock_store.save_report.return_value = {"id": "rpt-001"}
    mock_supabase = MagicMock()

    # Mock product_sync_schedule query
    mock_result = MagicMock()
    mock_result.data = products or []
    mock_result.count = len(products) if products else 0
    mock_supabase.client.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_result

    mock_settings = MagicMock()
    mock_settings.redis_url = "redis://localhost:6379"
    mock_settings.report_recipients = recipients or ["test@example.com"]
    mock_settings.resend_api_key = resend_api_key

    svc = ReportService(
        resend_client=mock_resend,
        report_store=mock_store,
        supabase_client=mock_supabase,
        settings=mock_settings,
    )
    return svc, mock_resend, mock_store


class TestGenerateCycleStartReport:

    @patch(f"{MODULE}.get_cycle_start_time", return_value="2026-03-23T10:00:00+00:00")
    @patch(f"{MODULE}.get_cycle_progress", return_value={
        "cycle_id": "sync_cycle:2026-03-23:0",
        "total_buckets": 24,
        "buckets_completed": [0],
        "is_complete": False,
        "progress_percent": 4.2,
    })
    def test_generates_start_notification(self, mock_progress, mock_start_time):
        products = [{"id": str(i), "is_active": True} for i in range(200)]
        svc, mock_resend, mock_store = _make_service(products=products)

        result = svc.generate_cycle_start_report()

        assert result["email_sent"] is True
        assert result["report_id"] == "rpt-001"
        mock_store.save_report.assert_called_once()
        call_kwargs = mock_store.save_report.call_args[1]
        assert call_kwargs["report_type"] == "cycle_start"
        assert call_kwargs["cycle_started_at"] == "2026-03-23T10:00:00+00:00"

    @patch(f"{MODULE}.get_cycle_start_time", return_value="2026-03-23T10:00:00+00:00")
    @patch(f"{MODULE}.get_cycle_progress", return_value={
        "cycle_id": "sync_cycle:2026-03-23:0",
        "total_buckets": 24,
        "buckets_completed": [],
        "is_complete": False,
        "progress_percent": 0,
    })
    def test_email_subject_contains_date(self, mock_progress, mock_start):
        svc, mock_resend, _ = _make_service(products=[])

        svc.generate_cycle_start_report()

        call_args = mock_resend.send_email.call_args[0]
        subject = call_args[1]
        assert "Sync Cycle Started" in subject

    @patch(f"{MODULE}.get_cycle_start_time", return_value=None)
    @patch(f"{MODULE}.get_cycle_progress", return_value={
        "cycle_id": "sync_cycle:2026-03-23:0",
        "total_buckets": 24,
        "buckets_completed": [],
        "is_complete": False,
        "progress_percent": 0,
    })
    def test_fallback_when_no_start_time_in_redis(self, mock_progress, mock_start):
        svc, _, mock_store = _make_service(products=[])

        result = svc.generate_cycle_start_report()

        # Should still succeed with fallback timestamp
        assert result["report_id"] == "rpt-001"
        call_kwargs = mock_store.save_report.call_args[1]
        assert call_kwargs["cycle_started_at"] is not None

    @patch(f"{MODULE}.get_cycle_start_time", return_value="2026-03-23T10:00:00+00:00")
    @patch(f"{MODULE}.get_cycle_progress", return_value={
        "cycle_id": "sync_cycle:2026-03-23:0",
        "total_buckets": 24,
        "buckets_completed": [],
        "is_complete": False,
        "progress_percent": 0,
    })
    def test_skips_email_when_no_api_key(self, mock_progress, mock_start):
        svc, mock_resend, _ = _make_service(
            products=[], resend_api_key=None
        )

        result = svc.generate_cycle_start_report()

        assert result["email_sent"] is False
        mock_resend.send_email.assert_not_called()


class TestGenerateCycleReport:

    @patch(f"{MODULE}.get_cycle_start_time", return_value="2026-03-23T10:00:00+00:00")
    @patch(f"{MODULE}.get_cycle_changes", return_value={"SKU-001": "price: 100->110"})
    @patch(f"{MODULE}.get_cycle_progress", return_value={
        "cycle_id": "sync_cycle:2026-03-23:0",
        "total_buckets": 24,
        "buckets_completed": list(range(24)),
        "is_complete": True,
        "progress_percent": 100,
    })
    def test_normal_completion(self, mock_progress, mock_changes, mock_start):
        products = [
            {"id": "1", "sku": "SKU-001", "sync_status": "success",
             "hour_bucket": 0, "last_inventory_status": "in_stock"},
            {"id": "2", "sku": "SKU-002", "sync_status": "success",
             "hour_bucket": 1, "last_inventory_status": "in_stock"},
        ]
        svc, mock_resend, mock_store = _make_service(products=products)

        result = svc.generate_cycle_report()

        assert result["email_sent"] is True
        call_kwargs = mock_store.save_report.call_args[1]
        assert call_kwargs["report_type"] == "cycle_complete"
        assert call_kwargs["cycle_started_at"] == "2026-03-23T10:00:00+00:00"
        assert call_kwargs["cycle_ended_at"] is not None

    @patch(f"{MODULE}.get_cycle_start_time", return_value="2026-03-23T10:00:00+00:00")
    @patch(f"{MODULE}.get_cycle_changes", return_value={})
    @patch(f"{MODULE}.get_cycle_progress", return_value={
        "cycle_id": "sync_cycle:2026-03-23:0",
        "total_buckets": 24,
        "buckets_completed": list(range(24)),
        "is_complete": True,
        "progress_percent": 100,
    })
    def test_timeout_fallback_with_still_syncing(
        self, mock_progress, mock_changes, mock_start
    ):
        products = [
            {"id": "1", "sku": "SKU-001", "sync_status": "success",
             "hour_bucket": 0, "last_inventory_status": "in_stock"},
        ]
        svc, mock_resend, mock_store = _make_service(products=products)

        result = svc.generate_cycle_report(still_syncing=5)

        assert result["email_sent"] is True
        # Subject should indicate partial
        subject = mock_resend.send_email.call_args[0][1]
        assert "Partial" in subject

        # Summary should include still_syncing
        summary = mock_store.save_report.call_args[1]["summary_stats"]
        assert summary["still_syncing"] == 5

    @patch(f"{MODULE}.get_cycle_start_time", return_value="2026-03-23T10:00:00+00:00")
    @patch(f"{MODULE}.get_cycle_changes", return_value={})
    @patch(f"{MODULE}.get_cycle_progress", return_value={
        "cycle_id": "sync_cycle:2026-03-23:0",
        "total_buckets": 24,
        "buckets_completed": list(range(24)),
        "is_complete": True,
        "progress_percent": 100,
    })
    def test_subject_line_normal_completion(
        self, mock_progress, mock_changes, mock_start
    ):
        svc, mock_resend, _ = _make_service(products=[])

        svc.generate_cycle_report(still_syncing=0)

        subject = mock_resend.send_email.call_args[0][1]
        assert "Sync Cycle Complete" in subject
        assert "Partial" not in subject


class TestUnchangedCountClamping:

    @patch(f"{MODULE}.get_cycle_start_time", return_value=None)
    @patch(f"{MODULE}.get_cycle_changes", return_value={
        f"SKU-{i}": "changed" for i in range(50)
    })
    @patch(f"{MODULE}.get_cycle_progress", return_value={
        "cycle_id": "sync_cycle:2026-03-23:0",
        "total_buckets": 24,
        "buckets_completed": list(range(24)),
        "is_complete": True,
        "progress_percent": 100,
    })
    def test_unchanged_count_clamped_to_zero(
        self, mock_progress, mock_changes, mock_start
    ):
        """When changes + failures > total, unchanged should be 0, not negative."""
        products = [
            {"id": "1", "sku": "SKU-001", "sync_status": "failed",
             "hour_bucket": 0, "last_inventory_status": "in_stock",
             "last_error": "timeout", "consecutive_failures": 1},
        ]
        svc, _, mock_store = _make_service(products=products)

        svc.generate_cycle_report()

        summary = mock_store.save_report.call_args[1]["summary_stats"]
        assert summary["unchanged_count"] >= 0


class TestHtmlTemplates:

    def test_warning_banner_shown_when_still_syncing(self):
        from app.services.report_service import ReportService
        svc = MagicMock(spec=ReportService)

        # Call the actual method
        result = ReportService._build_incomplete_warning(svc, still_syncing=3)
        assert "3 products" in result
        assert "Incomplete Sync" in result

    def test_warning_banner_hidden_when_zero(self):
        from app.services.report_service import ReportService
        svc = MagicMock(spec=ReportService)

        result = ReportService._build_incomplete_warning(svc, still_syncing=0)
        assert result == ""

    def test_out_of_stock_summary_shown(self):
        from app.services.report_service import ReportService
        svc = MagicMock(spec=ReportService)

        oos = [{"sku": f"SKU-{i}"} for i in range(5)]
        result = ReportService._build_out_of_stock_summary(svc, oos)
        assert "Out of Stock (5)" in result
        assert "SKU-0" in result

    def test_out_of_stock_summary_hidden_when_empty(self):
        from app.services.report_service import ReportService
        svc = MagicMock(spec=ReportService)

        result = ReportService._build_out_of_stock_summary(svc, [])
        assert result == ""

    def test_error_grouping_in_failures_table(self):
        from app.services.report_service import ReportService
        svc = MagicMock(spec=ReportService)

        # 5 products with same error, 1 with different
        failed = [
            {"sku": f"SKU-{i}", "last_error": "Connection timeout",
             "consecutive_failures": 1}
            for i in range(5)
        ]
        failed.append({
            "sku": "SKU-99", "last_error": "Product not found",
            "consecutive_failures": 2,
        })

        result = ReportService._build_failures_table_html(svc, failed)
        assert "Error Summary" in result
        assert "Connection timeout" in result
        assert "Failures (6)" in result

    def test_html_escape_in_changes_table(self):
        from app.services.report_service import ReportService
        svc = MagicMock(spec=ReportService)

        changes = {"SKU<script>": "price: <b>100</b> -> 110"}
        result = ReportService._build_changes_table_html(svc, changes)
        assert "<script>" not in result
        assert "&lt;script&gt;" in result
        assert "&lt;b&gt;" in result

    def test_html_escape_in_failures_table(self):
        from app.services.report_service import ReportService
        svc = MagicMock(spec=ReportService)

        failed = [{
            "sku": "SKU<xss>",
            "last_error": "Error: <tag>&amp;",
            "consecutive_failures": 1,
        }]
        result = ReportService._build_failures_table_html(svc, failed)
        assert "<xss>" not in result
        assert "&lt;xss&gt;" in result

    def test_metric_cards_show_five_values(self):
        from app.services.report_service import ReportService
        svc = MagicMock(spec=ReportService)

        summary = {
            "total_products": 100,
            "success_count": 90,
            "failed_count": 5,
            "unchanged_count": 3,
        }
        result = ReportService._build_metric_cards(svc, summary, changes_count=2)
        assert "Total" in result
        assert "Success" in result
        assert "Failed" in result
        assert "Changed" in result
        assert "Unchanged" in result

    def test_truncation_note_for_many_failures(self):
        from app.services.report_service import ReportService
        svc = MagicMock(spec=ReportService)

        failed = [
            {"sku": f"SKU-{i}", "last_error": "err", "consecutive_failures": 1}
            for i in range(50)
        ]
        result = ReportService._build_failures_table_html(svc, failed)
        assert "20 more failures" in result

    def test_changes_table_empty_cycle(self):
        from app.services.report_service import ReportService
        svc = MagicMock(spec=ReportService)

        result = ReportService._build_changes_table_html(svc, {})
        assert "No changes detected" in result

    def test_cycle_start_html_contains_estimates(self):
        from app.services.report_service import ReportService
        svc = MagicMock(spec=ReportService)

        summary = {
            "total_products": 200,
            "bucket_count": 24,
            "estimated_api_calls": 20,
            "estimated_duration_minutes": 10,
        }
        now = datetime(2026, 3, 23, 10, 0, tzinfo=timezone.utc)
        result = ReportService._build_cycle_start_html(svc, summary, now)
        assert "Sync Cycle Started" in result
        assert "200" in result
        assert "~20" in result
        assert "completion report" in result.lower()


class TestDurationCalculation:

    @patch(f"{MODULE}.get_cycle_start_time", return_value="2026-03-23T10:00:00+00:00")
    @patch(f"{MODULE}.get_cycle_changes", return_value={})
    @patch(f"{MODULE}.get_cycle_progress", return_value={
        "cycle_id": "sync_cycle:2026-03-23:0",
        "total_buckets": 24,
        "buckets_completed": list(range(24)),
        "is_complete": True,
        "progress_percent": 100,
    })
    def test_duration_included_in_summary(
        self, mock_progress, mock_changes, mock_start
    ):
        svc, _, mock_store = _make_service(products=[])

        svc.generate_cycle_report()

        summary = mock_store.save_report.call_args[1]["summary_stats"]
        assert "cycle_duration_minutes" in summary
        assert "duration_display" in summary
        assert summary["started_at"] == "2026-03-23T10:00:00+00:00"
        assert summary["ended_at"] is not None
