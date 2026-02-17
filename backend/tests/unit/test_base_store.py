"""
Unit tests for BaseStore — shared Supabase client access and CRUD helpers.

Tests cover:
- Client property returns the Supabase client from SupabaseClient wrapper
- _insert delegates to supabase table insert
- _upsert delegates to supabase table upsert (with and without on_conflict)
- _select delegates to supabase table select with optional filters
- _update delegates to supabase table update with filters
- Error handling raises HTTPException on APIError

Version: 1.0.0
"""
import pytest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from postgrest.exceptions import APIError

from app.db.base_store import BaseStore


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
    mock_table.execute.return_value = MagicMock(data=[])
    supabase_client.client.table.return_value = mock_table
    return supabase_client, mock_table


@pytest.fixture
def store(mock_supabase):
    """BaseStore instance wired to the mock SupabaseClient."""
    supabase_client, _ = mock_supabase
    return BaseStore(supabase_client=supabase_client)


# --------------------------------------------------------------------------
# _client property
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestClientProperty:
    """Verify _client property delegates to SupabaseClient.client."""

    def test_client_returns_supabase_client(self, store, mock_supabase):
        supabase_client, _ = mock_supabase
        assert store._client is supabase_client.client


# --------------------------------------------------------------------------
# _insert
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestInsert:

    @pytest.mark.asyncio
    async def test_insert_calls_table_insert(self, store, mock_supabase):
        _, mock_table = mock_supabase
        rows = [{"sku": "A", "title": "Part A"}]

        await store._insert("boeing_raw_data", rows)

        store._client.table.assert_called_with("boeing_raw_data")
        mock_table.insert.assert_called_once_with(rows)
        mock_table.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_insert_skips_empty_rows(self, store, mock_supabase):
        _, mock_table = mock_supabase

        await store._insert("boeing_raw_data", [])

        mock_table.insert.assert_not_called()

    @pytest.mark.asyncio
    async def test_insert_raises_http_exception_on_api_error(self, store, mock_supabase):
        _, mock_table = mock_supabase
        mock_table.execute.side_effect = APIError({"message": "insert failed", "code": "42000", "details": "", "hint": ""})

        with pytest.raises(HTTPException) as exc_info:
            await store._insert("boeing_raw_data", [{"sku": "A"}])

        assert exc_info.value.status_code == 500
        assert "boeing_raw_data" in exc_info.value.detail


# --------------------------------------------------------------------------
# _upsert
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestUpsert:

    @pytest.mark.asyncio
    async def test_upsert_without_on_conflict(self, store, mock_supabase):
        _, mock_table = mock_supabase
        rows = [{"sku": "A"}]

        await store._upsert("product_staging", rows)

        mock_table.upsert.assert_called_once_with(rows)

    @pytest.mark.asyncio
    async def test_upsert_with_on_conflict(self, store, mock_supabase):
        _, mock_table = mock_supabase
        rows = [{"sku": "A"}]

        await store._upsert("product_staging", rows, on_conflict="user_id,sku")

        mock_table.upsert.assert_called_once_with(rows, on_conflict="user_id,sku")

    @pytest.mark.asyncio
    async def test_upsert_skips_empty_rows(self, store, mock_supabase):
        _, mock_table = mock_supabase

        await store._upsert("product_staging", [])

        mock_table.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_upsert_raises_http_exception_on_api_error(self, store, mock_supabase):
        _, mock_table = mock_supabase
        mock_table.execute.side_effect = APIError({"message": "upsert failed", "code": "42000", "details": "", "hint": ""})

        with pytest.raises(HTTPException) as exc_info:
            await store._upsert("product_staging", [{"sku": "A"}])

        assert exc_info.value.status_code == 500


# --------------------------------------------------------------------------
# _select
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestSelect:

    @pytest.mark.asyncio
    async def test_select_returns_data(self, store, mock_supabase):
        _, mock_table = mock_supabase
        mock_table.execute.return_value = MagicMock(data=[{"sku": "A", "title": "Part A"}])

        result = await store._select("product")

        assert result == [{"sku": "A", "title": "Part A"}]
        mock_table.select.assert_called_once_with("*")

    @pytest.mark.asyncio
    async def test_select_with_filters(self, store, mock_supabase):
        _, mock_table = mock_supabase
        mock_table.execute.return_value = MagicMock(data=[{"sku": "A"}])

        result = await store._select("product", filters={"sku": "A", "user_id": "u1"})

        assert mock_table.eq.call_count == 2

    @pytest.mark.asyncio
    async def test_select_with_custom_columns(self, store, mock_supabase):
        _, mock_table = mock_supabase
        mock_table.execute.return_value = MagicMock(data=[])

        await store._select("product", columns="sku,title")

        mock_table.select.assert_called_once_with("sku,title")

    @pytest.mark.asyncio
    async def test_select_returns_empty_when_no_data(self, store, mock_supabase):
        _, mock_table = mock_supabase
        mock_table.execute.return_value = MagicMock(data=None)

        result = await store._select("product")

        assert result == []


# --------------------------------------------------------------------------
# _update
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestUpdate:

    @pytest.mark.asyncio
    async def test_update_applies_filters_and_payload(self, store, mock_supabase):
        _, mock_table = mock_supabase

        await store._update("product", {"sku": "A"}, {"price": 10.0})

        mock_table.update.assert_called_once_with({"price": 10.0})
        mock_table.eq.assert_called_once_with("sku", "A")
        mock_table.execute.assert_called()

    @pytest.mark.asyncio
    async def test_update_raises_http_exception_on_api_error(self, store, mock_supabase):
        _, mock_table = mock_supabase
        mock_table.execute.side_effect = APIError({"message": "update failed", "code": "42000", "details": "", "hint": ""})

        with pytest.raises(HTTPException) as exc_info:
            await store._update("product", {"sku": "A"}, {"price": 10.0})

        assert exc_info.value.status_code == 500
