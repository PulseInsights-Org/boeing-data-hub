"""
Report store — CRUD operations for the sync_reports table.
Version: 1.1.0
"""
import logging
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.clients.supabase_client import SupabaseClient

logger = logging.getLogger(__name__)


class ReportStore:
    """CRUD for sync report records."""

    def __init__(self, supabase_client: Optional[SupabaseClient] = None):
        self._supabase_client = supabase_client

    @property
    def client(self):
        if self._supabase_client is None:
            self._supabase_client = SupabaseClient(settings)
        return self._supabase_client.client

    def save_report(
        self,
        cycle_id: str,
        report_text: Optional[str],
        summary_stats: Dict[str, Any],
        file_path: Optional[str] = None,
        email_sent: bool = False,
        email_recipients: Optional[List[str]] = None,
        report_type: str = "cycle_complete",
        cycle_started_at: Optional[str] = None,
        cycle_ended_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Save a generated report to the database.

        Args:
            cycle_id: Sync cycle identifier.
            report_text: Full HTML of the report (None for lightweight start notifications).
            summary_stats: JSONB summary metrics.
            file_path: Optional temp file path.
            email_sent: Whether the email was delivered.
            email_recipients: List of recipient addresses.
            report_type: 'cycle_start' or 'cycle_complete'.
            cycle_started_at: ISO timestamp when the cycle began.
            cycle_ended_at: ISO timestamp when the cycle finished.
        """
        data = {
            "cycle_id": cycle_id,
            "report_text": report_text,
            "summary_stats": summary_stats,
            "file_path": file_path,
            "email_sent": email_sent,
            "email_recipients": email_recipients or [],
            "report_type": report_type,
            "cycle_started_at": cycle_started_at,
            "cycle_ended_at": cycle_ended_at,
        }

        try:
            result = self.client.table("sync_reports").insert(data).execute()
            report = result.data[0] if result.data else data
            logger.info(
                f"Saved {report_type} report for cycle {cycle_id}, "
                f"id={report.get('id')}"
            )
            return report
        except Exception as e:
            logger.error(f"Error saving report: {e}")
            raise

    def get_latest_report(
        self, report_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get the most recently generated report.

        Args:
            report_type: Optional filter by type ('cycle_start', 'cycle_complete').
                         If None, returns the latest regardless of type.
        """
        try:
            query = self.client.table("sync_reports").select("*")
            if report_type:
                query = query.eq("report_type", report_type)
            result = query.order("created_at", desc=True).limit(1).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Error getting latest report: {e}")
            return None

    def update_email_status(self, report_id: str, sent: bool) -> None:
        """Update the email_sent flag for a report."""
        try:
            self.client.table("sync_reports") \
                .update({"email_sent": sent}) \
                .eq("id", report_id) \
                .execute()
        except Exception as e:
            logger.error(f"Error updating email status for report {report_id}: {e}")

    def get_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific report by ID."""
        try:
            result = self.client.table("sync_reports") \
                .select("*") \
                .eq("id", report_id) \
                .execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Error getting report {report_id}: {e}")
            return None
