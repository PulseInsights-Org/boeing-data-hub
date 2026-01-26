import logging
from typing import Any

from supabase import create_client, Client

from app.core.config import Settings

logger = logging.getLogger("supabase_client")


class SupabaseClient:
    """Supabase client wrapper using the official supabase-py SDK."""

    _instance: Client | None = None

    def __init__(self, settings: Settings) -> None:
        self._url = settings.supabase_url
        self._key = settings.supabase_service_role_key
        self._bucket = settings.supabase_storage_bucket

        if not self._url or not self._key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set for Supabase access"
            )

    def get_client(self) -> Client:
        """Get or create the Supabase client instance."""
        if SupabaseClient._instance is None:
            # Simple client creation - let supabase SDK use defaults
            SupabaseClient._instance = create_client(
                self._url,
                self._key,
            )
            logger.info("supabase client initialized url=%s", self._url)
        return SupabaseClient._instance

    @property
    def client(self) -> Client:
        """Property accessor for the Supabase client."""
        return self.get_client()

    @property
    def storage_bucket(self) -> str:
        """Get the configured storage bucket name."""
        return self._bucket


def get_supabase_client(settings: Settings) -> SupabaseClient:
    """Factory function to create a SupabaseClient instance."""
    return SupabaseClient(settings)
