"""
Report service — builds dashboard-style sync reports and sends via email.

Generates two types of reports:
1. Cycle Start Notification — lightweight email when sync begins
2. Cycle Completion Report — full dashboard with metrics, charts, and tables

Pure HTML/CSS dashboard (no LLM) with metric cards, SVG donut chart,
bucket distribution bars, and compact change/failure tables.
Version: 2.0.0
"""
import html
import logging
import math
import tempfile
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.clients.resend_client import ResendClient
from app.clients.supabase_client import SupabaseClient
from app.core.config import Settings
from app.db.report_store import ReportStore
from app.utils.cycle_tracker import (
    get_cycle_progress,
    get_cycle_changes,
    get_cycle_start_time,
)

logger = logging.getLogger(__name__)

# Color palette
CLR_HEADER = "#1a1a2e"
CLR_START_HEADER = "#1a3a5c"
CLR_SUCCESS = "#27ae60"
CLR_FAILED = "#e74c3c"
CLR_CHANGED = "#f39c12"
CLR_NEUTRAL = "#6c7a89"
CLR_UNCHANGED = "#3498db"
CLR_OOS = "#e67e22"
CLR_BAR_BG = "#e8e8e8"
CLR_BAR_FG = "#3498db"
CLR_WARNING_BG = "#fff3cd"
CLR_WARNING_BORDER = "#ffc107"
CLR_WARNING_TEXT = "#856404"


