"""
Extraction service — Boeing search, extract, and staging pipeline.

Extraction service – Boeing product search + raw-data storage.

Replaces: services/boeing_service.py
Version: 1.0.0
"""
import json
import logging
from typing import Any, Dict, List

from app.clients.boeing_client import BoeingClient
from app.db.raw_data_store import RawDataStore
from app.db.staging_store import StagingStore
from app.utils.boeing_normalize import normalize_boeing_payload


class ExtractionService:
    def __init__(
        self,
        client: BoeingClient,
        raw_store: RawDataStore,
        staging_store: StagingStore,
    ) -> None:
        self._client = client
        self._raw_store = raw_store
        self._staging_store = staging_store
        self._logger = logging.getLogger("extraction_service")

    async def search_products(
        self, query: str, user_id: str = "system"
    ) -> List[Dict[str, Any]]:
        """Search Boeing API, normalize results, and store in staging."""
        payload = await self._client.fetch_price_availability(query)
        self._logger.info(
            "boeing raw response=%s", json.dumps(payload, ensure_ascii=True)
        )

        normalized = normalize_boeing_payload(query, payload)
        self._logger.info(
            "boeing normalized=%s", json.dumps(normalized, ensure_ascii=True)
        )

        shopify_view = [item.get("shopify") for item in normalized]
        self._logger.info(
            "boeing shopify_view=%s", json.dumps(shopify_view, ensure_ascii=True)
        )

        await self._raw_store.insert_boeing_raw_data(
            search_query=query, raw_payload=payload, user_id=user_id
        )
        await self._staging_store.upsert_product_staging(
            normalized, user_id=user_id
        )
        return normalized
