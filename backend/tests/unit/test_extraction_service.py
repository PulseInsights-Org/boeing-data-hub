"""
Unit tests for ExtractionService — Boeing search, extract, and staging pipeline.

Tests cover:
- search_products calls BoeingClient.fetch_price_availability
- search_products passes query to normalize_boeing_payload
- search_products stores raw data in RawDataStore
- search_products upserts normalized data into StagingStore
- search_products returns normalized result list
- search_products uses correct user_id for storage
- search_products handles empty Boeing API response
- search_products propagates BoeingClient errors
- search_products propagates RawDataStore errors
- search_products propagates StagingStore errors

Version: 1.0.0
"""
import sys
from unittest.mock import MagicMock as _MagicMock

for _mod in (
    "supabase", "storage3", "storage3.utils",
    "storage3._async", "storage3._async.client", "storage3._async.analytics",
    "postgrest", "postgrest.exceptions",
    "pyiceberg", "pyiceberg.catalog", "pyiceberg.catalog.rest", "pyroaring",
):
    sys.modules.setdefault(_mod, _MagicMock())

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from app.services.extraction_service import ExtractionService


def _make_service(
    fetch_return=None,
    normalize_return=None,
):
    """Create an ExtractionService with mocked dependencies."""
    mock_client = MagicMock()
    mock_client.fetch_price_availability = AsyncMock(
        return_value=fetch_return or {}
    )

    mock_raw_store = MagicMock()
    mock_raw_store.insert_boeing_raw_data = AsyncMock()

    mock_staging_store = MagicMock()
    mock_staging_store.upsert_product_staging = AsyncMock()

    service = ExtractionService(
        client=mock_client,
        raw_store=mock_raw_store,
        staging_store=mock_staging_store,
    )
    return service, mock_client, mock_raw_store, mock_staging_store


