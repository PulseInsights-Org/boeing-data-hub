"""
Raw data store — Boeing API response storage.

Raw data store – boeing_raw_data table operations.
Version: 1.0.0
"""

import logging
from typing import Any, Dict

from app.db.base_store import BaseStore

logger = logging.getLogger("raw_data_store")


class RawDataStore(BaseStore):
    """CRUD for the boeing_raw_data table."""

    async def insert_boeing_raw_data(
        self, search_query: str, raw_payload: Dict[str, Any], user_id: str = "system"
    ) -> None:
        row = {
            "search_query": search_query,
            "raw_payload": raw_payload,
            "user_id": user_id,
        }
        await self._insert("boeing_raw_data", [row])
