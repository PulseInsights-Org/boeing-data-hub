from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ZapWebhookRequest(BaseModel):
    rfq_number: Optional[str] = None
    rfq_sub_url: Optional[str] = None
    buyer_company_name: Optional[str] = None
    buyer_name: Optional[str] = None
    buyer_address: Optional[str] = None
    buyer_phone: Optional[str] = None
    buyer_email: Optional[str] = None
    deliver_to_address: Optional[str] = None
    requested_parts: Optional[str] = None
    requested_qty: Optional[str] = None


class QuoteItem(BaseModel):
    part_no: str
    condition: str
    requested_qty: int
    no_quote: bool
    qty_available: Optional[int] = None
    traceability: Optional[str] = None
    uom: Optional[str] = None
    price_usd: Optional[float] = None
    price_type: Optional[str] = None
    tag_date: Optional[str] = None
    lead_time: Optional[str] = None


class QuoteDetails(BaseModel):
    quote_prepared_by: Optional[str] = None
    supplier_comments: Optional[str] = None
    items: List[QuoteItem]


class QuotePayload(BaseModel):
    rfq_details: Dict[str, Any]
    buyer_details: Dict[str, Any]
    quote_details: QuoteDetails
