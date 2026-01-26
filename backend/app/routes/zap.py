from typing import Any, Dict, List

from fastapi import APIRouter, Body, HTTPException

from app.schemas.zap import QuotePayload, ZapWebhookRequest
from app.services.zap_service import ZapService


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


def build_zap_router(service: ZapService) -> APIRouter:
    router = APIRouter()

    @router.post("/api/zap/webhook", response_model=QuotePayload)
    async def zap_webhook(payload: ZapWebhookRequest = Body(...)):
        try:
            payload_dict = payload.model_dump()
            service_payload = {
                "rfq_details": {
                    "rfq_number": payload_dict.get("rfq_number"),
                    "quote_submission_url": payload_dict.get("rfq_sub_url") or "",
                },
                "buyer_details": {
                    "company_name": payload_dict.get("buyer_company_name"),
                    "buyer_name": payload_dict.get("buyer_name"),
                    "address": payload_dict.get("buyer_address"),
                    "phone": payload_dict.get("buyer_phone"),
                    "email": payload_dict.get("buyer_email"),
                    "deliver_to_address": payload_dict.get("deliver_to_address"),
                },
                "requested_parts": _parse_requested_parts(payload_dict),
            }
            final_payload = await service.build_final_quote(service_payload)
            rfq_details = final_payload.get("rfq_details") or {}
            rfq_no = rfq_details.get("rfq_number")
            if not rfq_no:
                raise HTTPException(status_code=400, detail="rfq_details.rfq_number is required to update quotes")
            buyer_details = final_payload.get("buyer_details") or {}
            requested_parts = service_payload.get("requested_parts") or []
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
            await service.persist_quote(quote_row)
            return final_payload
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return router
