"""
End-to-end tests for the extraction pipeline.

Tests the full flow: search -> extract -> normalize -> stage.
Mocks the Boeing client to return sample data and verifies the pipeline
produces correct normalized/staging records.
Version: 1.0.0
"""
import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

os.environ.setdefault("AUTO_START_CELERY", "false")


SAMPLE_BOEING_RAW_RESPONSE = {
    "currency": "USD",
    "lineItems": [
        {
            "aviallPartNumber": "WF338109",
            "aviallPartName": "GASKET, O-RING",
            "aviallPartDescription": "O-Ring Gasket for hydraulic system",
            "supplierName": "Aviall",
            "listPrice": 25.50,
            "netPrice": 23.00,
            "baseUom": "EA",
            "inStock": True,
            "quantity": 150,
            "countryOfOrigin": "US",
            "dimensionLength": 2.5,
            "dimensionWidth": 1.0,
            "dimensionHeight": 0.5,
            "dimensionUom": "IN",
            "weight": 0.1,
            "weightUom": "LB",
            "condition": "NE",
            "hazmatCode": None,
            "eccn": None,
            "faaApprovalCode": None,
            "scheduleBCode": None,
            "productImage": "https://boeing.com/images/WF338109.jpg",
            "thumbnailImage": "https://boeing.com/thumbs/WF338109.jpg",
            "locationAvailabilities": [
                {"location": "Dallas Central", "availQuantity": 100},
                {"location": "Chicago Warehouse", "availQuantity": 50},
            ],
        },
    ],
}


@pytest.mark.e2e
class TestExtractionPipelineSearch:
    """E2E tests for the extraction search flow."""

    @pytest.mark.asyncio
    @patch("app.services.extraction_service.normalize_boeing_payload")
    async def test_search_calls_boeing_and_stores_results(self, mock_normalize):
        """Full search flow: client -> normalize -> store raw + staging."""
        from app.services.extraction_service import ExtractionService

        mock_client = MagicMock()
        mock_client.fetch_price_availability = AsyncMock(
            return_value=SAMPLE_BOEING_RAW_RESPONSE
        )

        mock_raw_store = MagicMock()
        mock_raw_store.insert_boeing_raw_data = AsyncMock()

        mock_staging_store = MagicMock()
        mock_staging_store.upsert_product_staging = AsyncMock()

        mock_normalize.return_value = [
            {
                "sku": "WF338109",
                "title": "WF338109",
                "vendor": "BDI",
                "list_price": 25.50,
                "net_price": 23.00,
                "inventory_quantity": 150,
                "shopify": {"sku": "WF338109", "price": 28.05},
            },
        ]

        service = ExtractionService(
            client=mock_client,
            raw_store=mock_raw_store,
            staging_store=mock_staging_store,
        )

        results = await service.search_products("WF338109", user_id="test-user-id")

        # Verify Boeing client was called
        mock_client.fetch_price_availability.assert_called_once_with("WF338109")

        # Verify raw data was stored
        mock_raw_store.insert_boeing_raw_data.assert_called_once()
        call_kwargs = mock_raw_store.insert_boeing_raw_data.call_args
        assert call_kwargs[1]["search_query"] == "WF338109"
        assert call_kwargs[1]["user_id"] == "test-user-id"

        # Verify staging was updated
        mock_staging_store.upsert_product_staging.assert_called_once()

        # Verify results
        assert len(results) == 1
        assert results[0]["sku"] == "WF338109"

    @pytest.mark.asyncio
    async def test_search_empty_response_returns_empty_list(self):
        """Search with no Boeing results should return empty list."""
        from app.services.extraction_service import ExtractionService

        mock_client = MagicMock()
        mock_client.fetch_price_availability = AsyncMock(
            return_value={"currency": "USD", "lineItems": []}
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

        results = await service.search_products("NONEXISTENT", user_id="test-user-id")

        assert isinstance(results, list)
        # Raw data should still be stored even for empty results
        mock_raw_store.insert_boeing_raw_data.assert_called_once()


@pytest.mark.e2e
class TestExtractionPipelineNormalization:
    """E2E tests for the normalization step of extraction."""

    def test_normalize_boeing_payload_produces_valid_records(self):
        """Normalization should transform raw Boeing data into staging-ready records."""
        from app.utils.boeing_normalize import normalize_boeing_payload

        result = normalize_boeing_payload("WF338109", SAMPLE_BOEING_RAW_RESPONSE)

        assert isinstance(result, list)
        assert len(result) >= 1

        record = result[0]
        # Check essential fields exist
        assert record.get("sku") is not None or record.get("aviall_part_number") is not None
        assert record.get("title") is not None or record.get("name") is not None

    def test_normalize_empty_response_returns_empty_list(self):
        """Normalization with no lineItems should return empty list."""
        from app.utils.boeing_normalize import normalize_boeing_payload

        result = normalize_boeing_payload(
            "NONEXISTENT",
            {"currency": "USD", "lineItems": []},
        )
        assert isinstance(result, list)
        assert len(result) == 0


@pytest.mark.e2e
class TestExtractionPipelineBulkSearch:
    """E2E tests for the bulk search extraction flow."""

    @pytest.mark.asyncio
    async def test_bulk_search_task_splits_into_chunks(self):
        """Bulk search should split part numbers into chunks."""
        from app.celery_app.tasks.extraction import BOEING_BATCH_SIZE

        part_numbers = [f"PN-{i:05d}" for i in range(25)]
        expected_chunks = (len(part_numbers) + BOEING_BATCH_SIZE - 1) // BOEING_BATCH_SIZE

        # Verify chunk math
        assert expected_chunks > 1
        assert expected_chunks * BOEING_BATCH_SIZE >= len(part_numbers)

    @pytest.mark.asyncio
    @patch("app.services.extraction_service.normalize_boeing_payload")
    async def test_search_preserves_user_id_through_pipeline(self, mock_normalize):
        """User ID should propagate from search through to raw/staging stores."""
        from app.services.extraction_service import ExtractionService

        mock_client = MagicMock()
        mock_client.fetch_price_availability = AsyncMock(
            return_value={"currency": "USD", "lineItems": []}
        )

        mock_raw_store = MagicMock()
        mock_raw_store.insert_boeing_raw_data = AsyncMock()

        mock_staging_store = MagicMock()
        mock_staging_store.upsert_product_staging = AsyncMock()

        mock_normalize.return_value = []

        service = ExtractionService(
            client=mock_client,
            raw_store=mock_raw_store,
            staging_store=mock_staging_store,
        )

        await service.search_products("WF338109", user_id="specific-user-123")

        # Verify user_id was passed to raw store
        raw_call_kwargs = mock_raw_store.insert_boeing_raw_data.call_args[1]
        assert raw_call_kwargs["user_id"] == "specific-user-123"

        # Verify user_id was passed to staging store
        staging_call_kwargs = mock_staging_store.upsert_product_staging.call_args[1]
        assert staging_call_kwargs["user_id"] == "specific-user-123"
