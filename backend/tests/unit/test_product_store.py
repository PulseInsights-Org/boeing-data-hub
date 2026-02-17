"""
Unit tests for ProductStore — published product CRUD and pricing updates.

Tests cover:
- upsert_product builds row from record and delegates to _upsert
- upsert_quote_form_data delegates to _upsert on quotes table
- get_product_by_part_number queries by sku then falls back to id
- get_product_by_sku is an alias for get_product_by_part_number
- update_product_pricing updates price/cost/inventory fields
- Edge cases: empty payload, user_id filtering, API errors

Version: 1.0.0
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from postgrest.exceptions import APIError

from app.db.product_store import ProductStore


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
    """ProductStore wired to the mock SupabaseClient."""
    supabase_client, _ = mock_supabase
    return ProductStore(supabase_client=supabase_client)


# --------------------------------------------------------------------------
# upsert_product
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestUpsertProduct:

    @pytest.mark.asyncio
    async def test_upserts_to_product_table_with_on_conflict(self, store, mock_supabase):
        _, mock_table = mock_supabase
        record = {"sku": "WF338109", "title": "Gasket", "price": 25.50}

        await store.upsert_product(record)

        mock_table.upsert.assert_called_once()
        upsert_call = mock_table.upsert.call_args
        assert upsert_call[1]["on_conflict"] == "user_id,sku"

    @pytest.mark.asyncio
    async def test_row_contains_correct_fields(self, store, mock_supabase):
        _, mock_table = mock_supabase
        record = {
            "sku": "WF338109",
            "title": "Gasket",
            "price": 25.50,
            "list_price": 23.00,
            "net_price": 21.00,
            "currency": "USD",
            "inventory_quantity": 150,
            "weight": 0.1,
        }

        await store.upsert_product(record, shopify_product_id="sp-99001")

        upserted_rows = mock_table.upsert.call_args[0][0]
        row = upserted_rows[0]
        assert row["sku"] == "WF338109"
        assert row["price"] == 25.50
        assert row["shopify_product_id"] == "sp-99001"
        assert row["user_id"] == "system"

    @pytest.mark.asyncio
    async def test_custom_user_id(self, store, mock_supabase):
        _, mock_table = mock_supabase
        record = {"sku": "A"}

        await store.upsert_product(record, user_id="user-42")

        upserted_rows = mock_table.upsert.call_args[0][0]
        assert upserted_rows[0]["user_id"] == "user-42"

    @pytest.mark.asyncio
    async def test_extracts_shopify_nested_fields(self, store, mock_supabase):
        _, mock_table = mock_supabase
        record = {
            "sku": "WF338109",
            "shopify": {
                "title": "Shopify Title",
                "body_html": "<p>Desc</p>",
                "vendor": "TestVendor",
                "price": 30.00,
                "cost_per_item": 25.00,
            },
        }

        await store.upsert_product(record)

        upserted_rows = mock_table.upsert.call_args[0][0]
        row = upserted_rows[0]
        assert row["title"] == "Shopify Title"
        assert row["body_html"] == "<p>Desc</p>"
        assert row["vendor"] == "TestVendor"
        assert row["price"] == 30.00
        assert row["cost_per_item"] == 25.00

    @pytest.mark.asyncio
    async def test_row_has_uuid_id(self, store, mock_supabase):
        _, mock_table = mock_supabase
        record = {"sku": "A"}

        await store.upsert_product(record)

        upserted_rows = mock_table.upsert.call_args[0][0]
        row_id = upserted_rows[0]["id"]
        assert isinstance(row_id, str)
        assert len(row_id) == 36  # UUID format


# --------------------------------------------------------------------------
# upsert_quote_form_data
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestUpsertQuoteFormData:

    @pytest.mark.asyncio
    async def test_delegates_to_upsert_on_quotes_table(self, store, mock_supabase):
        _, mock_table = mock_supabase
        record = {"name": "John", "email": "john@test.com", "part_number": "WF338109"}

        await store.upsert_quote_form_data(record)

        store._client.table.assert_called_with("quotes")
        mock_table.upsert.assert_called_once_with([record])


# --------------------------------------------------------------------------
# get_product_by_part_number
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestGetProductByPartNumber:

    @pytest.mark.asyncio
    async def test_returns_record_found_by_sku(self, store, mock_supabase):
        _, mock_table = mock_supabase
        expected = {"sku": "WF338109", "title": "Gasket"}
        mock_table.execute.return_value = MagicMock(data=[expected])

        result = await store.get_product_by_part_number("WF338109")

        assert result == expected

    @pytest.mark.asyncio
    async def test_falls_back_to_id_when_sku_not_found(self, store, mock_supabase):
        _, mock_table = mock_supabase
        expected = {"id": "uuid-123", "sku": "WF338109"}
        mock_table.execute.side_effect = [
            MagicMock(data=[]),
            MagicMock(data=[expected]),
        ]

        result = await store.get_product_by_part_number("uuid-123")

        assert result == expected

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self, store, mock_supabase):
        _, mock_table = mock_supabase
        mock_table.execute.side_effect = [
            MagicMock(data=[]),
            MagicMock(data=[]),
        ]

        result = await store.get_product_by_part_number("MISSING")

        assert result is None

    @pytest.mark.asyncio
    async def test_applies_user_id_filter(self, store, mock_supabase):
        _, mock_table = mock_supabase
        mock_table.execute.return_value = MagicMock(data=[{"sku": "A"}])

        await store.get_product_by_part_number("A", user_id="u1")

        eq_calls = mock_table.eq.call_args_list
        user_id_calls = [c for c in eq_calls if c[0][0] == "user_id"]
        assert len(user_id_calls) >= 1

    @pytest.mark.asyncio
    async def test_raises_on_api_error(self, store, mock_supabase):
        _, mock_table = mock_supabase
        mock_table.execute.side_effect = APIError({"message": "db error", "code": "42000", "details": "", "hint": ""})

        with pytest.raises(HTTPException) as exc_info:
            await store.get_product_by_part_number("A")

        assert exc_info.value.status_code == 500


# --------------------------------------------------------------------------
# get_product_by_sku (alias)
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestGetProductBySku:

    @pytest.mark.asyncio
    async def test_is_alias_for_get_product_by_part_number(self, store, mock_supabase):
        _, mock_table = mock_supabase
        expected = {"sku": "WF338109"}
        mock_table.execute.return_value = MagicMock(data=[expected])

        result = await store.get_product_by_sku("WF338109", user_id="u1")

        assert result == expected


# --------------------------------------------------------------------------
# update_product_pricing
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestUpdateProductPricing:

    @pytest.mark.asyncio
    async def test_updates_price_cost_inventory(self, store, mock_supabase):
        _, mock_table = mock_supabase

        await store.update_product_pricing("WF338109", "u1", price=30.0, cost=25.0, inventory=100)

        mock_table.update.assert_called_once()
        update_payload = mock_table.update.call_args[0][0]
        assert update_payload["price"] == 30.0
        assert update_payload["cost_per_item"] == 25.0
        assert update_payload["inventory_quantity"] == 100

    @pytest.mark.asyncio
    async def test_updates_only_provided_fields(self, store, mock_supabase):
        _, mock_table = mock_supabase

        await store.update_product_pricing("A", "u1", price=15.0)

        update_payload = mock_table.update.call_args[0][0]
        assert update_payload == {"price": 15.0}

    @pytest.mark.asyncio
    async def test_skips_update_when_no_fields_provided(self, store, mock_supabase):
        _, mock_table = mock_supabase

        await store.update_product_pricing("A", "u1")

        mock_table.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_applies_sku_and_user_id_filters(self, store, mock_supabase):
        _, mock_table = mock_supabase

        await store.update_product_pricing("WF338109", "user-42", price=10.0)

        eq_calls = mock_table.eq.call_args_list
        assert any(c[0] == ("user_id", "user-42") for c in eq_calls)
        assert any(c[0] == ("sku", "WF338109") for c in eq_calls)

    @pytest.mark.asyncio
    async def test_raises_on_api_error(self, store, mock_supabase):
        _, mock_table = mock_supabase
        mock_table.execute.side_effect = APIError({"message": "update failed", "code": "42000", "details": "", "hint": ""})

        with pytest.raises(HTTPException) as exc_info:
            await store.update_product_pricing("A", "u1", price=10.0)

        assert exc_info.value.status_code == 500
