from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException

from .zap_orchestrator import build_final_quote

router = APIRouter()


@router.post("/api/zap/webhook")
async def zap_webhook(payload: Dict[str, Any] = Body(...)):
    """Receive ZAP webhook payload and return normalized quote payload."""
    try:
        final_payload = await build_final_quote(payload)
        return final_payload
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - generic guard
        raise HTTPException(status_code=500, detail=str(exc)) from exc
