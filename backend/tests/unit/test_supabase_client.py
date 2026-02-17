"""
Unit tests for SupabaseClient — initialization, client property, storage bucket.

Tests cover:
- Constructor validates required URL and key settings
- Constructor raises RuntimeError when URL or key is missing
- get_client creates and caches a Supabase client singleton
- client property delegates to get_client
- storage_bucket property returns the configured bucket name
- get_supabase_client factory function returns a SupabaseClient instance

Version: 1.0.0
"""
import sys
from unittest.mock import MagicMock as _MagicMock

# Pre-mock supabase SDK modules that may be unavailable in test env
for _mod in (
    "supabase", "storage3", "storage3.utils",
    "storage3._async", "storage3._async.client", "storage3._async.analytics",
    "postgrest", "postgrest.exceptions",
    "pyiceberg", "pyiceberg.catalog", "pyiceberg.catalog.rest", "pyroaring",
):
    sys.modules.setdefault(_mod, _MagicMock())

import pytest
from unittest.mock import MagicMock, patch


@pytest.mark.unit
class TestSupabaseClientInit:
    """Verify SupabaseClient constructor validates settings."""

    def test_init_stores_url_and_key(self):
        from app.clients.supabase_client import SupabaseClient

        settings = MagicMock()
        settings.supabase_url = "https://test.supabase.co"
        settings.supabase_service_role_key = "test-key"
        settings.supabase_storage_bucket = "test-bucket"

        client = SupabaseClient(settings)

        assert client._url == "https://test.supabase.co"
        assert client._key == "test-key"
        assert client._bucket == "test-bucket"

    def test_init_raises_when_url_missing(self):
        from app.clients.supabase_client import SupabaseClient

        settings = MagicMock()
        settings.supabase_url = ""
        settings.supabase_service_role_key = "test-key"
        settings.supabase_storage_bucket = "test-bucket"

        with pytest.raises(RuntimeError, match="SUPABASE_URL"):
            SupabaseClient(settings)

    def test_init_raises_when_key_missing(self):
        from app.clients.supabase_client import SupabaseClient

        settings = MagicMock()
        settings.supabase_url = "https://test.supabase.co"
        settings.supabase_service_role_key = ""
        settings.supabase_storage_bucket = "test-bucket"

        with pytest.raises(RuntimeError, match="SUPABASE_SERVICE_ROLE_KEY"):
            SupabaseClient(settings)

    def test_init_raises_when_url_is_none(self):
        from app.clients.supabase_client import SupabaseClient

        settings = MagicMock()
        settings.supabase_url = None
        settings.supabase_service_role_key = "test-key"
        settings.supabase_storage_bucket = "test-bucket"

        with pytest.raises(RuntimeError):
            SupabaseClient(settings)

    def test_init_raises_when_key_is_none(self):
        from app.clients.supabase_client import SupabaseClient

        settings = MagicMock()
        settings.supabase_url = "https://test.supabase.co"
        settings.supabase_service_role_key = None
        settings.supabase_storage_bucket = "test-bucket"

        with pytest.raises(RuntimeError):
            SupabaseClient(settings)


@pytest.mark.unit
class TestGetClient:
    """Verify get_client creates and caches the Supabase SDK client."""

    def test_get_client_calls_create_client(self):
        from app.clients.supabase_client import SupabaseClient

        # Reset singleton for isolation
        SupabaseClient._instance = None

        settings = MagicMock()
        settings.supabase_url = "https://test.supabase.co"
        settings.supabase_service_role_key = "test-key"
        settings.supabase_storage_bucket = "test-bucket"

        mock_sdk_client = MagicMock()

        with patch("app.clients.supabase_client.create_client", return_value=mock_sdk_client) as mock_create:
            client = SupabaseClient(settings)
            result = client.get_client()

            mock_create.assert_called_once_with("https://test.supabase.co", "test-key")
            assert result is mock_sdk_client

        # Cleanup singleton
        SupabaseClient._instance = None

    def test_get_client_returns_cached_instance(self):
        from app.clients.supabase_client import SupabaseClient

        SupabaseClient._instance = None

        settings = MagicMock()
        settings.supabase_url = "https://test.supabase.co"
        settings.supabase_service_role_key = "test-key"
        settings.supabase_storage_bucket = "test-bucket"

        mock_sdk_client = MagicMock()

        with patch("app.clients.supabase_client.create_client", return_value=mock_sdk_client) as mock_create:
            client = SupabaseClient(settings)
            first = client.get_client()
            second = client.get_client()

            assert first is second
            mock_create.assert_called_once()

        SupabaseClient._instance = None


@pytest.mark.unit
class TestClientProperty:
    """Verify client property delegates to get_client."""

    def test_client_property_returns_sdk_client(self):
        from app.clients.supabase_client import SupabaseClient

        SupabaseClient._instance = None

        settings = MagicMock()
        settings.supabase_url = "https://test.supabase.co"
        settings.supabase_service_role_key = "test-key"
        settings.supabase_storage_bucket = "test-bucket"

        mock_sdk_client = MagicMock()

        with patch("app.clients.supabase_client.create_client", return_value=mock_sdk_client):
            client = SupabaseClient(settings)
            assert client.client is mock_sdk_client

        SupabaseClient._instance = None


@pytest.mark.unit
class TestStorageBucket:
    """Verify storage_bucket property returns configured bucket name."""

    def test_storage_bucket_returns_bucket(self):
        from app.clients.supabase_client import SupabaseClient

        settings = MagicMock()
        settings.supabase_url = "https://test.supabase.co"
        settings.supabase_service_role_key = "test-key"
        settings.supabase_storage_bucket = "my-images-bucket"

        client = SupabaseClient(settings)
        assert client.storage_bucket == "my-images-bucket"

    def test_storage_bucket_reflects_settings_value(self):
        from app.clients.supabase_client import SupabaseClient

        settings = MagicMock()
        settings.supabase_url = "https://test.supabase.co"
        settings.supabase_service_role_key = "test-key"
        settings.supabase_storage_bucket = "product-images"

        client = SupabaseClient(settings)
        assert client.storage_bucket == "product-images"


@pytest.mark.unit
class TestFactoryFunction:
    """Verify get_supabase_client factory function."""

    def test_factory_returns_supabase_client_instance(self):
        from app.clients.supabase_client import SupabaseClient, get_supabase_client

        settings = MagicMock()
        settings.supabase_url = "https://test.supabase.co"
        settings.supabase_service_role_key = "test-key"
        settings.supabase_storage_bucket = "test-bucket"

        result = get_supabase_client(settings)
        assert isinstance(result, SupabaseClient)
