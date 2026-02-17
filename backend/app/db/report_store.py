"""
Report store — CRUD operations for the sync_reports table.
Version: 1.0.0
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
        report_text: str,
        summary_stats: Dict[str, Any],
        file_path: Optional[str] = None,
        email_sent: bool = False,
        email_recipients: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Save a generated report to the database."""
        data = {
            "cycle_id": cycle_id,
            "report_text": report_text,
            "summary_stats": summary_stats,
            "file_path": file_path,
            "email_sent": email_sent,
            "email_recipients": email_recipients or [],
        }

        try:
            result = self.client.table("sync_reports").insert(data).execute()
            report = result.data[0] if result.data else data
            logger.info(f"Saved report for cycle {cycle_id}, id={report.get('id')}")
            return report
        except Exception as e:
            logger.error(f"Error saving report: {e}")
            raise

    def get_latest_report(self) -> Optional[Dict[str, Any]]:
        """Get the most recently generated report."""
        try:
            result = self.client.table("sync_reports") \
                .select("*") \
                .order("created_at", desc=True) \
                .limit(1) \
                .execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Error getting latest report: {e}")
            return None

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
