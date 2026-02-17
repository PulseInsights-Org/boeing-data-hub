"""
Resend email client — transactional email delivery for sync reports.
Version: 1.0.0
"""
import logging
from typing import Dict, Any

import resend

logger = logging.getLogger(__name__)


class ResendClient:
    """Thin synchronous wrapper around the Resend SDK."""

    def __init__(self, api_key: str, from_address: str):
        resend.api_key = api_key
        self._from_address = from_address
        logger.info(f"ResendClient initialised with from={from_address}")

    def send_email(
        self,
        to: list[str],
        subject: str,
        html_body: str,
    ) -> Dict[str, Any]:
        """Send an HTML email via Resend.

        Args:
            to: List of recipient email addresses.
            subject: Email subject line.
            html_body: HTML content of the email.

        Returns:
            Resend API response dict (contains 'id' on success).
        """
        params: resend.Emails.SendParams = {
            "from": self._from_address,
            "to": to,
            "subject": subject,
            "html": html_body,
        }

        response = resend.Emails.send(params)
        logger.info(f"Email sent to {to}, response={response}")
        return response
