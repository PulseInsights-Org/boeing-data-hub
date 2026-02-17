"""
Unit tests for BoeingClient — OAuth authentication and REST API calls.

Tests cover:
- Client initialization from settings
- OAuth token retrieval (_get_oauth_access_token)
- Part access token retrieval (_get_part_access_token)
- Single part price/availability fetch
- Batch part price/availability fetch
- Error handling for missing credentials and failed HTTP calls

Version: 1.0.0
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import HTTPException

from app.clients.boeing_client import BoeingClient


@pytest.fixture
def boeing_settings():
    """Minimal settings object for BoeingClient."""
    s = MagicMock()
    s.boeing_oauth_token_url = "https://api.boeing.test/oauth2/token"
    s.boeing_client_id = "test-client-id"
    s.boeing_client_secret = "test-client-secret"
    s.boeing_scope = "api://test/.default"
    s.boeing_pna_oauth_url = "https://api.boeing.test/pna/oauth"
    s.boeing_pna_price_url = "https://api.boeing.test/pna/price"
    s.boeing_username = "test-user"
    s.boeing_password = "test-pass"
    return s


@pytest.fixture
def client(boeing_settings):
    """Construct a BoeingClient with test settings."""
    return BoeingClient(boeing_settings)


# --------------------------------------------------------------------------
# Initialization
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestBoeingClientInit:
    """Verify constructor stores every setting attribute."""

    def test_init_stores_oauth_url(self, client, boeing_settings):
        assert client._oauth_token_url == boeing_settings.boeing_oauth_token_url

    def test_init_stores_client_credentials(self, client, boeing_settings):
        assert client._client_id == boeing_settings.boeing_client_id
        assert client._client_secret == boeing_settings.boeing_client_secret

    def test_init_stores_scope(self, client, boeing_settings):
        assert client._scope == boeing_settings.boeing_scope

    def test_init_stores_pna_urls(self, client, boeing_settings):
        assert client._pna_oauth_url == boeing_settings.boeing_pna_oauth_url
        assert client._pna_price_url == boeing_settings.boeing_pna_price_url

    def test_init_stores_username_password(self, client, boeing_settings):
        assert client._username == boeing_settings.boeing_username
        assert client._password == boeing_settings.boeing_password


# --------------------------------------------------------------------------
# _get_oauth_access_token
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestGetOAuthAccessToken:
    """Verify OAuth token retrieval logic."""

    @pytest.mark.asyncio
    async def test_returns_token_on_success(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "oauth-token-123"}

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_response

        with patch("app.clients.boeing_client.httpx.AsyncClient") as MockAsyncClient:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__.return_value = mock_http
            MockAsyncClient.return_value = mock_ctx

            token = await client._get_oauth_access_token()

        assert token == "oauth-token-123"
        mock_http.post.assert_called_once()
        call_args = mock_http.post.call_args
        assert call_args[0][0] == client._oauth_token_url

    @pytest.mark.asyncio
    async def test_raises_on_missing_credentials(self, boeing_settings):
        boeing_settings.boeing_client_id = ""
        boeing_settings.boeing_client_secret = ""
        bad_client = BoeingClient(boeing_settings)

        with pytest.raises(HTTPException) as exc_info:
            await bad_client._get_oauth_access_token()
        assert exc_info.value.status_code == 500
        assert "BOEING_CLIENT_ID" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_raises_on_non_200_response(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_response

        with patch("app.clients.boeing_client.httpx.AsyncClient") as MockAsyncClient:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__.return_value = mock_http
            MockAsyncClient.return_value = mock_ctx

            with pytest.raises(HTTPException) as exc_info:
                await client._get_oauth_access_token()
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_raises_when_no_access_token_in_body(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"something_else": "value"}

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_response

        with patch("app.clients.boeing_client.httpx.AsyncClient") as MockAsyncClient:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__.return_value = mock_http
            MockAsyncClient.return_value = mock_ctx

            with pytest.raises(HTTPException) as exc_info:
                await client._get_oauth_access_token()
            assert exc_info.value.status_code == 500
            assert "No access_token" in exc_info.value.detail


# --------------------------------------------------------------------------
# _get_part_access_token
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestGetPartAccessToken:
    """Verify PNA part token retrieval."""

    @pytest.mark.asyncio
    async def test_returns_token_from_header(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"x-part-access-token": "part-token-456"}

        mock_http = AsyncMock()
        mock_http.get.return_value = mock_response

        with patch("app.clients.boeing_client.httpx.AsyncClient") as MockAsyncClient:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__.return_value = mock_http
            MockAsyncClient.return_value = mock_ctx

            token = await client._get_part_access_token("oauth-token-123")

        assert token == "part-token-456"
        mock_http.get.assert_called_once()
        call_args = mock_http.get.call_args
        assert call_args[0][0] == client._pna_oauth_url
        assert call_args[1]["headers"]["Authorization"] == "Bearer oauth-token-123"

    @pytest.mark.asyncio
    async def test_falls_back_to_json_body(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.json.return_value = {"x-part-access-token": "body-token-789"}

        mock_http = AsyncMock()
        mock_http.get.return_value = mock_response

        with patch("app.clients.boeing_client.httpx.AsyncClient") as MockAsyncClient:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__.return_value = mock_http
            MockAsyncClient.return_value = mock_ctx

            token = await client._get_part_access_token("oauth-token-123")

        assert token == "body-token-789"

    @pytest.mark.asyncio
    async def test_raises_on_missing_username_password(self, boeing_settings):
        boeing_settings.boeing_username = ""
        boeing_settings.boeing_password = ""
        bad_client = BoeingClient(boeing_settings)

        with pytest.raises(HTTPException) as exc_info:
            await bad_client._get_part_access_token("some-token")
        assert exc_info.value.status_code == 500
        assert "BOEING_USERNAME" in exc_info.value.detail


# --------------------------------------------------------------------------
# fetch_price_availability
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestFetchPriceAvailability:
    """Verify single-query price and availability fetch."""

    @pytest.mark.asyncio
    async def test_success_returns_json(self, client):
        expected = {"lineItems": [{"aviallPartNumber": "WF338109"}]}

        with patch.object(client, "_get_oauth_access_token", new_callable=AsyncMock) as mock_oauth, \
             patch.object(client, "_get_part_access_token", new_callable=AsyncMock) as mock_part:
            mock_oauth.return_value = "oauth-tok"
            mock_part.return_value = "part-tok"

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = expected

            mock_http = AsyncMock()
            mock_http.post.return_value = mock_response

            with patch("app.clients.boeing_client.httpx.AsyncClient") as MockAsyncClient:
                mock_ctx = AsyncMock()
                mock_ctx.__aenter__.return_value = mock_http
                MockAsyncClient.return_value = mock_ctx

                result = await client.fetch_price_availability("WF338109")

        assert result == expected
        mock_http.post.assert_called_once()
        call_args = mock_http.post.call_args
        assert call_args[0][0] == client._pna_price_url
        assert call_args[1]["json"]["productCodes"] == ["WF338109"]

    @pytest.mark.asyncio
    async def test_raises_on_api_error(self, client):
        with patch.object(client, "_get_oauth_access_token", new_callable=AsyncMock) as mock_oauth, \
             patch.object(client, "_get_part_access_token", new_callable=AsyncMock) as mock_part:
            mock_oauth.return_value = "oauth-tok"
            mock_part.return_value = "part-tok"

            mock_response = MagicMock()
            mock_response.status_code = 503
            mock_response.text = "Service Unavailable"

            mock_http = AsyncMock()
            mock_http.post.return_value = mock_response

            with patch("app.clients.boeing_client.httpx.AsyncClient") as MockAsyncClient:
                mock_ctx = AsyncMock()
                mock_ctx.__aenter__.return_value = mock_http
                MockAsyncClient.return_value = mock_ctx

                with pytest.raises(HTTPException) as exc_info:
                    await client.fetch_price_availability("BAD-PART")
                assert exc_info.value.status_code == 503


# --------------------------------------------------------------------------
# fetch_price_availability_batch
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestFetchPriceAvailabilityBatch:
    """Verify batch price and availability fetch."""

    @pytest.mark.asyncio
    async def test_batch_sends_multiple_product_codes(self, client):
        expected = {"lineItems": [{"aviallPartNumber": "A"}, {"aviallPartNumber": "B"}]}
        parts = ["A", "B"]

        with patch.object(client, "_get_oauth_access_token", new_callable=AsyncMock) as mock_oauth, \
             patch.object(client, "_get_part_access_token", new_callable=AsyncMock) as mock_part:
            mock_oauth.return_value = "oauth-tok"
            mock_part.return_value = "part-tok"

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = expected

            mock_http = AsyncMock()
            mock_http.post.return_value = mock_response

            with patch("app.clients.boeing_client.httpx.AsyncClient") as MockAsyncClient:
                mock_ctx = AsyncMock()
                mock_ctx.__aenter__.return_value = mock_http
                MockAsyncClient.return_value = mock_ctx

                result = await client.fetch_price_availability_batch(parts)

        assert result == expected
        call_args = mock_http.post.call_args
        assert call_args[1]["json"]["productCodes"] == parts
        # Batch uses 60s timeout
        MockAsyncClient.assert_called_once_with(timeout=60.0)
