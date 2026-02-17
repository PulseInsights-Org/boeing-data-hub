"""
Report service — orchestrates sync report generation, storage, and email delivery.
Version: 1.0.0
"""
import logging
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.clients.gemini_client import GeminiClient
from app.clients.resend_client import ResendClient
from app.clients.supabase_client import SupabaseClient
from app.core.config import Settings
from app.db.report_store import ReportStore
from app.utils.cycle_tracker import get_cycle_progress

logger = logging.getLogger(__name__)


class ReportService:
    """Orchestrates sync report generation via Gemini LLM and email delivery."""

    def __init__(
        self,
        gemini: GeminiClient,
        resend_client: ResendClient,
        report_store: ReportStore,
        supabase_client: SupabaseClient,
        settings: Settings,
    ):
        self._gemini = gemini
        self._resend = resend_client
        self._report_store = report_store
        self._supabase = supabase_client
        self._settings = settings

    def generate_cycle_report(self, cycle_id: Optional[str] = None) -> Dict[str, Any]:
        """Generate a full sync cycle report.

        Steps:
        1. Fetch all sync data from product_sync_schedule
        2. Compute summary statistics
        3. Build prompt and call Gemini LLM
        4. Save report to temp file
        5. Persist to sync_reports table
        6. Send email to configured recipients

        Args:
            cycle_id: Optional cycle identifier. If None, uses current cycle.

        Returns:
            Dict with report_id, file_path, email_sent, summary.
        """
        # Resolve cycle_id
        if not cycle_id:
            progress = get_cycle_progress(self._settings.redis_url)
            cycle_id = progress["cycle_id"]

        logger.info(f"Generating sync report for cycle {cycle_id}")

        # Step 1-2: Fetch data and compute stats
        report_data = self.get_report_data()

        # Step 3: Build prompt and call LLM
        prompt = self._build_prompt(report_data, cycle_id)
        report_text = self._gemini.generate_content(prompt)

        logger.info(f"LLM report generated, length={len(report_text)} chars")

        # Step 4: Save to temp file
        file_path = self._save_to_temp_file(report_text, cycle_id)

        # Step 5: Persist to database
        summary_stats = report_data["summary"]
        email_sent = False
        recipients = self._settings.report_recipients

        saved = self._report_store.save_report(
            cycle_id=cycle_id,
            report_text=report_text,
            summary_stats=summary_stats,
            file_path=file_path,
            email_sent=False,
            email_recipients=recipients,
        )
        report_id = saved.get("id", "unknown")

        # Step 6: Send email
        if recipients and self._settings.resend_api_key:
            try:
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                subject = f"Sync Report — {now}"
                html_body = self._build_email_html(report_text, summary_stats)
                self._resend.send_email(recipients, subject, html_body)
                email_sent = True

                # Update email_sent flag in DB
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
            "summary": summary_stats,
        }

    def get_report_data(self) -> Dict[str, Any]:
        """Fetch all sync schedule data and compute summary statistics."""
        result = self._supabase.client.table("product_sync_schedule") \
            .select("*") \
            .eq("is_active", True) \
            .execute()

        products = result.data or []

        # Categorise products
        success_products = []
        failed_products = []
        pending_products = []
        syncing_products = []
        out_of_stock = []

        for p in products:
            status = p.get("sync_status", "pending")
            if status == "success":
                success_products.append(p)
            elif status == "failed":
                failed_products.append(p)
            elif status == "syncing":
                syncing_products.append(p)
            else:
                pending_products.append(p)

            if p.get("last_inventory_status") == "out_of_stock":
                out_of_stock.append(p)

        summary = {
            "total_products": len(products),
            "success_count": len(success_products),
            "failed_count": len(failed_products),
            "pending_count": len(pending_products),
            "syncing_count": len(syncing_products),
            "out_of_stock_count": len(out_of_stock),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        return {
            "products": products,
            "success_products": success_products,
            "failed_products": failed_products,
            "pending_products": pending_products,
            "out_of_stock": out_of_stock,
            "summary": summary,
        }

    def _build_prompt(self, data: Dict[str, Any], cycle_id: str) -> str:
        """Construct the Gemini prompt from sync data."""
        summary = data["summary"]
        failed = data["failed_products"]
        out_of_stock = data["out_of_stock"]
        products = data["products"]

        # Build product details section (limit to avoid token overflow)
        product_lines = []
        for p in products[:200]:
            sku = p.get("sku", "?")
            status = p.get("sync_status", "?")
            price = p.get("last_price")
            qty = p.get("last_quantity")
            loc_status = p.get("last_inventory_status", "?")
            error = p.get("last_error", "")
            bucket = p.get("hour_bucket", "?")
            locations = p.get("last_locations") or p.get("last_location_summary", "")

            line = (
                f"  SKU: {sku} | Status: {status} | Price: {price} | "
                f"Qty: {qty} | InvStatus: {loc_status} | Bucket: {bucket}"
            )
            if locations:
                line += f" | Locations: {locations}"
            if error:
                line += f" | Error: {error[:100]}"
            product_lines.append(line)

        product_section = "\n".join(product_lines) if product_lines else "  (no products)"

        # Failed products detail
        failed_lines = []
        for p in failed[:50]:
            failed_lines.append(
                f"  SKU: {p.get('sku')} | Failures: {p.get('consecutive_failures')} | "
                f"Error: {p.get('last_error', 'unknown')[:150]}"
            )
        failed_section = "\n".join(failed_lines) if failed_lines else "  (none)"

        # Out of stock detail
        oos_lines = [f"  SKU: {p.get('sku')}" for p in out_of_stock[:50]]
        oos_section = "\n".join(oos_lines) if oos_lines else "  (none)"

        prompt = f"""You are a supply-chain operations analyst. Generate a concise, professional
sync cycle report for the Boeing Data Hub auto-sync system.

CYCLE: {cycle_id}
GENERATED AT: {summary['generated_at']}

SUMMARY STATISTICS:
  Total products in sync: {summary['total_products']}
  Successfully synced: {summary['success_count']}
  Failed: {summary['failed_count']}
  Pending: {summary['pending_count']}
  Currently syncing: {summary['syncing_count']}
  Out of stock: {summary['out_of_stock_count']}

PRODUCT DETAILS (up to 200):
{product_section}

FAILED PRODUCTS (up to 50):
{failed_section}

OUT OF STOCK PRODUCTS (up to 50):
{oos_section}

INSTRUCTIONS:
1. Write a 2-3 paragraph executive summary of the sync cycle.
2. Highlight any critical issues (high failure rates, many out-of-stock items).
3. List the top 5 most notable changes (price changes, inventory changes).
4. If there are failures, provide a brief root cause analysis based on error messages.
5. End with recommendations if any action is needed.

Format the report in clean HTML suitable for email delivery. Use simple inline
styles. Do NOT include <html>, <head>, or <body> tags — just the content div."""

        return prompt

    def _save_to_temp_file(self, report_text: str, cycle_id: str) -> str:
        """Save report text to a temporary file."""
        safe_id = cycle_id.replace(":", "_")
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".html",
            prefix=f"sync_report_{safe_id}_",
            delete=False,
            encoding="utf-8",
        ) as f:
            f.write(report_text)
            path = f.name

        logger.info(f"Report saved to {path}")
        return path

    def _build_email_html(
        self, report_text: str, summary: Dict[str, Any]
    ) -> str:
        """Wrap the LLM report in an email-friendly HTML shell."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        total = summary.get("total_products", 0)
        success = summary.get("success_count", 0)
        failed = summary.get("failed_count", 0)

        return f"""<div style="font-family: Arial, sans-serif; max-width: 700px; margin: 0 auto;">
  <div style="background: #1a1a2e; color: #ffffff; padding: 20px; border-radius: 8px 8px 0 0;">
    <h1 style="margin: 0; font-size: 22px;">Boeing Data Hub — Sync Report</h1>
    <p style="margin: 5px 0 0; opacity: 0.8; font-size: 14px;">{now}</p>
  </div>
  <div style="background: #f0f0f5; padding: 15px; display: flex; gap: 15px;">
    <div style="flex:1; background:#fff; padding:12px; border-radius:6px; text-align:center;">
      <div style="font-size:24px; font-weight:bold; color:#1a1a2e;">{total}</div>
      <div style="font-size:12px; color:#666;">Total</div>
    </div>
    <div style="flex:1; background:#fff; padding:12px; border-radius:6px; text-align:center;">
      <div style="font-size:24px; font-weight:bold; color:#27ae60;">{success}</div>
      <div style="font-size:12px; color:#666;">Success</div>
    </div>
    <div style="flex:1; background:#fff; padding:12px; border-radius:6px; text-align:center;">
      <div style="font-size:24px; font-weight:bold; color:#e74c3c;">{failed}</div>
      <div style="font-size:12px; color:#666;">Failed</div>
    </div>
  </div>
  <div style="background: #ffffff; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #e0e0e0;">
    {report_text}
  </div>
  <p style="text-align:center; font-size:11px; color:#999; margin-top:15px;">
    Auto-generated by Boeing Data Hub Sync System
  </p>
</div>"""
