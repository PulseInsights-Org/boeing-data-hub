"""
Unit tests for ShopifyClient HTTP transport layer.

Tests domain normalization, GID conversion, REST/GraphQL calls,
error handling, and delete delegation.

Version: 1.0.0
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import HTTPException

from app.clients.shopify_client import ShopifyClient


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(
    domain: str = "test-store.myshopify.com",
    token: str = "shpat_test",
    version: str = "2024-10",
) -> ShopifyClient:
    """Create a ShopifyClient with mock settings."""
    settings = MagicMock()
    settings.shopify_store_domain = domain
    settings.shopify_admin_api_token = token
    settings.shopify_api_version = version
    return ShopifyClient(settings)


# ---------------------------------------------------------------------------
# _normalize_store_domain
# ---------------------------------------------------------------------------

class TestNormalizeStoreDomain:
    """Tests for ShopifyClient._normalize_store_domain."""

    def test_none_returns_none(self):
        assert ShopifyClient._normalize_store_domain(None) is None

    def test_empty_string_returns_empty(self):
        assert ShopifyClient._normalize_store_domain("") == ""

    def test_bare_domain_appends_myshopify(self):
        result = ShopifyClient._normalize_store_domain("test-store")
        assert result == "test-store.myshopify.com"

    def test_full_https_url_stripped(self):
        result = ShopifyClient._normalize_store_domain("https://test-store.myshopify.com")
        assert result == "test-store.myshopify.com"

    def test_http_url_stripped(self):
        result = ShopifyClient._normalize_store_domain("http://test-store.myshopify.com/")
        assert result == "test-store.myshopify.com"

    def test_already_myshopify_untouched(self):
        result = ShopifyClient._normalize_store_domain("test-store.myshopify.com")
        assert result == "test-store.myshopify.com"

    def test_trailing_slash_removed(self):
        result = ShopifyClient._normalize_store_domain("test-store.myshopify.com/")
        assert result == "test-store.myshopify.com"

    def test_custom_domain_appends_myshopify(self):
        result = ShopifyClient._normalize_store_domain("my-store.example.com")
        assert result == "my-store.example.com.myshopify.com"


# ---------------------------------------------------------------------------
# to_gid
# ---------------------------------------------------------------------------

class TestToGid:
    """Tests for ShopifyClient.to_gid conversion."""

    def test_numeric_id_converted(self):
        client = _make_client()
        assert client.to_gid("Product", 12345) == "gid://shopify/Product/12345"

    def test_string_numeric_id_converted(self):
        client = _make_client()
        assert client.to_gid("Variant", "67890") == "gid://shopify/Variant/67890"

    def test_already_gid_returned_unchanged(self):
        client = _make_client()
        gid = "gid://shopify/Product/12345"
        assert client.to_gid("Product", gid) == gid

    def test_different_entities(self):
        client = _make_client()
        assert "InventoryItem" in client.to_gid("InventoryItem", 1)
        assert "Location" in client.to_gid("Location", 2)


# ---------------------------------------------------------------------------
# call_shopify
# ---------------------------------------------------------------------------

class TestCallShopify:
    """Tests for ShopifyClient.call_shopify REST calls."""

    @pytest.mark.asyncio
    async def test_successful_get(self):
        client = _make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"products": []}'
        mock_response.json.return_value = {"products": []}

        with patch("app.clients.shopify_client.httpx.AsyncClient") as MockAsyncClient:
            mock_ctx = AsyncMock()
            mock_ctx.request = AsyncMock(return_value=mock_response)
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await client.call_shopify("GET", "/products.json")
            assert result == {"products": []}

    @pytest.mark.asyncio
    async def test_http_error_raises_exception(self):
        client = _make_client()
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Rate limited"

        with patch("app.clients.shopify_client.httpx.AsyncClient") as MockAsyncClient:
            mock_ctx = AsyncMock()
            mock_ctx.request = AsyncMock(return_value=mock_response)
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(HTTPException) as exc_info:
                await client.call_shopify("GET", "/products.json")
            assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_empty_body_returns_empty_dict(self):
        client = _make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = ""

        with patch("app.clients.shopify_client.httpx.AsyncClient") as MockAsyncClient:
            mock_ctx = AsyncMock()
            mock_ctx.request = AsyncMock(return_value=mock_response)
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await client.call_shopify("DELETE", "/products/1.json")
            assert result == {}


# ---------------------------------------------------------------------------
# call_shopify_graphql
# ---------------------------------------------------------------------------

class TestCallShopifyGraphQL:
    """Tests for ShopifyClient.call_shopify_graphql error handling."""

    @pytest.mark.asyncio
    async def test_graphql_errors_raise_502(self):
        client = _make_client()
        client.call_shopify = AsyncMock(return_value={
            "errors": [{"message": "internal error"}]
        })

        with pytest.raises(HTTPException) as exc_info:
            await client.call_shopify_graphql("query { shop { name } }")
        assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    async def test_graphql_success(self):
        client = _make_client()
        expected = {"data": {"shop": {"name": "TestShop"}}}
        client.call_shopify = AsyncMock(return_value=expected)

        result = await client.call_shopify_graphql("query { shop { name } }")
        assert result == expected


# ---------------------------------------------------------------------------
# delete_product
# ---------------------------------------------------------------------------

class TestDeleteProduct:
    """Tests for ShopifyClient.delete_product."""

    @pytest.mark.asyncio
    async def test_delete_returns_true(self):
        client = _make_client()
        client.call_shopify = AsyncMock(return_value={})

        result = await client.delete_product(12345)
        assert result is True
        client.call_shopify.assert_called_once_with("DELETE", "/products/12345.json")

    @pytest.mark.asyncio
    async def test_delete_with_string_id(self):
        client = _make_client()
        client.call_shopify = AsyncMock(return_value={})

        result = await client.delete_product("67890")
        assert result is True
        client.call_shopify.assert_called_once_with("DELETE", "/products/67890.json")


# ---------------------------------------------------------------------------
# _base_url
# ---------------------------------------------------------------------------

class TestBaseUrl:
    """Tests for ShopifyClient._base_url."""

    def test_base_url_format(self):
        client = _make_client()
        url = client._base_url()
        assert url == "https://test-store.myshopify.com/admin/api/2024-10"

    def test_missing_domain_raises(self):
        client = _make_client(domain="")
        with pytest.raises(HTTPException) as exc_info:
            client._base_url()
        assert exc_info.value.status_code == 500

    def test_missing_token_raises(self):
        client = _make_client(token="")
        with pytest.raises(HTTPException) as exc_info:
            client._base_url()
        assert exc_info.value.status_code == 500
