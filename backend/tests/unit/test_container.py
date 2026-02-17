"""
Unit tests for the lazy DI container.
Version: 1.0.0
"""
import pytest
from unittest.mock import patch, MagicMock


@pytest.mark.unit
class TestContainer:
    """Tests for container.py DI factory functions."""

    def test_get_shopify_client_returns_instance(self):
        """get_shopify_client returns a ShopifyClient."""
        from app.container import get_shopify_client
        # Clear lru_cache to get fresh instance
        get_shopify_client.cache_clear()
        with patch("app.container.settings") as mock_settings:
            mock_settings.shopify_store_domain = "test.myshopify.com"
            mock_settings.shopify_admin_api_token = "token"
            mock_settings.shopify_api_version = "2024-10"
            result = get_shopify_client()
            assert result is not None
            get_shopify_client.cache_clear()

    def test_get_boeing_client_returns_instance(self):
        """get_boeing_client returns a BoeingClient."""
        from app.container import get_boeing_client
        get_boeing_client.cache_clear()
        with patch("app.container.settings") as mock_settings:
            mock_settings.boeing_oauth_token_url = "https://test.com/token"
            mock_settings.boeing_client_id = "id"
            mock_settings.boeing_client_secret = "secret"
            mock_settings.boeing_scope = "scope"
            mock_settings.boeing_pna_oauth_url = "https://test.com/pna"
            mock_settings.boeing_pna_price_url = "https://test.com/price"
            mock_settings.boeing_search_url = "https://test.com/search"
            mock_settings.boeing_detail_url = "https://test.com/detail"
            result = get_boeing_client()
            assert result is not None
            get_boeing_client.cache_clear()

    def test_singleton_behavior(self):
        """Calling same getter twice returns identical instance."""
        from app.container import get_shopify_client
        get_shopify_client.cache_clear()
        with patch("app.container.settings") as mock_settings:
            mock_settings.shopify_store_domain = "test.myshopify.com"
            mock_settings.shopify_admin_api_token = "token"
            mock_settings.shopify_api_version = "2024-10"
            a = get_shopify_client()
            b = get_shopify_client()
            assert a is b
            get_shopify_client.cache_clear()

    def test_get_shopify_orchestrator_returns_instance(self):
        """get_shopify_orchestrator builds the full dependency chain."""
        from app.container import get_shopify_orchestrator
        get_shopify_orchestrator.cache_clear()
        with patch("app.container.settings") as mock_settings:
            mock_settings.shopify_store_domain = "test.myshopify.com"
            mock_settings.shopify_admin_api_token = "token"
            mock_settings.shopify_api_version = "2024-10"
            mock_settings.shopify_location_map = {}
            result = get_shopify_orchestrator()
            assert result is not None
            get_shopify_orchestrator.cache_clear()

    def test_get_shopify_inventory_returns_instance(self):
        """get_shopify_inventory returns a ShopifyInventoryService."""
        from app.container import get_shopify_inventory
        get_shopify_inventory.cache_clear()
        with patch("app.container.settings") as mock_settings:
            mock_settings.shopify_store_domain = "test.myshopify.com"
            mock_settings.shopify_admin_api_token = "token"
            mock_settings.shopify_api_version = "2024-10"
            mock_settings.shopify_location_map = {}
            result = get_shopify_inventory()
            assert result is not None
            get_shopify_inventory.cache_clear()