class ReportService:
    """Builds dashboard-style sync cycle reports and delivers via email."""

    def __init__(
        self,
        resend_client: ResendClient,
        report_store: ReportStore,
        supabase_client: SupabaseClient,
        settings: Settings,
    ):
        self._resend = resend_client
        self._report_store = report_store
        self._supabase = supabase_client
        self._settings = settings

    # ── Public API ────────────────────────────────────────────────────────

    def generate_cycle_start_report(self) -> Dict[str, Any]:
        """Generate and send a lightweight 'Sync Cycle Started' notification.

        Called when the first bucket of a new sync cycle is dispatched.

        Returns:
            Dict with report_id, cycle_id, email_sent, summary.
        """
        progress = get_cycle_progress(self._settings.redis_url)
        cycle_id = progress["cycle_id"]
        started_at = get_cycle_start_time(
            cycle_id, self._settings.redis_url
        )

        total_products = self._get_total_active_products()
        bucket_count = progress["total_buckets"]

        # Estimate: 10 SKUs per Boeing call at 2 req/min
        api_calls_estimate = math.ceil(total_products / 10)
        minutes_estimate = math.ceil(api_calls_estimate / 2)

        now = datetime.now(timezone.utc)
        if started_at is None:
            logger.warning(
                f"No cycle start time found in Redis for {cycle_id}; "
                "using report generation time as fallback"
            )
            started_at_display = now.isoformat()
        else:
            started_at_display = started_at

        summary = {
            "total_products": total_products,
            "bucket_count": bucket_count,
            "estimated_api_calls": api_calls_estimate,
            "estimated_duration_minutes": minutes_estimate,
            "started_at": started_at_display,
        }

        start_html = self._build_cycle_start_html(summary, now)

        email_sent = False
        recipients = self._settings.report_recipients

        saved = self._report_store.save_report(
            cycle_id=cycle_id,
            report_text=start_html,
            summary_stats=summary,
            email_sent=False,
            email_recipients=recipients,
            report_type="cycle_start",
            cycle_started_at=started_at_display,
        )
        report_id = saved.get("id", "unknown")

        if recipients and self._settings.resend_api_key:
            try:
                date_str = now.strftime("%b %d, %Y")
                subject = f"Sync Cycle Started — {date_str}"
                self._resend.send_email(recipients, subject, start_html)
                email_sent = True

                self._report_store.update_email_status(report_id, True)

                logger.info(f"Cycle start email sent to {recipients}")
            except Exception as e:
                logger.error(f"Failed to send cycle start email: {e}")
        else:
            logger.info("Cycle start email skipped (no recipients or no API key)")

        return {
            "report_id": report_id,
            "cycle_id": cycle_id,
            "email_sent": email_sent,
            "summary": summary,
        }

    def generate_cycle_report(
        self,
        cycle_id: Optional[str] = None,
        still_syncing: int = 0,
    ) -> Dict[str, Any]:
        """Generate a dashboard-style sync cycle completion report.

        Args:
            cycle_id: Optional cycle identifier. If None, uses current cycle.
            still_syncing: Number of products still in 'syncing' state when
                           the report was force-generated after timeout.
                           0 means all products completed normally.

        Returns:
            Dict with report_id, cycle_id, file_path, email_sent, summary.
        """
        if not cycle_id:
            progress = get_cycle_progress(self._settings.redis_url)
            cycle_id = progress["cycle_id"]

        logger.info(f"Generating completion report for cycle {cycle_id}")

        report_data = self._get_report_data()
        changes = get_cycle_changes(cycle_id, self._settings.redis_url)

        summary = report_data["summary"]
        summary["changes_count"] = len(changes)
        summary["unchanged_count"] = max(
            0,
            summary["total_products"] - len(changes) - summary["failed_count"],
        )
        summary["still_syncing"] = still_syncing

        # Cycle timing
        started_at = get_cycle_start_time(cycle_id, self._settings.redis_url)
        ended_at = datetime.now(timezone.utc).isoformat()
        duration_display = None

        if started_at:
            try:
                start_dt = datetime.fromisoformat(started_at)
                end_dt = datetime.fromisoformat(ended_at)
                delta = end_dt - start_dt
                total_minutes = int(delta.total_seconds() // 60)
                hours, mins = divmod(total_minutes, 60)
                duration_display = (
                    f"{hours}h {mins}m" if hours > 0 else f"{mins}m"
                )
                summary["cycle_duration_minutes"] = total_minutes
            except (ValueError, TypeError):
                pass

        summary["started_at"] = started_at
        summary["ended_at"] = ended_at
        summary["duration_display"] = duration_display

        dashboard_html = self._build_dashboard_html(
            report_data, changes, summary, still_syncing
        )

        file_path = self._save_to_temp_file(dashboard_html, cycle_id)

        email_sent = False
        recipients = self._settings.report_recipients

        saved = self._report_store.save_report(
            cycle_id=cycle_id,
            report_text=dashboard_html,
            summary_stats=summary,
            file_path=file_path,
            email_sent=False,
            email_recipients=recipients,
            report_type="cycle_complete",
            cycle_started_at=started_at,
            cycle_ended_at=ended_at,
        )
        report_id = saved.get("id", "unknown")

        if recipients and self._settings.resend_api_key:
            try:
                date_str = datetime.now(timezone.utc).strftime("%b %d, %Y")
                if still_syncing > 0:
                    subject = (
                        f"Sync Cycle Complete (Partial) — {date_str}"
                    )
                else:
                    subject = f"Sync Cycle Complete — {date_str}"
                self._resend.send_email(recipients, subject, dashboard_html)
                email_sent = True

                self._report_store.update_email_status(report_id, True)

                logger.info(f"Completion report email sent to {recipients}")
            except Exception as e:
                logger.error(f"Failed to send completion report email: {e}")
        else:
            logger.info("Email delivery skipped (no recipients or no API key)")

        return {
            "report_id": report_id,
            "cycle_id": cycle_id,
            "file_path": file_path,
            "email_sent": email_sent,
            "summary": summary,
        }

    # ── Data fetching ─────────────────────────────────────────────────────

    def _get_total_active_products(self) -> int:
        """Count total active products in the sync schedule."""
        try:
            result = self._supabase.client.table("product_sync_schedule") \
                .select("id", count="exact") \
                .eq("is_active", True) \
                .execute()
            return result.count or 0
        except Exception as e:
            logger.error(f"Error counting active products: {e}")
            return 0

    def _get_report_data(self) -> Dict[str, Any]:
        """Fetch sync schedule data and compute summary statistics."""
        result = self._supabase.client.table("product_sync_schedule") \
            .select("*") \
            .eq("is_active", True) \
            .execute()

        products = result.data or []

        success_products: List[Dict] = []
        failed_products: List[Dict] = []
        out_of_stock: List[Dict] = []
        slot_counts: Dict[int, int] = {}

        for p in products:
            status = p.get("sync_status", "pending")
            if status == "success":
                success_products.append(p)
            elif status == "failed":
                failed_products.append(p)

            if p.get("last_inventory_status") == "out_of_stock":
                out_of_stock.append(p)

            bucket = p.get("hour_bucket")
            if bucket is not None:
                slot_counts[bucket] = slot_counts.get(bucket, 0) + 1

        summary = {
            "total_products": len(products),
            "success_count": len(success_products),
            "failed_count": len(failed_products),
            "out_of_stock_count": len(out_of_stock),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        return {
            "products": products,
            "success_products": success_products,
            "failed_products": failed_products,
            "out_of_stock": out_of_stock,
            "slot_counts": slot_counts,
            "summary": summary,
        }

    # ── Cycle Start HTML ──────────────────────────────────────────────────

    def _build_cycle_start_html(
        self, summary: Dict[str, Any], now: datetime
    ) -> str:
        """Build the 'Sync Cycle Started' notification email."""
        timestamp = now.strftime("%b %d, %Y — %H:%M UTC")
        total = summary["total_products"]
        buckets = summary["bucket_count"]
        est_calls = summary["estimated_api_calls"]
        est_mins = summary["estimated_duration_minutes"]

        hours, mins = divmod(est_mins, 60)
        est_display = f"{hours}h {mins}m" if hours > 0 else f"{mins}m"

        def _info_row(label: str, value: str) -> str:
            return f"""<tr>
  <td style="font-size:14px;color:#888;padding:8px 12px;border-bottom:1px solid #f0f0f0;width:200px;">{label}</td>
  <td style="font-size:14px;color:#333;font-weight:600;padding:8px 12px;border-bottom:1px solid #f0f0f0;">{value}</td>
</tr>"""

        return f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:650px;margin:0 auto;background:#ffffff;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:{CLR_START_HEADER};border-radius:8px 8px 0 0;">
<tr><td style="padding:20px 24px;">
  <div style="color:#ffffff;font-size:20px;font-weight:bold;margin:0;">Boeing Data Hub — Sync Cycle Started</div>
  <div style="color:#ffffffcc;font-size:13px;margin-top:4px;">{timestamp}</div>
</td></tr>
</table>
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f7ff;padding:16px 24px;">
<tr><td style="padding:12px 0;">
  <div style="font-size:15px;color:{CLR_START_HEADER};line-height:1.5;">
    A new sync cycle has started. All active products will be refreshed
    against the Boeing API to check for pricing and inventory changes.
  </div>
</td></tr>
</table>
<table width="100%" cellpadding="0" cellspacing="0" style="padding:16px 24px;">
  {_info_row("Products to Refresh", f"{total:,}")}
  {_info_row("Hour Buckets", str(buckets))}
  {_info_row("Estimated API Calls", f"~{est_calls:,}")}
  {_info_row("Estimated Duration", f"~{est_display}")}
</table>
<table width="100%" cellpadding="0" cellspacing="0" style="padding:8px 24px 16px;">
<tr><td style="font-size:12px;color:#999;line-height:1.4;">
  Products are distributed across {buckets} hourly buckets and refreshed in batches of 10.
  A completion report with full details will be sent once all products have been processed.
</td></tr>
</table>
<table width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #e9ecef;">
<tr><td style="padding:14px 24px;text-align:center;font-size:11px;color:#999;">
  Auto-generated by Boeing Data Hub Sync System
</td></tr>
</table>
</div>"""

    # ── Completion Dashboard HTML ─────────────────────────────────────────

    def _build_dashboard_html(
        self,
        data: Dict[str, Any],
        changes: Dict[str, str],
        summary: Dict[str, Any],
        still_syncing: int = 0,
    ) -> str:
        """Build the complete completion dashboard HTML email."""
        now = datetime.now(timezone.utc).strftime("%b %d, %Y — %H:%M UTC")

        header = self._build_header(now, summary.get("duration_display"))
        warning = self._build_incomplete_warning(still_syncing)
        metrics = self._build_metric_cards(summary, len(changes))
        donut = self._build_status_donut_svg(
            summary["success_count"], summary["failed_count"]
        )
        buckets = self._build_bucket_bars_html(data["slot_counts"])
        changes_table = self._build_changes_table_html(changes)
        failures_table = self._build_failures_table_html(
            data["failed_products"]
        )
        oos_section = self._build_out_of_stock_summary(data["out_of_stock"])
        footer = self._build_footer()

        return f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:650px;margin:0 auto;background:#ffffff;">
{header}
{warning}
{metrics}
{donut}
{buckets}
{changes_table}
{failures_table}
{oos_section}
{footer}
</div>"""

    def _build_header(
        self, timestamp: str, duration: Optional[str] = None
    ) -> str:
        duration_badge = ""
        if duration:
            duration_badge = (
                f' <span style="background:#ffffff33;padding:2px 8px;'
                f'border-radius:4px;font-size:11px;margin-left:8px;">'
                f'{duration}</span>'
            )
        return f"""<table width="100%" cellpadding="0" cellspacing="0" style="background:{CLR_HEADER};border-radius:8px 8px 0 0;">
<tr><td style="padding:20px 24px;">
  <div style="color:#ffffff;font-size:20px;font-weight:bold;margin:0;">Boeing Data Hub — Sync Cycle Complete{duration_badge}</div>
  <div style="color:#ffffffcc;font-size:13px;margin-top:4px;">{timestamp}</div>
</td></tr>
</table>"""

    def _build_incomplete_warning(self, still_syncing: int) -> str:
        """Warning banner when report is generated with products still syncing."""
        if still_syncing <= 0:
            return ""
        return f"""<table width="100%" cellpadding="0" cellspacing="0" style="background:{CLR_WARNING_BG};border-left:4px solid {CLR_WARNING_BORDER};padding:12px 24px;">
<tr><td style="font-size:13px;color:{CLR_WARNING_TEXT};line-height:1.4;">
  <strong>Incomplete Sync:</strong> {still_syncing} product{"s" if still_syncing != 1 else ""} were
  still syncing when this report was generated. Their results are not included
  in the statistics below. These products may have timed out or are still being
  processed by the Boeing/Shopify APIs.
</td></tr>
</table>"""

    def _build_metric_cards(
        self, summary: Dict[str, Any], changes_count: int
    ) -> str:
        total = summary["total_products"]
        success = summary["success_count"]
        failed = summary["failed_count"]
        unchanged = max(0, summary.get("unchanged_count", total - changes_count - failed))

        def _card(value: int, label: str, color: str, width: str = "20%") -> str:
            return f"""<td style="padding:6px;" width="{width}">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8f9fa;border-radius:6px;border:1px solid #e9ecef;">
  <tr><td style="text-align:center;padding:14px 8px 4px;">
    <div style="font-size:28px;font-weight:bold;color:{color};">{value}</div>
  </td></tr>
  <tr><td style="text-align:center;padding:2px 8px 12px;">
    <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:0.5px;">{label}</div>
  </td></tr>
  </table>
</td>"""

        return f"""<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f0f5;padding:12px 8px;">
<tr>
  {_card(total, "Total", CLR_NEUTRAL)}
  {_card(success, "Success", CLR_SUCCESS)}
  {_card(failed, "Failed", CLR_FAILED)}
  {_card(changes_count, "Changed", CLR_CHANGED)}
  {_card(unchanged, "Unchanged", CLR_UNCHANGED)}
</tr>
</table>"""

    def _build_status_donut_svg(self, success: int, failed: int) -> str:
        """Build an inline SVG donut chart for success vs failed breakdown."""
        total = success + failed
        if total == 0:
            return ""

        success_pct = success / total
        failed_pct = failed / total
        success_rate = round(success_pct * 100, 1)

        radius = 40
        circumference = 2 * math.pi * radius
        success_dash = success_pct * circumference
        failed_dash = failed_pct * circumference
        success_offset = 0
        failed_offset = -success_dash

        svg = f"""<svg width="110" height="110" viewBox="0 0 110 110" xmlns="http://www.w3.org/2000/svg">
  <circle cx="55" cy="55" r="{radius}" fill="none" stroke="{CLR_BAR_BG}" stroke-width="12"/>
  <circle cx="55" cy="55" r="{radius}" fill="none" stroke="{CLR_SUCCESS}" stroke-width="12"
    stroke-dasharray="{success_dash} {circumference}" stroke-dashoffset="{success_offset}"
    transform="rotate(-90 55 55)"/>
  <circle cx="55" cy="55" r="{radius}" fill="none" stroke="{CLR_FAILED}" stroke-width="12"
    stroke-dasharray="{failed_dash} {circumference}" stroke-dashoffset="{failed_offset}"
    transform="rotate(-90 55 55)"/>
  <text x="55" y="52" text-anchor="middle" font-size="16" font-weight="bold" fill="{CLR_HEADER}">{success_rate}%</text>
  <text x="55" y="66" text-anchor="middle" font-size="9" fill="#888">success</text>
</svg>"""

        legend_items = []
        if success > 0:
            legend_items.append(
                f'<tr><td style="padding:2px 6px 2px 0;"><span style="display:inline-block;width:10px;height:10px;'
                f'border-radius:50%;background:{CLR_SUCCESS};"></span></td>'
                f'<td style="font-size:13px;color:#444;padding:2px 0;">{success} Success</td></tr>'
            )
        if failed > 0:
            legend_items.append(
                f'<tr><td style="padding:2px 6px 2px 0;"><span style="display:inline-block;width:10px;height:10px;'
                f'border-radius:50%;background:{CLR_FAILED};"></span></td>'
                f'<td style="font-size:13px;color:#444;padding:2px 0;">{failed} Failed</td></tr>'
            )

        legend = f'<table cellpadding="0" cellspacing="0">{"".join(legend_items)}</table>'

        # Text fallback for email clients that don't render SVG
        text_fallback = (
            f'<div style="font-size:12px;color:#888;margin-top:4px;">'
            f'Success rate: {success_rate}% ({success}/{total})</div>'
        )

        return f"""<table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;padding:16px 24px;">
<tr>
  <td style="padding:0 0 6px;font-size:14px;font-weight:bold;color:{CLR_HEADER};" colspan="2">Status Breakdown</td>
</tr>
<tr>
  <td style="vertical-align:middle;width:130px;">{svg}{text_fallback}</td>
  <td style="vertical-align:middle;padding-left:16px;">{legend}</td>
</tr>
</table>"""

    def _build_bucket_bars_html(self, slot_counts: Dict[int, int]) -> str:
        """Build CSS horizontal bar chart for bucket distribution."""
        if not slot_counts:
            return ""

        max_count = max(slot_counts.values()) if slot_counts else 1

        rows = []
        for bucket in sorted(slot_counts.keys()):
            count = slot_counts[bucket]
            pct = int((count / max_count) * 100) if max_count > 0 else 0
            rows.append(
                f"""<tr>
  <td style="font-size:12px;color:#666;padding:3px 8px 3px 0;white-space:nowrap;width:30px;">B{bucket}</td>
  <td style="padding:3px 0;">
    <div style="background:{CLR_BAR_BG};border-radius:4px;height:16px;width:100%;">
      <div style="background:{CLR_BAR_FG};border-radius:4px;height:16px;width:{pct}%;min-width:2px;"></div>
    </div>
  </td>
  <td style="font-size:12px;color:#666;padding:3px 0 3px 8px;white-space:nowrap;width:60px;">{count} product{"s" if count != 1 else ""}</td>
</tr>"""
            )

        return f"""<table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;padding:16px 24px;">
<tr><td colspan="3" style="padding:0 0 8px;font-size:14px;font-weight:bold;color:{CLR_HEADER};">Bucket Distribution</td></tr>
{"".join(rows)}
</table>"""

    def _build_changes_table_html(self, changes: Dict[str, str]) -> str:
        """Build compact table of products that changed this cycle."""
        section_title = f"""<td colspan="2" style="padding:0 0 8px;font-size:14px;font-weight:bold;color:{CLR_HEADER};">Changes This Cycle ({len(changes)})</td>"""

        if not changes:
            return f"""<table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;padding:16px 24px;">
<tr>{section_title}</tr>
<tr><td style="font-size:13px;color:#888;padding:4px 0;">No changes detected this cycle</td></tr>
</table>"""

        rows = []
        for sku, reason in sorted(changes.items()):
            safe_sku = html.escape(sku)
            safe_reason = html.escape(reason[:120])
            rows.append(
                f"""<tr>
  <td style="font-size:12px;color:#333;padding:6px 8px;border-bottom:1px solid #f0f0f0;">{safe_sku}</td>
  <td style="font-size:12px;color:#555;padding:6px 8px;border-bottom:1px solid #f0f0f0;">{safe_reason}</td>
</tr>"""
            )

        return f"""<table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;padding:16px 24px;">
<tr>{section_title}</tr>
<tr><td colspan="2">
  <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e9ecef;border-radius:6px;">
  <tr style="background:#f8f9fa;">
    <td style="font-size:11px;font-weight:bold;color:#666;padding:8px;text-transform:uppercase;border-bottom:1px solid #e9ecef;width:140px;">SKU</td>
    <td style="font-size:11px;font-weight:bold;color:#666;padding:8px;text-transform:uppercase;border-bottom:1px solid #e9ecef;">Change</td>
  </tr>
  {"".join(rows)}
  </table>
</td></tr>
</table>"""

    def _build_failures_table_html(self, failed_products: List[Dict]) -> str:
        """Build compact table of failed products with error grouping."""
        section_title = f"""<td colspan="3" style="padding:0 0 8px;font-size:14px;font-weight:bold;color:{CLR_HEADER};">Failures ({len(failed_products)})</td>"""

        if not failed_products:
            return f"""<table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;padding:16px 24px;">
<tr>{section_title}</tr>
<tr><td style="font-size:13px;color:#888;padding:4px 0;">No failures this cycle</td></tr>
</table>"""

        # Error grouping summary
        error_counter: Counter = Counter()
        for p in failed_products:
            error_msg = (p.get("last_error") or "unknown")[:80]
            error_counter[error_msg] += 1

        group_rows = ""
        if len(error_counter) < len(failed_products):
            # Only show grouping if there are duplicates
            group_items = []
            for error_msg, count in error_counter.most_common(5):
                group_items.append(
                    f'<tr><td style="font-size:12px;color:#555;padding:4px 8px;'
                    f'border-bottom:1px solid #f0f0f0;">{error_msg}</td>'
                    f'<td style="font-size:12px;color:{CLR_FAILED};padding:4px 8px;'
                    f'border-bottom:1px solid #f0f0f0;text-align:center;width:60px;'
                    f'font-weight:bold;">{count}</td></tr>'
                )
            group_rows = f"""<tr><td colspan="3" style="padding:4px 0 12px;">
  <div style="font-size:12px;color:#888;margin-bottom:4px;">Error Summary</div>
  <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #fde8e8;border-radius:4px;background:#fff5f5;">
  <tr style="background:#fde8e8;">
    <td style="font-size:10px;font-weight:bold;color:#888;padding:6px 8px;text-transform:uppercase;">Error</td>
    <td style="font-size:10px;font-weight:bold;color:#888;padding:6px 8px;text-transform:uppercase;width:60px;text-align:center;">Count</td>
  </tr>
  {"".join(group_items)}
  </table>
</td></tr>"""

        # Individual failure rows (capped at 30)
        rows = []
        for p in failed_products[:30]:
            safe_sku = html.escape(p.get("sku", "?"))
            safe_error = html.escape((p.get("last_error") or "unknown")[:120])
            failures = p.get("consecutive_failures", 0)
            rows.append(
                f"""<tr>
  <td style="font-size:12px;color:#333;padding:6px 8px;border-bottom:1px solid #f0f0f0;">{safe_sku}</td>
  <td style="font-size:12px;color:#555;padding:6px 8px;border-bottom:1px solid #f0f0f0;">{safe_error}</td>
  <td style="font-size:12px;color:{CLR_FAILED};padding:6px 8px;border-bottom:1px solid #f0f0f0;text-align:center;">{failures}</td>
</tr>"""
            )

        truncation_note = ""
        if len(failed_products) > 30:
            truncation_note = (
                f'<tr><td colspan="3" style="font-size:11px;color:#999;'
                f'padding:8px;text-align:center;">... and '
                f'{len(failed_products) - 30} more failures</td></tr>'
            )

        return f"""<table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;padding:16px 24px;">
<tr>{section_title}</tr>
{group_rows}
<tr><td colspan="3">
  <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e9ecef;border-radius:6px;">
  <tr style="background:#f8f9fa;">
    <td style="font-size:11px;font-weight:bold;color:#666;padding:8px;text-transform:uppercase;border-bottom:1px solid #e9ecef;width:140px;">SKU</td>
    <td style="font-size:11px;font-weight:bold;color:#666;padding:8px;text-transform:uppercase;border-bottom:1px solid #e9ecef;">Error</td>
    <td style="font-size:11px;font-weight:bold;color:#666;padding:8px;text-transform:uppercase;border-bottom:1px solid #e9ecef;width:40px;text-align:center;">Fails</td>
  </tr>
  {"".join(rows)}
  {truncation_note}
  </table>
</td></tr>
</table>"""

    def _build_out_of_stock_summary(
        self, out_of_stock: List[Dict]
    ) -> str:
        """Build a compact summary of out-of-stock products."""
        if not out_of_stock:
            return ""

        sku_list = ", ".join(
            p.get("sku", "?") for p in out_of_stock[:20]
        )
        more = (
            f" ... and {len(out_of_stock) - 20} more"
            if len(out_of_stock) > 20
            else ""
        )

        return f"""<table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;padding:16px 24px;">
<tr><td style="padding:0 0 8px;font-size:14px;font-weight:bold;color:{CLR_OOS};">Out of Stock ({len(out_of_stock)})</td></tr>
<tr><td style="font-size:12px;color:#666;line-height:1.5;">{sku_list}{more}</td></tr>
</table>"""

    def _build_footer(self) -> str:
        return f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #e9ecef;">
<tr><td style="padding:14px 24px;text-align:center;font-size:11px;color:#999;">
  Auto-generated by Boeing Data Hub Sync System
</td></tr>
</table>"""

    # ── Utilities ─────────────────────────────────────────────────────────

    def _save_to_temp_file(self, report_html: str, cycle_id: str) -> str:
        """Save report HTML to a temporary file."""
        safe_id = cycle_id.replace(":", "_")
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".html",
            prefix=f"sync_report_{safe_id}_",
            delete=False,
            encoding="utf-8",
        ) as f:
            f.write(report_html)
            path = f.name

        logger.info(f"Report saved to {path}")
        return path