@pytest.mark.unit
class TestSearchProducts:
    """Verify search_products orchestrates fetch, normalize, and storage."""

    @pytest.mark.asyncio
    @patch("app.services.extraction_service.normalize_boeing_payload")
    async def test_calls_fetch_price_availability(self, mock_normalize):
        mock_normalize.return_value = []
        svc, mock_client, _, _ = _make_service(fetch_return={"lineItems": []})

        await svc.search_products("WF338109")

        mock_client.fetch_price_availability.assert_awaited_once_with("WF338109")

    @pytest.mark.asyncio
    @patch("app.services.extraction_service.normalize_boeing_payload")
    async def test_passes_query_and_payload_to_normalizer(self, mock_normalize):
        raw_payload = {"lineItems": [{"aviallPartNumber": "WF338109"}]}
        mock_normalize.return_value = [{"sku": "WF338109", "shopify": {"sku": "WF338109"}}]
        svc, _, _, _ = _make_service(fetch_return=raw_payload)

        await svc.search_products("WF338109")

        mock_normalize.assert_called_once_with("WF338109", raw_payload)

    @pytest.mark.asyncio
    @patch("app.services.extraction_service.normalize_boeing_payload")
    async def test_inserts_raw_data(self, mock_normalize):
        raw_payload = {"lineItems": []}
        mock_normalize.return_value = []
        svc, _, mock_raw_store, _ = _make_service(fetch_return=raw_payload)

        await svc.search_products("WF338109", user_id="user-42")

        mock_raw_store.insert_boeing_raw_data.assert_awaited_once_with(
            search_query="WF338109",
            raw_payload=raw_payload,
            user_id="user-42",
        )

    @pytest.mark.asyncio
    @patch("app.services.extraction_service.normalize_boeing_payload")
    async def test_upserts_normalized_to_staging(self, mock_normalize):
        normalized = [
            {"sku": "WF338109", "shopify": {"sku": "WF338109", "price": 28.05}},
        ]
        mock_normalize.return_value = normalized
        svc, _, _, mock_staging_store = _make_service(fetch_return={"lineItems": []})

        await svc.search_products("WF338109", user_id="user-42")

        mock_staging_store.upsert_product_staging.assert_awaited_once_with(
            normalized, user_id="user-42"
        )

    @pytest.mark.asyncio
    @patch("app.services.extraction_service.normalize_boeing_payload")
    async def test_returns_normalized_result(self, mock_normalize):
        normalized = [
            {"sku": "WF338109", "shopify": {"sku": "WF338109"}},
            {"sku": "AN3-12A", "shopify": {"sku": "AN3-12A"}},
        ]
        mock_normalize.return_value = normalized
        svc, _, _, _ = _make_service(fetch_return={"lineItems": []})

        result = await svc.search_products("WF338109")

        assert result == normalized
        assert len(result) == 2

    @pytest.mark.asyncio
    @patch("app.services.extraction_service.normalize_boeing_payload")
    async def test_default_user_id_is_system(self, mock_normalize):
        mock_normalize.return_value = []
        svc, _, mock_raw_store, mock_staging_store = _make_service(
            fetch_return={"lineItems": []}
        )

        await svc.search_products("WF338109")

        # Should default to "system" for user_id
        raw_call = mock_raw_store.insert_boeing_raw_data.call_args
        assert raw_call.kwargs["user_id"] == "system"

        staging_call = mock_staging_store.upsert_product_staging.call_args
        assert staging_call.kwargs["user_id"] == "system"

    @pytest.mark.asyncio
    @patch("app.services.extraction_service.normalize_boeing_payload")
    async def test_handles_empty_boeing_response(self, mock_normalize):
        mock_normalize.return_value = []
        svc, _, _, _ = _make_service(fetch_return={})

        result = await svc.search_products("NONEXISTENT")

        assert result == []

    @pytest.mark.asyncio
    async def test_propagates_boeing_client_error(self):
        svc, mock_client, _, _ = _make_service()
        mock_client.fetch_price_availability = AsyncMock(
            side_effect=Exception("Boeing API timeout")
        )

        with pytest.raises(Exception, match="Boeing API timeout"):
            await svc.search_products("WF338109")

    @pytest.mark.asyncio
    @patch("app.services.extraction_service.normalize_boeing_payload")
    async def test_propagates_raw_store_error(self, mock_normalize):
        mock_normalize.return_value = []
        svc, _, mock_raw_store, _ = _make_service(fetch_return={"lineItems": []})
        mock_raw_store.insert_boeing_raw_data = AsyncMock(
            side_effect=Exception("DB write failed")
        )

        with pytest.raises(Exception, match="DB write failed"):
            await svc.search_products("WF338109")

    @pytest.mark.asyncio
    @patch("app.services.extraction_service.normalize_boeing_payload")
    async def test_propagates_staging_store_error(self, mock_normalize):
        normalized = [{"sku": "WF338109", "shopify": {"sku": "WF338109"}}]
        mock_normalize.return_value = normalized
        svc, _, _, mock_staging_store = _make_service(fetch_return={"lineItems": []})
        mock_staging_store.upsert_product_staging = AsyncMock(
            side_effect=Exception("Staging upsert failed")
        )

        with pytest.raises(Exception, match="Staging upsert failed"):
            await svc.search_products("WF338109")

    @pytest.mark.asyncio
    @patch("app.services.extraction_service.normalize_boeing_payload")
    async def test_shopify_view_extracted_from_normalized(self, mock_normalize):
        """Verify the shopify_view is built from 'shopify' key of each item."""
        normalized = [
            {"sku": "A", "shopify": {"sku": "A", "price": 10.0}},
            {"sku": "B", "shopify": {"sku": "B", "price": 20.0}},
        ]
        mock_normalize.return_value = normalized
        svc, _, _, _ = _make_service(fetch_return={"lineItems": []})

        result = await svc.search_products("A,B")

        # Service doesn't return shopify_view but uses it for logging.
        # We verify the result is the full normalized list.
        assert len(result) == 2
        assert result[0]["shopify"]["price"] == 10.0
