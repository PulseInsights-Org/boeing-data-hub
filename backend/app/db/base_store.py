"""
Base store — shared Supabase client access for all stores.

Base Supabase store with shared CRUD helpers.

All domain-specific stores inherit from this class to get
standardised insert / upsert / select / update primitives.
Version: 1.0.0
"""

import logging
from typing import Any, Dict, List

from fastapi import HTTPException
from postgrest.exceptions import APIError

from app.core.config import settings
from app.clients.supabase_client import SupabaseClient

logger = logging.getLogger("base_store")


class BaseStore:
    """Base class for all Supabase stores providing shared CRUD operations."""

    def __init__(self, supabase_client: SupabaseClient | None = None) -> None:
        self._supabase_client = supabase_client or SupabaseClient(settings)

    @property
    def _client(self):
        """Get the Supabase client instance."""
        return self._supabase_client.client

    @property
    def _storage_url(self) -> str:
        return settings.supabase_url.rstrip("/") + "/storage/v1"

    @property
    def _bucket(self) -> str:
        return settings.supabase_storage_bucket

    async def _insert(self, table: str, rows: List[Dict[str, Any]]) -> None:
        """Insert rows into a table."""
        if not rows:
            return
        try:
            self._client.table(table).insert(rows).execute()
        except APIError as e:
            logger.info("supabase error table=%s detail=%s", table, str(e))
            raise HTTPException(
                status_code=500,
                detail=f"Supabase insert into {table} failed: {e}",
            )

    async def _upsert(
        self, table: str, rows: List[Dict[str, Any]], on_conflict: str | None = None
    ) -> None:
        """Upsert rows into a table (insert or update on conflict)."""
        if not rows:
            return
        try:
            if on_conflict:
                self._client.table(table).upsert(rows, on_conflict=on_conflict).execute()
            else:
                self._client.table(table).upsert(rows).execute()
        except APIError as e:
            logger.info("supabase error table=%s detail=%s", table, str(e))
            raise HTTPException(
                status_code=500,
                detail=f"Supabase upsert into {table} failed: {e}",
            )

    async def _select(
        self, table: str, columns: str = "*", filters: Dict[str, Any] | None = None
    ) -> List[Dict[str, Any]]:
        """Select rows from a table with optional filters."""
        try:
            query = self._client.table(table).select(columns)
            if filters:
                for key, value in filters.items():
                    query = query.eq(key, value)
            response = query.execute()
            return response.data or []
        except APIError as e:
            logger.info("supabase error table=%s detail=%s", table, str(e))
            raise HTTPException(
                status_code=500,
                detail=f"Supabase select from {table} failed: {e}",
            )

    async def _update(
        self, table: str, filters: Dict[str, Any], payload: Dict[str, Any]
    ) -> None:
        """Update rows in a table matching the filters."""
        try:
            query = self._client.table(table).update(payload)
            for key, value in filters.items():
                query = query.eq(key, value)
            query.execute()
        except APIError as e:
            logger.info("supabase error table=%s detail=%s", table, str(e))
            raise HTTPException(
                status_code=500,
                detail=f"Supabase update {table} failed: {e}",
            )
