"""
Unit tests for StagingStore — normalized product staging CRUD.

Tests cover:
- upsert_product_staging builds rows and delegates to _upsert
- get_product_staging_by_part_number queries by sku then falls back to id
- update_product_staging_shopify_id sets shopify_product_id and status
- update_product_staging_image sets image_url and image_path
- Edge cases: empty records, missing fields, user_id filtering

Version: 1.0.0
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from fastapi import HTTPException
from postgrest.exceptions import APIError

from app.db.staging_store import StagingStore


@pytest.fixture
def mock_supabase():
    """Build a mock SupabaseClient with chained table builder."""
    supabase_client = MagicMock()
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.insert.return_value = mock_table
    mock_table.upsert.return_value = mock_table
    mock_table.update.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.limit.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[])
    supabase_client.client.table.return_value = mock_table
    return supabase_client, mock_table


@pytest.fixture
def store(mock_supabase):
    """StagingStore wired to the mock SupabaseClient."""
    supabase_client, _ = mock_supabase
    return StagingStore(supabase_client=supabase_client)


# --------------------------------------------------------------------------
# upsert_product_staging
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestUpsertProductStaging:

    @pytest.mark.asyncio
    async def test_upserts_to_correct_table_with_on_conflict(self, store, mock_supabase):
        _, mock_table = mock_supabase
        records = [{"sku": "WF338109", "title": "Test Part"}]

        await store.upsert_product_staging(records)

        mock_table.upsert.assert_called_once()
        upsert_call = mock_table.upsert.call_args
        assert upsert_call[1]["on_conflict"] == "user_id,sku"

    @pytest.mark.asyncio
    async def test_row_includes_sku_and_title(self, store, mock_supabase):
        _, mock_table = mock_supabase
        records = [{"sku": "WF338109", "title": "Gasket"}]

        await store.upsert_product_staging(records)

        upserted_rows = mock_table.upsert.call_args[0][0]
        assert len(upserted_rows) == 1
        assert upserted_rows[0]["sku"] == "WF338109"
        assert upserted_rows[0]["title"] == "Gasket"

    @pytest.mark.asyncio
    async def test_skips_empty_records(self, store, mock_supabase):
        _, mock_table = mock_supabase

        await store.upsert_product_staging([])

        mock_table.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_default_status_is_fetched(self, store, mock_supabase):
        _, mock_table = mock_supabase
        records = [{"sku": "A"}]

        await store.upsert_product_staging(records)

        upserted_rows = mock_table.upsert.call_args[0][0]
        assert upserted_rows[0]["status"] == "fetched"

    @pytest.mark.asyncio
    async def test_batch_id_included_when_provided(self, store, mock_supabase):
        _, mock_table = mock_supabase
        records = [{"sku": "A"}]

        await store.upsert_product_staging(records, batch_id="batch-001")

        upserted_rows = mock_table.upsert.call_args[0][0]
        assert upserted_rows[0]["batch_id"] == "batch-001"

    @pytest.mark.asyncio
    async def test_batch_id_absent_when_not_provided(self, store, mock_supabase):
        _, mock_table = mock_supabase
        records = [{"sku": "A"}]

        await store.upsert_product_staging(records)

        upserted_rows = mock_table.upsert.call_args[0][0]
        assert "batch_id" not in upserted_rows[0]

    @pytest.mark.asyncio
    async def test_extracts_shopify_nested_fields(self, store, mock_supabase):
        _, mock_table = mock_supabase
        records = [{
            "sku": "WF338109",
            "shopify": {
                "title": "Shopify Title",
                "body_html": "<p>Description</p>",
                "vendor": "TestVendor",
                "price": 25.50,
            },
        }]

        await store.upsert_product_staging(records)

        upserted_rows = mock_table.upsert.call_args[0][0]
        row = upserted_rows[0]
        assert row["title"] == "Shopify Title"
        assert row["body_html"] == "<p>Description</p>"
        assert row["vendor"] == "TestVendor"
        assert row["price"] == 25.50


# --------------------------------------------------------------------------
# get_product_staging_by_part_number
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestGetProductStagingByPartNumber:

    @pytest.mark.asyncio
    async def test_returns_record_found_by_sku(self, store, mock_supabase):
        _, mock_table = mock_supabase
        expected = {"sku": "WF338109", "title": "Gasket"}
        mock_table.execute.return_value = MagicMock(data=[expected])

        result = await store.get_product_staging_by_part_number("WF338109")

        assert result == expected

    @pytest.mark.asyncio
    async def test_falls_back_to_id_when_sku_not_found(self, store, mock_supabase):
        _, mock_table = mock_supabase
        expected = {"id": "uuid-123", "sku": "WF338109"}
        # First call (sku query) returns empty, second call (id query) returns data
        mock_table.execute.side_effect = [
            MagicMock(data=[]),
            MagicMock(data=[expected]),
        ]

        result = await store.get_product_staging_by_part_number("uuid-123")

        assert result == expected

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self, store, mock_supabase):
        _, mock_table = mock_supabase
        mock_table.execute.side_effect = [
            MagicMock(data=[]),
            MagicMock(data=[]),
        ]

        result = await store.get_product_staging_by_part_number("NONEXISTENT")

        assert result is None

    @pytest.mark.asyncio
    async def test_applies_user_id_filter(self, store, mock_supabase):
        _, mock_table = mock_supabase
        mock_table.execute.return_value = MagicMock(data=[{"sku": "A"}])

        await store.get_product_staging_by_part_number("A", user_id="u1")

        # Should have called eq for user_id and sku
        eq_calls = mock_table.eq.call_args_list
        user_id_calls = [c for c in eq_calls if c[0][0] == "user_id"]
        assert len(user_id_calls) >= 1

    @pytest.mark.asyncio
    async def test_raises_on_api_error(self, store, mock_supabase):
        _, mock_table = mock_supabase
        mock_table.execute.side_effect = APIError({"message": "db error", "code": "42000", "details": "", "hint": ""})

        with pytest.raises(HTTPException) as exc_info:
            await store.get_product_staging_by_part_number("A")

        assert exc_info.value.status_code == 500


# --------------------------------------------------------------------------
# update_product_staging_shopify_id
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestUpdateProductStagingShopifyId:

    @pytest.mark.asyncio
    async def test_updates_shopify_id_and_status(self, store, mock_supabase):
        _, mock_table = mock_supabase
        mock_table.execute.return_value = MagicMock(data=[{"sku": "A"}])

        await store.update_product_staging_shopify_id("A", "shopify-99001")

        mock_table.update.assert_called()
        update_payload = mock_table.update.call_args[0][0]
        assert update_payload["shopify_product_id"] == "shopify-99001"
        assert update_payload["status"] == "published"

    @pytest.mark.asyncio
    async def test_falls_back_to_id_when_sku_update_returns_empty(self, store, mock_supabase):
        _, mock_table = mock_supabase
        # First update returns empty (no rows matched by sku), second returns data
        mock_table.execute.side_effect = [
            MagicMock(data=[]),
            MagicMock(data=[{"id": "uuid-1"}]),
        ]

        await store.update_product_staging_shopify_id("uuid-1", "shopify-99001")

        # update called at least twice (first by sku, then by id)
        assert mock_table.update.call_count >= 2

    @pytest.mark.asyncio
    async def test_raises_on_api_error(self, store, mock_supabase):
        _, mock_table = mock_supabase
        mock_table.execute.side_effect = APIError({"message": "update failed", "code": "42000", "details": "", "hint": ""})

        with pytest.raises(HTTPException) as exc_info:
            await store.update_product_staging_shopify_id("A", "shopify-99001")

        assert exc_info.value.status_code == 500


# --------------------------------------------------------------------------
# update_product_staging_image
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestUpdateProductStagingImage:

    @pytest.mark.asyncio
    async def test_updates_image_fields(self, store, mock_supabase):
        _, mock_table = mock_supabase
        mock_table.execute.return_value = MagicMock(data=[{"id": "part-1"}])

        await store.update_product_staging_image(
            "part-1", "https://cdn.test/img.png", "products/img.png"
        )

        mock_table.update.assert_called()
        update_payload = mock_table.update.call_args[0][0]
        assert update_payload["image_url"] == "https://cdn.test/img.png"
        assert update_payload["image_path"] == "products/img.png"

    @pytest.mark.asyncio
    async def test_falls_back_to_sku_when_id_returns_empty(self, store, mock_supabase):
        _, mock_table = mock_supabase
        # First update by id returns empty, second by sku succeeds
        mock_table.execute.side_effect = [
            MagicMock(data=[]),
            MagicMock(data=[{"sku": "A"}]),
        ]

        await store.update_product_staging_image("A", "https://cdn.test/img.png", "p/img.png")

        # Should attempt update twice
        assert mock_table.update.call_count == 2

    @pytest.mark.asyncio
    async def test_raises_on_api_error(self, store, mock_supabase):
        _, mock_table = mock_supabase
        mock_table.execute.side_effect = APIError({"message": "update failed", "code": "42000", "details": "", "hint": ""})

        with pytest.raises(HTTPException) as exc_info:
            await store.update_product_staging_image("A", "url", "path")

        assert exc_info.value.status_code == 500
