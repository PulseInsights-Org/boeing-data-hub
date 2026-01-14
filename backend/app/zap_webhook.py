import json
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Body, HTTPException

from .supabase_client import upsert_quote_form_data
from .zap_orchestrator import build_final_quote

router = APIRouter()
logger = logging.getLogger("zap_webhook")


def _split_csv(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def _parse_requested_parts(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    parts = _split_csv(payload.get("requested_parts"))
    qtys = _split_csv(payload.get("requested_qty"))

    requested: List[Dict[str, Any]] = []
    for idx, part_no in enumerate(parts):
        qty = 0
        if idx < len(qtys):
            try:
                qty = int(qtys[idx])
            except Exception:
                qty = 0
        requested.append(
            {
                "item_number": f"{idx + 1:02d}",
                "part_number_searched": part_no,
                "supplier_part_number": part_no,
                "description": "",
                "category": None,
                "quantity": qty,
                "unit_of_measure": None,
            }
        )
    return requested


@router.post("/api/zap/webhook")
async def zap_webhook(payload: Dict[str, Any] = Body(...)):
    """Receive ZAP webhook payload and return normalized quote payload."""
    try:
        logger.info("zap_webhook received payload=%s", json.dumps(payload, ensure_ascii=True))
        orchestrator_payload = {
            "rfq_details": {
                "rfq_number": payload.get("rfq_number"),
                "quote_submission_url": payload.get("rfq_sub_url") or "",
            },
            "buyer_details": {
                "company_name": payload.get("buyer_company_name"),
                "buyer_name": payload.get("buyer_name"),
                "address": payload.get("buyer_address"),
                "phone": payload.get("buyer_phone"),
                "email": payload.get("buyer_email"),
                "deliver_to_address": payload.get("deliver_to_address"),
            },
            "requested_parts": _parse_requested_parts(payload),
        }
        final_payload = await build_final_quote(orchestrator_payload)
        logger.info("zap_webhook final_payload=%s", json.dumps(final_payload, ensure_ascii=True))
        rfq_details = final_payload.get("rfq_details") or {}
        rfq_no = rfq_details.get("rfq_number")
        if not rfq_no:
            raise HTTPException(status_code=400, detail="rfq_details.rfq_number is required to update quotes")
        buyer_details = final_payload.get("buyer_details") or {}
        requested_parts = orchestrator_payload.get("requested_parts") or []
        quote_row = {
            "rfq_no": rfq_no,
            "buyer_name": buyer_details.get("buyer_name") or "",
            "buyer_company_name": buyer_details.get("company_name"),
            "buyer_address": buyer_details.get("address"),
            "buyer_phone": buyer_details.get("phone"),
            "buyer_email": buyer_details.get("email"),
            "buyer_delivery_address": buyer_details.get("deliver_to_address"),
            "requested_parts": requested_parts,
            "form_data": final_payload,
        }
        await upsert_quote_form_data(quote_row)
        return final_payload
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - generic guard
        raise HTTPException(status_code=500, detail=str(exc)) from exc
