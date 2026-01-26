import json
import logging
from typing import Any, Dict, List

from app.clients.boeing_client import BoeingClient
from app.db.supabase_store import SupabaseStore
from app.utils.boeing_normalize import normalize_boeing_payload


class BoeingService:
    def __init__(self, client: BoeingClient, store: SupabaseStore) -> None:
        self._client = client
        self._store = store
        self._logger = logging.getLogger("boeing_service")

    async def search_products(self, query: str, user_id: str = "system") -> List[Dict[str, Any]]:
        payload = await self._client.fetch_price_availability(query)
        self._logger.info("boeing raw response=%s", json.dumps(payload, ensure_ascii=True))
        normalized = normalize_boeing_payload(query, payload)
        self._logger.info("boeing normalized=%s", json.dumps(normalized, ensure_ascii=True))
        shopify_view = [item.get("shopify") for item in normalized]
        self._logger.info("boeing shopify_view=%s", json.dumps(shopify_view, ensure_ascii=True))
        await self._store.insert_boeing_raw_data(search_query=query, raw_payload=payload, user_id=user_id)
        await self._store.upsert_product_staging(normalized, user_id=user_id)
        return normalized
