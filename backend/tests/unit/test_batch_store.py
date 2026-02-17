"""
Unit tests for BatchStore — CRUD for pipeline batch tracking.

Tests cover:
- create_batch inserts a new batch record with correct fields
- get_batch retrieves a batch by ID
- update_status changes batch status and adds completed_at for terminal states
- _increment_counter reads current value and writes incremented value
- record_failure appends to failed_items and failed_part_numbers
- list_batches with pagination, status filter, and user_id filter

Version: 1.0.0
"""
import pytest
from unittest.mock import MagicMock, patch

from app.db.batch_store import BatchStore


@pytest.fixture
def mock_supabase_table():
    """Build a chained mock table builder for Supabase."""
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.insert.return_value = mock_table
    mock_table.update.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.in_.return_value = mock_table
    mock_table.order.return_value = mock_table
    mock_table.range.return_value = mock_table
    mock_table.limit.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[], count=0)
    return mock_table


@pytest.fixture
def store(mock_supabase_table):
    """BatchStore with a mocked Supabase client."""
    with patch("app.db.batch_store.create_client") as mock_create:
        mock_client = MagicMock()
        mock_client.table.return_value = mock_supabase_table
        mock_create.return_value = mock_client

        mock_settings = MagicMock()
        mock_settings.supabase_url = "https://test.supabase.co"
        mock_settings.supabase_key = "test-key"

        s = BatchStore(mock_settings)
    return s


# --------------------------------------------------------------------------
# create_batch
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestCreateBatch:

    def test_inserts_batch_with_correct_fields(self, store):
        store.client.table.return_value.execute.return_value = MagicMock(data=[{
            "id": "batch-uuid",
            "batch_type": "extract",
            "status": "pending",
            "total_items": 5,
        }])

        result = store.create_batch(
            batch_type="extract",
            total_items=5,
            user_id="user-1",
            part_numbers=["A", "B", "C", "D", "E"],
        )

        store.client.table.assert_called_with("batches")
        insert_call = store.client.table.return_value.insert
        insert_call.assert_called_once()
        data = insert_call.call_args[0][0]
        assert data["batch_type"] == "extract"
        assert data["total_items"] == 5
        assert data["status"] == "pending"
        assert data["user_id"] == "user-1"
        assert data["part_numbers"] == ["A", "B", "C", "D", "E"]
        assert data["extracted_count"] == 0
        assert data["failed_count"] == 0

    def test_returns_created_record(self, store):
        expected = {"id": "batch-uuid", "batch_type": "extract", "status": "pending"}
        store.client.table.return_value.execute.return_value = MagicMock(data=[expected])

        result = store.create_batch(batch_type="extract", total_items=3)

        assert result == expected

    def test_returns_data_dict_when_insert_returns_empty(self, store):
        store.client.table.return_value.execute.return_value = MagicMock(data=[])

        result = store.create_batch(batch_type="publish", total_items=1)

        assert result["batch_type"] == "publish"
        assert result["status"] == "pending"
        assert "id" in result

    def test_idempotency_key_and_celery_task_id_stored(self, store):
        store.client.table.return_value.execute.return_value = MagicMock(data=[{"id": "b1"}])

        store.create_batch(
            batch_type="extract",
            total_items=1,
            idempotency_key="idem-123",
            celery_task_id="celery-456",
        )

        data = store.client.table.return_value.insert.call_args[0][0]
        assert data["idempotency_key"] == "idem-123"
        assert data["celery_task_id"] == "celery-456"


# --------------------------------------------------------------------------
# get_batch
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestGetBatch:

    def test_returns_batch_when_found(self, store):
        expected = {"id": "batch-001", "status": "processing"}
        store.client.table.return_value.execute.return_value = MagicMock(data=[expected])

        result = store.get_batch("batch-001")

        assert result == expected
        store.client.table.return_value.eq.assert_called_with("id", "batch-001")

    def test_returns_none_when_not_found(self, store):
        store.client.table.return_value.execute.return_value = MagicMock(data=[])

        result = store.get_batch("nonexistent")

        assert result is None


