"""
Unit tests for report_store.py — new fields and methods.

Tests cover:
- save_report with report_type, cycle_started_at, cycle_ended_at
- update_email_status
- get_latest_report with report_type filter
- Backward compatibility (default report_type)

Version: 1.0.0
"""
import pytest
from unittest.mock import patch, MagicMock

MODULE = "app.db.report_store"

pytestmark = pytest.mark.unit


def _make_store():
    """Create a ReportStore with mocked Supabase client."""
    from app.db.report_store import ReportStore

    mock_client = MagicMock()
    mock_supabase = MagicMock()
    mock_supabase.client = mock_client

    store = ReportStore(supabase_client=mock_supabase)
    return store, mock_client


class TestSaveReport:

    def test_saves_with_new_fields(self):
        store, mock_client = _make_store()

        mock_result = MagicMock()
        mock_result.data = [{"id": "rpt-001"}]
        mock_client.table.return_value.insert.return_value.execute.return_value = mock_result

        result = store.save_report(
            cycle_id="sync_cycle:2026-03-23:0",
            report_text="<html>...</html>",
            summary_stats={"total_products": 100},
            report_type="cycle_complete",
            cycle_started_at="2026-03-23T10:00:00+00:00",
            cycle_ended_at="2026-03-23T11:30:00+00:00",
        )

        assert result["id"] == "rpt-001"
        insert_data = mock_client.table.return_value.insert.call_args[0][0]
        assert insert_data["report_type"] == "cycle_complete"
        assert insert_data["cycle_started_at"] == "2026-03-23T10:00:00+00:00"
        assert insert_data["cycle_ended_at"] == "2026-03-23T11:30:00+00:00"

    def test_saves_cycle_start_with_null_report_text(self):
        store, mock_client = _make_store()

        mock_result = MagicMock()
        mock_result.data = [{"id": "rpt-002"}]
        mock_client.table.return_value.insert.return_value.execute.return_value = mock_result

        result = store.save_report(
            cycle_id="sync_cycle:2026-03-23:0",
            report_text=None,
            summary_stats={"total_products": 200},
            report_type="cycle_start",
            cycle_started_at="2026-03-23T10:00:00+00:00",
        )

        insert_data = mock_client.table.return_value.insert.call_args[0][0]
        assert insert_data["report_text"] is None
        assert insert_data["report_type"] == "cycle_start"

    def test_default_report_type_is_cycle_complete(self):
        store, mock_client = _make_store()

        mock_result = MagicMock()
        mock_result.data = [{"id": "rpt-003"}]
        mock_client.table.return_value.insert.return_value.execute.return_value = mock_result

        store.save_report(
            cycle_id="sync_cycle:2026-03-23:0",
            report_text="<html>test</html>",
            summary_stats={},
        )

        insert_data = mock_client.table.return_value.insert.call_args[0][0]
        assert insert_data["report_type"] == "cycle_complete"


class TestUpdateEmailStatus:

    def test_updates_email_sent_flag(self):
        store, mock_client = _make_store()

        store.update_email_status("rpt-001", True)

        mock_client.table.assert_called_with("sync_reports")
        mock_client.table.return_value.update.assert_called_once_with(
            {"email_sent": True}
        )
        mock_client.table.return_value.update.return_value.eq.assert_called_once_with(
            "id", "rpt-001"
        )

    def test_handles_exception_gracefully(self):
        store, mock_client = _make_store()
        mock_client.table.return_value.update.side_effect = Exception("DB error")

        # Should not raise
        store.update_email_status("rpt-001", True)


class TestGetLatestReport:

    def test_filters_by_report_type(self):
        store, mock_client = _make_store()

        mock_result = MagicMock()
        mock_result.data = [{"id": "rpt-001", "report_type": "cycle_complete"}]
        mock_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = mock_result

        result = store.get_latest_report(report_type="cycle_complete")

        assert result["id"] == "rpt-001"
        mock_client.table.return_value.select.return_value.eq.assert_called_once_with(
            "report_type", "cycle_complete"
        )

    def test_no_filter_returns_any_type(self):
        store, mock_client = _make_store()

        mock_result = MagicMock()
        mock_result.data = [{"id": "rpt-002"}]
        mock_client.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = mock_result

        result = store.get_latest_report()

        assert result["id"] == "rpt-002"
