import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.clients.shopify_client import ShopifyClient
from app.db.supabase_store import SupabaseStore
from app.services.boeing_service import BoeingService


logger = logging.getLogger("zap_service")


class ZapService:
    def __init__(self, shopify_client: ShopifyClient, boeing_service: BoeingService, store: SupabaseStore) -> None:
        self._shopify_client = shopify_client
        self._boeing_service = boeing_service
        self._store = store

    def _to_int(self, value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except Exception:
            return None

    def _to_float(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            return None

    def _tag_date(self) -> str:
        return datetime.utcnow().strftime("%b-%d-%y").upper()

    def _base_item(self, part_no: str, requested_qty: int) -> Dict[str, Any]:
        return {
            "part_no": part_no,
            "condition": "OH",
            "requested_qty": requested_qty,
            "no_quote": True,
        }

    async def _shopify_item(self, part_no: str, requested_qty: int, uom: str) -> Optional[Dict[str, Any]]:
        logger.info("shopify lookup sku=%s requested_qty=%s uom=%s", part_no, requested_qty, uom)
        variant = await self._shopify_client.get_variant_by_sku(part_no)
        if not variant:
            logger.info("shopify lookup miss sku=%s", part_no)
            return None

        price = self._to_float(variant.get("price"))
        inventory = self._to_int(variant.get("inventoryQuantity"))

        item = self._base_item(part_no, requested_qty)
        item.update(
            {
                "no_quote": False,
                "qty_available": inventory if inventory is not None else 0,
                "traceability": "SHOPIFY",
                "uom": uom,
                "price_usd": price if price is not None else 0,
                "price_type": "OUTRIGHT",
                "tag_date": self._tag_date(),
                "lead_time": "ON REQUEST",
            }
        )
        return item

    async def _boeing_item(self, part_no: str, requested_qty: int, uom: str) -> Optional[Dict[str, Any]]:
        logger.info("boeing lookup part_no=%s requested_qty=%s uom=%s", part_no, requested_qty, uom)
        results = await self._boeing_service.search_products(part_no)
        if not results:
            logger.info("boeing lookup miss part_no=%s", part_no)
            return None

        product = results[0]
        price = self._to_float(product.get("cost_per_item") or product.get("net_price"))
        inventory = self._to_int(product.get("inventory_quantity"))
        base_uom = product.get("base_uom") or uom

        item = self._base_item(part_no, requested_qty)
        item.update(
            {
                "no_quote": False,
                "qty_available": inventory if inventory is not None else 0,
                "traceability": "VENDOR",
                "uom": base_uom,
                "price_usd": price if price is not None else 0,
                "price_type": "OUTRIGHT",
                "tag_date": self._tag_date(),
                "lead_time": "ON REQUEST",
            }
        )
        return item

    async def build_final_quote(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        requested_parts: List[Dict[str, Any]] = payload.get("requested_parts") or []
        logger.info("build_final_quote requested_parts_count=%s", len(requested_parts))
        items: List[Dict[str, Any]] = []

        for part in requested_parts:
            part_no = (
                part.get("part_number_searched")
                or part.get("supplier_part_number")
                or part.get("part_number")
                or ""
            )
            requested_qty = self._to_int(part.get("quantity")) or 0
            uom = part.get("unit_of_measure") or "EA"

            item = await self._shopify_item(part_no, requested_qty, uom)
            if item is None:
                item = await self._boeing_item(part_no, requested_qty, uom)
            if item is None:
                item = self._base_item(part_no, requested_qty)

            items.append(item)

        final_payload = {
            "rfq_details": payload.get("rfq_details") or {},
            "buyer_details": payload.get("buyer_details") or {},
            "quote_details": {
                "quote_prepared_by": "Manesh Bhide",
                "supplier_comments": "Auto-generated via inventory lookup",
                "items": items,
            },
        }

        print(json.dumps(final_payload, indent=2))
        logger.info("build_final_quote final_payload=%s", json.dumps(final_payload, ensure_ascii=True))
        return final_payload

    async def persist_quote(self, quote_row: Dict[str, Any]) -> None:
        await self._store.upsert_quote_form_data(quote_row)
