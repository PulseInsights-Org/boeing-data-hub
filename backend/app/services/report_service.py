"""
Report service — builds dashboard-style sync reports and sends via email.

Generates a pure HTML/CSS dashboard (no LLM) with metric cards,
SVG donut chart, bucket distribution bars, and compact change/failure tables.
Version: 1.1.0
"""
import logging
import math
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.clients.resend_client import ResendClient
from app.clients.supabase_client import SupabaseClient
from app.core.config import Settings
from app.db.report_store import ReportStore
from app.utils.cycle_tracker import get_cycle_progress, get_cycle_changes

logger = logging.getLogger(__name__)

# Color palette
CLR_HEADER = "#1a1a2e"
CLR_SUCCESS = "#27ae60"
CLR_FAILED = "#e74c3c"
CLR_CHANGED = "#f39c12"
CLR_NEUTRAL = "#6c7a89"
CLR_OOS = "#e67e22"
CLR_BAR_BG = "#e8e8e8"
CLR_BAR_FG = "#3498db"


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

    def generate_cycle_report(self, cycle_id: Optional[str] = None) -> Dict[str, Any]:
        """Generate a dashboard-style sync cycle report.

        Steps:
        1. Fetch sync data from product_sync_schedule
        2. Fetch cycle changes from Redis
        3. Build dashboard HTML
        4. Save to temp file
        5. Persist to sync_reports table
        6. Send email

        Returns:
            Dict with report_id, cycle_id, file_path, email_sent, summary.
        """
        if not cycle_id:
            progress = get_cycle_progress(self._settings.redis_url)
            cycle_id = progress["cycle_id"]

        logger.info(f"Generating dashboard report for cycle {cycle_id}")

        report_data = self._get_report_data()
        changes = get_cycle_changes(cycle_id, self._settings.redis_url)

        summary = report_data["summary"]
        summary["changes_count"] = len(changes)

        dashboard_html = self._build_dashboard_html(report_data, changes, cycle_id)

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
        )
        report_id = saved.get("id", "unknown")

        if recipients and self._settings.resend_api_key:
            try:
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                subject = f"Sync Report — {now}"
                self._resend.send_email(recipients, subject, dashboard_html)
                email_sent = True

                self._report_store.client.table("sync_reports") \
                    .update({"email_sent": True}) \
                    .eq("id", report_id) \
                    .execute()

                logger.info(f"Report email sent to {recipients}")
            except Exception as e:
                logger.error(f"Failed to send report email: {e}")
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

    # ── Dashboard HTML builder ────────────────────────────────────────────

    def _build_dashboard_html(
        self,
        data: Dict[str, Any],
        changes: Dict[str, str],
        cycle_id: str,
    ) -> str:
        """Build the complete dashboard HTML email."""
        summary = data["summary"]
        now = datetime.now(timezone.utc).strftime("%b %d, %H:%M UTC")

        header = self._build_header(cycle_id, now)
        metrics = self._build_metric_cards(summary, len(changes))
        donut = self._build_status_donut_svg(
            summary["success_count"], summary["failed_count"]
        )
        buckets = self._build_bucket_bars_html(data["slot_counts"])
        changes_table = self._build_changes_table_html(changes)
        failures_table = self._build_failures_table_html(data["failed_products"])
        footer = self._build_footer()

        return f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:650px;margin:0 auto;background:#ffffff;">
{header}
{metrics}
{donut}
{buckets}
{changes_table}
{failures_table}
{footer}
</div>"""

    def _build_header(self, cycle_id: str, timestamp: str) -> str:
        return f"""<table width="100%" cellpadding="0" cellspacing="0" style="background:{CLR_HEADER};border-radius:8px 8px 0 0;">
<tr><td style="padding:20px 24px;">
  <div style="color:#ffffff;font-size:20px;font-weight:bold;margin:0;">Boeing Data Hub — Sync Cycle Report</div>
  <div style="color:#ffffffcc;font-size:13px;margin-top:4px;">{cycle_id} &bull; {timestamp}</div>
</td></tr>
</table>"""

    def _build_metric_cards(self, summary: Dict[str, Any], changes_count: int) -> str:
        total = summary["total_products"]
        success = summary["success_count"]
        failed = summary["failed_count"]
        oos = summary.get("out_of_stock_count", 0)

        def _card(value: int, label: str, color: str) -> str:
            return f"""<td style="padding:6px;" width="25%">
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

        # SVG donut chart using stroke-dasharray
        radius = 40
        circumference = 2 * math.pi * radius
        success_dash = success_pct * circumference
        failed_dash = failed_pct * circumference
        # Rotate start to top (-90deg)
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

        return f"""<table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;padding:16px 24px;">
<tr>
  <td style="padding:0 0 6px;font-size:14px;font-weight:bold;color:{CLR_HEADER};" colspan="2">Status Breakdown</td>
</tr>
<tr>
  <td style="vertical-align:middle;width:130px;">{svg}</td>
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
        section_title = f"""<td style="padding:0 0 8px;font-size:14px;font-weight:bold;color:{CLR_HEADER};">Changes This Cycle</td>"""

        if not changes:
            return f"""<table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;padding:16px 24px;">
<tr>{section_title}</tr>
<tr><td style="font-size:13px;color:#888;padding:4px 0;">No changes detected this cycle</td></tr>
</table>"""

        rows = []
        for sku, reason in sorted(changes.items()):
            rows.append(
                f"""<tr>
  <td style="font-size:12px;color:#333;padding:6px 8px;border-bottom:1px solid #f0f0f0;">{sku}</td>
  <td style="font-size:12px;color:#555;padding:6px 8px;border-bottom:1px solid #f0f0f0;">{reason[:120]}</td>
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
        """Build compact table of failed products with error messages."""
        section_title = f"""<td style="padding:0 0 8px;font-size:14px;font-weight:bold;color:{CLR_HEADER};">Failures</td>"""

        if not failed_products:
            return f"""<table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;padding:16px 24px;">
<tr>{section_title}</tr>
<tr><td style="font-size:13px;color:#888;padding:4px 0;">No failures this cycle</td></tr>
</table>"""

        rows = []
        for p in failed_products[:30]:
            sku = p.get("sku", "?")
            error = (p.get("last_error") or "unknown")[:120]
            failures = p.get("consecutive_failures", 0)
            rows.append(
                f"""<tr>
  <td style="font-size:12px;color:#333;padding:6px 8px;border-bottom:1px solid #f0f0f0;">{sku}</td>
  <td style="font-size:12px;color:#555;padding:6px 8px;border-bottom:1px solid #f0f0f0;">{error}</td>
  <td style="font-size:12px;color:{CLR_FAILED};padding:6px 8px;border-bottom:1px solid #f0f0f0;text-align:center;">{failures}</td>
</tr>"""
            )

        return f"""<table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;padding:16px 24px;">
<tr>{section_title}</tr>
<tr><td colspan="3">
  <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e9ecef;border-radius:6px;">
  <tr style="background:#f8f9fa;">
    <td style="font-size:11px;font-weight:bold;color:#666;padding:8px;text-transform:uppercase;border-bottom:1px solid #e9ecef;width:140px;">SKU</td>
    <td style="font-size:11px;font-weight:bold;color:#666;padding:8px;text-transform:uppercase;border-bottom:1px solid #e9ecef;">Error</td>
    <td style="font-size:11px;font-weight:bold;color:#666;padding:8px;text-transform:uppercase;border-bottom:1px solid #e9ecef;width:40px;text-align:center;">Fails</td>
  </tr>
  {"".join(rows)}
  </table>
</td></tr>
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