# --------------------------------------------------------------------------
# update_status
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestUpdateStatus:

    def test_updates_status_to_processing(self, store):
        store.update_status("batch-001", "processing")

        update_call = store.client.table.return_value.update
        update_call.assert_called_once()
        payload = update_call.call_args[0][0]
        assert payload["status"] == "processing"
        assert "completed_at" not in payload

    def test_adds_completed_at_for_completed_status(self, store):
        store.update_status("batch-001", "completed")

        payload = store.client.table.return_value.update.call_args[0][0]
        assert payload["status"] == "completed"
        assert "completed_at" in payload

    def test_adds_completed_at_for_failed_status(self, store):
        store.update_status("batch-001", "failed", error_message="Something broke")

        payload = store.client.table.return_value.update.call_args[0][0]
        assert payload["status"] == "failed"
        assert payload["error_message"] == "Something broke"
        assert "completed_at" in payload

    def test_applies_batch_id_filter(self, store):
        store.update_status("batch-001", "processing")

        store.client.table.return_value.eq.assert_called_with("id", "batch-001")


# --------------------------------------------------------------------------
# _increment_counter
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestIncrementCounter:

    def test_reads_then_writes_incremented_value(self, store):
        # First execute returns the select, second execute returns the update
        store.client.table.return_value.execute.side_effect = [
            MagicMock(data=[{"extracted_count": 3}]),
            MagicMock(data=[]),
        ]

        store._increment_counter("batch-001", "extracted_count", 2)

        update_call = store.client.table.return_value.update
        update_call.assert_called_once_with({"extracted_count": 5})

    def test_no_op_when_batch_not_found(self, store):
        store.client.table.return_value.execute.return_value = MagicMock(data=[])

        store._increment_counter("nonexistent", "extracted_count")

        store.client.table.return_value.update.assert_not_called()

    def test_increment_extracted_delegates(self, store):
        store.client.table.return_value.execute.side_effect = [
            MagicMock(data=[{"extracted_count": 0}]),
            MagicMock(data=[]),
        ]

        store.increment_extracted("batch-001")

        store.client.table.return_value.update.assert_called_once_with({"extracted_count": 1})


# --------------------------------------------------------------------------
# record_failure
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestRecordFailure:

    def test_appends_failure_to_existing_list(self, store):
        store.client.table.return_value.execute.side_effect = [
            MagicMock(data=[{
                "failed_items": [{"part_number": "A", "error": "err1"}],
                "failed_count": 1,
            }]),
            MagicMock(data=[]),
        ]

        store.record_failure("batch-001", "B", "timeout", stage="normalization")

        update_call = store.client.table.return_value.update
        update_payload = update_call.call_args[0][0]
        assert len(update_payload["failed_items"]) == 2
        item = update_payload["failed_items"][1]
        assert item["part_number"] == "B"
        assert item["error"] == "timeout"
        assert item["stage"] == "normalization"
        assert "timestamp" in item
        assert update_payload["failed_count"] == 2

    def test_record_failure_includes_stage_and_timestamp(self, store):
        store.client.table.return_value.execute.side_effect = [
            MagicMock(data=[{
                "failed_items": [],
                "failed_count": 0,
            }]),
            MagicMock(data=[]),
        ]

        store.record_failure("batch-001", "A", "retry failed", stage="extraction")

        update_payload = store.client.table.return_value.update.call_args[0][0]
        item = update_payload["failed_items"][0]
        assert item["part_number"] == "A"
        assert item["error"] == "retry failed"
        assert item["stage"] == "extraction"
        assert "timestamp" in item
        assert update_payload["failed_count"] == 1

    def test_no_op_when_batch_not_found(self, store):
        store.client.table.return_value.execute.return_value = MagicMock(data=[])

        store.record_failure("nonexistent", "A", "error")

        store.client.table.return_value.update.assert_not_called()


# --------------------------------------------------------------------------
# list_batches
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestListBatches:

    def test_returns_data_and_count(self, store):
        batch_list = [{"id": "b1"}, {"id": "b2"}]
        store.client.table.return_value.execute.return_value = MagicMock(data=batch_list, count=2)

        data, count = store.list_batches()

        assert data == batch_list
        assert count == 2

    def test_applies_status_filter(self, store):
        store.client.table.return_value.execute.return_value = MagicMock(data=[], count=0)

        store.list_batches(status="completed")

        eq_calls = store.client.table.return_value.eq.call_args_list
        assert any(c[0] == ("status", "completed") for c in eq_calls)

    def test_applies_user_id_filter(self, store):
        store.client.table.return_value.execute.return_value = MagicMock(data=[], count=0)

        store.list_batches(user_id="user-42")

        eq_calls = store.client.table.return_value.eq.call_args_list
        assert any(c[0] == ("user_id", "user-42") for c in eq_calls)

    def test_applies_pagination(self, store):
        store.client.table.return_value.execute.return_value = MagicMock(data=[], count=0)

        store.list_batches(limit=10, offset=20)

        store.client.table.return_value.range.assert_called_once_with(20, 29)
