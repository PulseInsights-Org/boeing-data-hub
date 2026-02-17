"""
Unit tests for RawDataStore — Boeing API response storage.

Tests cover:
- insert_boeing_raw_data builds the correct row and delegates to _insert
- Default user_id is "system" when not specified
- Custom user_id is forwarded
- Correct table name is passed

Version: 1.0.0
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.db.raw_data_store import RawDataStore


@pytest.fixture
def mock_supabase():
    """Build a mock SupabaseClient."""
    supabase_client = MagicMock()
    mock_table = MagicMock()
    mock_table.insert.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[])
    supabase_client.client.table.return_value = mock_table
    return supabase_client, mock_table


@pytest.fixture
def store(mock_supabase):
    """RawDataStore wired to the mock SupabaseClient."""
    supabase_client, _ = mock_supabase
    return RawDataStore(supabase_client=supabase_client)


# --------------------------------------------------------------------------
# insert_boeing_raw_data
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestInsertBoeingRawData:

    @pytest.mark.asyncio
    async def test_inserts_row_with_correct_table(self, store, mock_supabase):
        _, mock_table = mock_supabase
        payload = {"lineItems": [{"aviallPartNumber": "WF338109"}]}

        await store.insert_boeing_raw_data("WF338109", payload)

        store._client.table.assert_called_with("boeing_raw_data")
        mock_table.insert.assert_called_once()

    @pytest.mark.asyncio
    async def test_row_contains_search_query(self, store, mock_supabase):
        _, mock_table = mock_supabase
        payload = {"lineItems": []}

        await store.insert_boeing_raw_data("AN3-12A", payload)

        inserted_rows = mock_table.insert.call_args[0][0]
        assert len(inserted_rows) == 1
        assert inserted_rows[0]["search_query"] == "AN3-12A"

    @pytest.mark.asyncio
    async def test_row_contains_raw_payload(self, store, mock_supabase):
        _, mock_table = mock_supabase
        payload = {"currency": "USD", "lineItems": [{"aviallPartNumber": "X"}]}

        await store.insert_boeing_raw_data("X", payload)

        inserted_rows = mock_table.insert.call_args[0][0]
        assert inserted_rows[0]["raw_payload"] == payload

    @pytest.mark.asyncio
    async def test_default_user_id_is_system(self, store, mock_supabase):
        _, mock_table = mock_supabase

        await store.insert_boeing_raw_data("Q", {"lineItems": []})

        inserted_rows = mock_table.insert.call_args[0][0]
        assert inserted_rows[0]["user_id"] == "system"

    @pytest.mark.asyncio
    async def test_custom_user_id(self, store, mock_supabase):
        _, mock_table = mock_supabase

        await store.insert_boeing_raw_data("Q", {"lineItems": []}, user_id="user-42")

        inserted_rows = mock_table.insert.call_args[0][0]
        assert inserted_rows[0]["user_id"] == "user-42"

    @pytest.mark.asyncio
    async def test_row_has_exactly_three_keys(self, store, mock_supabase):
        _, mock_table = mock_supabase

        await store.insert_boeing_raw_data("Q", {})

        inserted_rows = mock_table.insert.call_args[0][0]
        assert set(inserted_rows[0].keys()) == {"search_query", "raw_payload", "user_id"}
