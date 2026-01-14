from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException

from .supabase_client import update_quote_form_data
from .zap_orchestrator import build_final_quote

router = APIRouter()


@router.post("/api/zap/webhook")
async def zap_webhook(payload: Dict[str, Any] = Body(...)):
    """Receive ZAP webhook payload and return normalized quote payload."""
    try:
        final_payload = await build_final_quote(payload)
        rfq_details = final_payload.get("rfq_details") or {}
        rfq_no = rfq_details.get("rfq_number")
        if not rfq_no:
            raise HTTPException(status_code=400, detail="rfq_details.rfq_number is required to update quotes")
        await update_quote_form_data(rfq_no, final_payload)
        return final_payload
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - generic guard
        raise HTTPException(status_code=500, detail=str(exc)) from exc
