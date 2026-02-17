"""
Unit tests for SyncStore — product sync schedule CRUD and status management.

Tests cover:
- client property creates SupabaseClient lazily
- upsert_sync_schedule creates or updates a sync schedule
- get_products_for_hour returns active products for a given hour bucket
- update_sync_success updates record after successful sync
- update_sync_failure increments failures and deactivates after max
- create_sync_schedule inserts new schedule with least-loaded slot
- reactivate_product resets failure counters

Version: 1.0.0
"""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from app.db.sync_store import SyncStore


@pytest.fixture
def mock_supabase_table():
    """Build a chained mock table builder for Supabase."""
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.insert.return_value = mock_table
    mock_table.upsert.return_value = mock_table
    mock_table.update.return_value = mock_table
    mock_table.delete.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.in_.return_value = mock_table
    mock_table.lt.return_value = mock_table
    mock_table.limit.return_value = mock_table
    mock_table.order.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[], count=0)
    return mock_table


@pytest.fixture
def store(mock_supabase_table):
    """SyncStore with a mocked SupabaseClient."""
    mock_sb_client = MagicMock()
    mock_sb_client.client.table.return_value = mock_supabase_table
    return SyncStore(supabase_client=mock_sb_client)


# --------------------------------------------------------------------------
# client property
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestClientProperty:

    def test_returns_supabase_client(self, store):
        client = store.client
        assert client is not None

    @patch("app.db.sync_store.SupabaseClient")
    @patch("app.db.sync_store.settings")
    def test_creates_client_lazily_when_none(self, mock_settings, MockSupabaseClient):
        s = SyncStore(supabase_client=None)
        mock_instance = MagicMock()
        MockSupabaseClient.return_value = mock_instance

        client = s.client

        MockSupabaseClient.assert_called_once_with(mock_settings)
        assert client is mock_instance.client


# --------------------------------------------------------------------------
# upsert_sync_schedule
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestUpsertSyncSchedule:

    def test_creates_new_schedule_when_not_existing(self, store, mock_supabase_table):
        # 1st execute: select existing → empty
        # 2nd execute: get_slot_counts (inside get_least_loaded_slot) → empty
        # 3rd execute: upsert → new record
        mock_supabase_table.execute.side_effect = [
            MagicMock(data=[]),  # select existing
            MagicMock(data=[]),  # get_slot_counts
            MagicMock(data=[{"sku": "WF338109", "hour_bucket": 0}]),  # upsert
        ]

        result = store.upsert_sync_schedule(
            sku="WF338109", user_id="u1",
            initial_price=25.50, initial_quantity=100,
        )

        mock_supabase_table.upsert.assert_called_once()
        upsert_data = mock_supabase_table.upsert.call_args[0][0]
        assert upsert_data["sku"] == "WF338109"
        assert upsert_data["user_id"] == "u1"
        assert upsert_data["sync_status"] == "pending"
        assert upsert_data["is_active"] is True
        assert upsert_data["consecutive_failures"] == 0

    def test_preserves_existing_hour_bucket(self, store, mock_supabase_table):
        mock_supabase_table.execute.side_effect = [
            MagicMock(data=[{"hour_bucket": 5}]),  # existing record
            MagicMock(data=[{"sku": "A", "hour_bucket": 5}]),  # upsert
        ]

        result = store.upsert_sync_schedule(sku="A", user_id="u1")

        upsert_data = mock_supabase_table.upsert.call_args[0][0]
        assert upsert_data["hour_bucket"] == 5

    def test_raises_on_db_error(self, store, mock_supabase_table):
        mock_supabase_table.execute.side_effect = Exception("DB connection failed")

        with pytest.raises(Exception, match="DB connection failed"):
            store.upsert_sync_schedule(sku="A", user_id="u1")


# --------------------------------------------------------------------------
# get_products_for_hour
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestGetProductsForHour:

    def test_returns_active_products_for_hour(self, store, mock_supabase_table):
        products = [
            {"sku": "A", "hour_bucket": 3, "is_active": True},
            {"sku": "B", "hour_bucket": 3, "is_active": True},
        ]
        mock_supabase_table.execute.return_value = MagicMock(data=products)

        result = store.get_products_for_hour(3)

        assert result == products
        mock_supabase_table.eq.assert_any_call("hour_bucket", 3)
        mock_supabase_table.eq.assert_any_call("is_active", True)

    def test_returns_empty_list_on_error(self, store, mock_supabase_table):
        mock_supabase_table.execute.side_effect = Exception("DB error")

        result = store.get_products_for_hour(0)

        assert result == []

    def test_applies_status_filter(self, store, mock_supabase_table):
        mock_supabase_table.execute.return_value = MagicMock(data=[])

        store.get_products_for_hour(1, status_filter=["pending", "failed"])

        mock_supabase_table.in_.assert_called_once_with("sync_status", ["pending", "failed"])


# --------------------------------------------------------------------------
# update_sync_success
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestUpdateSyncSuccess:

    def test_updates_record_with_success_data(self, store, mock_supabase_table):
        mock_supabase_table.execute.return_value = MagicMock(data=[{"sku": "A"}])

        result = store.update_sync_success(
            sku="A", new_hash="abc123",
            new_price=25.50, new_quantity=100,
        )

        assert result is True
        mock_supabase_table.update.assert_called_once()
        update_data = mock_supabase_table.update.call_args[0][0]
        assert update_data["sync_status"] == "success"
        assert update_data["last_boeing_hash"] == "abc123"
        assert update_data["last_price"] == 25.50
        assert update_data["last_quantity"] == 100
        assert update_data["consecutive_failures"] == 0
        assert update_data["last_error"] is None

    def test_includes_inventory_status_and_locations(self, store, mock_supabase_table):
        mock_supabase_table.execute.return_value = MagicMock(data=[{"sku": "A"}])
        locations = [{"location": "Dallas", "quantity": 50}]

        store.update_sync_success(
            sku="A", new_hash="h1",
            new_price=10.0, new_quantity=50,
            inventory_status="in_stock", locations=locations,
        )

        update_data = mock_supabase_table.update.call_args[0][0]
        assert update_data["last_inventory_status"] == "in_stock"
        assert update_data["last_locations"] == locations

    def test_returns_false_when_no_rows_updated(self, store, mock_supabase_table):
        mock_supabase_table.execute.return_value = MagicMock(data=[])

        result = store.update_sync_success("MISSING", "h1", 10.0, 0)

        assert result is False


# --------------------------------------------------------------------------
# update_sync_failure
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestUpdateSyncFailure:

    @patch("app.db.sync_store.MAX_CONSECUTIVE_FAILURES", 5)
    def test_increments_failure_count(self, store, mock_supabase_table):
        mock_supabase_table.execute.side_effect = [
            MagicMock(data=[{"consecutive_failures": 1}]),  # select current
            MagicMock(data=[]),  # update
        ]

        result = store.update_sync_failure("A", "timeout error")

        assert result["sku"] == "A"
        assert result["consecutive_failures"] == 2
        assert result["is_active"] is True

    @patch("app.db.sync_store.MAX_CONSECUTIVE_FAILURES", 3)
    def test_deactivates_after_max_failures(self, store, mock_supabase_table):
        mock_supabase_table.execute.side_effect = [
            MagicMock(data=[{"consecutive_failures": 2}]),  # current = 2, next = 3
            MagicMock(data=[]),  # update
        ]

        result = store.update_sync_failure("A", "fatal error")

        assert result["consecutive_failures"] == 3
        assert result["is_active"] is False

    def test_returns_error_dict_on_exception(self, store, mock_supabase_table):
        mock_supabase_table.execute.side_effect = Exception("DB gone")

        result = store.update_sync_failure("A", "some error")

        assert result["sku"] == "A"
        assert "error" in result

    @patch("app.db.sync_store.MAX_CONSECUTIVE_FAILURES", 5)
    def test_truncates_error_message_to_500_chars(self, store, mock_supabase_table):
        mock_supabase_table.execute.side_effect = [
            MagicMock(data=[{"consecutive_failures": 0}]),
            MagicMock(data=[]),
        ]
        long_error = "x" * 1000

        store.update_sync_failure("A", long_error)

        update_data = mock_supabase_table.update.call_args[0][0]
        assert len(update_data["last_error"]) == 500


# --------------------------------------------------------------------------
# reactivate_product
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestReactivateProduct:

    def test_resets_product_to_active_pending(self, store, mock_supabase_table):
        mock_supabase_table.execute.return_value = MagicMock(data=[{"sku": "A"}])

        result = store.reactivate_product("A")

        assert result is True
        update_data = mock_supabase_table.update.call_args[0][0]
        assert update_data["is_active"] is True
        assert update_data["sync_status"] == "pending"
        assert update_data["consecutive_failures"] == 0
        assert update_data["last_error"] is None

    def test_returns_false_when_not_found(self, store, mock_supabase_table):
        mock_supabase_table.execute.return_value = MagicMock(data=[])

        result = store.reactivate_product("MISSING")

        assert result is False

    def test_returns_false_on_error(self, store, mock_supabase_table):
        mock_supabase_table.execute.side_effect = Exception("DB error")

        result = store.reactivate_product("A")

        assert result is False


# --------------------------------------------------------------------------
# delete_sync_schedule
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestDeleteSyncSchedule:

    def test_deletes_by_sku(self, store, mock_supabase_table):
        mock_supabase_table.execute.return_value = MagicMock(data=[{"sku": "A"}])

        result = store.delete_sync_schedule("A")

        assert result is True
        mock_supabase_table.delete.assert_called_once()
        mock_supabase_table.eq.assert_called_with("sku", "A")

    def test_returns_false_when_nothing_deleted(self, store, mock_supabase_table):
        mock_supabase_table.execute.return_value = MagicMock(data=[])

        result = store.delete_sync_schedule("MISSING")

        assert result is False
