"""
Integration tests for batch routes.

Tests GET /api/v1/batches/{id}, GET /api/v1/batches, DELETE /api/v1/batches/{id}.
Verifies auth enforcement, correct batch store delegation, and error handling.
Version: 1.0.0
"""
import os
import pytest
from unittest.mock import patch, MagicMock

os.environ.setdefault("AUTO_START_CELERY", "false")

from fastapi.testclient import TestClient


SAMPLE_BATCH = {
    "id": "batch-001",
    "batch_type": "extract",
    "status": "processing",
    "total_items": 5,
    "extracted_count": 3,
    "normalized_count": 2,
    "published_count": 0,
    "failed_count": 1,
    "part_numbers": ["WF338109", "AN3-12A", "MS20426AD3-5", "NAS1149F0332P", "AN960C10L"],
    "publish_part_numbers": None,
    "error_message": None,
    "idempotency_key": None,
    "failed_items": None,
    "created_at": "2025-01-15T10:00:00+00:00",
    "updated_at": "2025-01-15T10:05:00+00:00",
    "completed_at": None,
}


@pytest.fixture
def client():
    """Create a test client with auth dependency overridden."""
    with patch.dict(os.environ, {"AUTO_START_CELERY": "false"}):
        from app.main import app
        from app.core.auth import get_current_user

        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "test-user-id",
            "email": "test@test.com",
            "username": "testuser",
            "groups": [],
            "scope": [],
        }
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
        app.dependency_overrides.clear()


@pytest.fixture
def unauthenticated_client():
    """Create a test client WITHOUT auth override to test 401/403."""
    with patch.dict(os.environ, {"AUTO_START_CELERY": "false"}):
        from app.main import app
        app.dependency_overrides.clear()
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


@pytest.mark.integration
class TestGetBatchStatus:
    """Tests for GET /api/v1/batches/{batch_id}."""

    @patch("app.routes.batches.batch_store")
    def test_get_batch_found(self, mock_batch_store, client):
        """Getting an existing batch should return its status."""
        mock_batch_store.get_batch_by_user.return_value = SAMPLE_BATCH

        response = client.get("/api/v1/batches/batch-001")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "batch-001"
        assert data["status"] == "processing"
        assert data["total_items"] == 5

    @patch("app.routes.batches.batch_store")
    def test_get_batch_not_found(self, mock_batch_store, client):
        """Getting a non-existent batch should return 404."""
        mock_batch_store.get_batch_by_user.return_value = None

        response = client.get("/api/v1/batches/nonexistent-id")
        assert response.status_code == 404

    @patch("app.routes.batches.batch_store")
    def test_get_batch_includes_progress(self, mock_batch_store, client):
        """Batch status should include progress_percent."""
        mock_batch_store.get_batch_by_user.return_value = SAMPLE_BATCH

        response = client.get("/api/v1/batches/batch-001")
        data = response.json()
        assert "progress_percent" in data
        assert isinstance(data["progress_percent"], (int, float))

    def test_get_batch_requires_auth(self, unauthenticated_client):
        """Getting a batch without auth should return 401 or 403."""
        response = unauthenticated_client.get("/api/v1/batches/batch-001")
        assert response.status_code in (401, 403)


@pytest.mark.integration
class TestListBatches:
    """Tests for GET /api/v1/batches."""

    @patch("app.routes.batches.batch_store")
    def test_list_batches_returns_results(self, mock_batch_store, client):
        """Listing batches should return a paginated response."""
        mock_batch_store.list_batches.return_value = ([SAMPLE_BATCH], 1)

        response = client.get("/api/v1/batches")
        assert response.status_code == 200
        data = response.json()
        assert "batches" in data
        assert "total" in data
        assert data["total"] == 1
        assert len(data["batches"]) == 1

    @patch("app.routes.batches.batch_store")
    def test_list_batches_empty(self, mock_batch_store, client):
        """Listing batches when none exist should return empty list."""
        mock_batch_store.list_batches.return_value = ([], 0)

        response = client.get("/api/v1/batches")
        assert response.status_code == 200
        data = response.json()
        assert data["batches"] == []
        assert data["total"] == 0

    @patch("app.routes.batches.batch_store")
    def test_list_batches_with_pagination(self, mock_batch_store, client):
        """Listing batches with limit and offset should pass params to store."""
        mock_batch_store.list_batches.return_value = ([], 0)

        response = client.get("/api/v1/batches", params={"limit": 10, "offset": 5})
        assert response.status_code == 200
        mock_batch_store.list_batches.assert_called_once_with(
            limit=10, offset=5, status=None, user_id="test-user-id"
        )

    def test_list_batches_requires_auth(self, unauthenticated_client):
        """Listing batches without auth should return 401 or 403."""
        response = unauthenticated_client.get("/api/v1/batches")
        assert response.status_code in (401, 403)


@pytest.mark.integration
class TestCancelBatch:
    """Tests for DELETE /api/v1/batches/{batch_id}."""

    @patch("app.celery_app.tasks.batch.cancel_batch")
    @patch("app.routes.batches.batch_store")
    def test_cancel_processing_batch(self, mock_batch_store, mock_cancel_task, client):
        """Cancelling a processing batch should initiate cancellation."""
        mock_batch_store.get_batch_by_user.return_value = {
            **SAMPLE_BATCH,
            "status": "processing",
        }
        mock_cancel_task.delay.return_value = MagicMock()

        response = client.delete("/api/v1/batches/batch-001")
        assert response.status_code == 200
        data = response.json()
        assert "cancellation" in data["message"].lower()

    @patch("app.routes.batches.batch_store")
    def test_cancel_completed_batch_returns_400(self, mock_batch_store, client):
        """Cancelling a completed batch should return 400."""
        mock_batch_store.get_batch_by_user.return_value = {
            **SAMPLE_BATCH,
            "status": "completed",
        }

        response = client.delete("/api/v1/batches/batch-001")
        assert response.status_code == 400

    @patch("app.routes.batches.batch_store")
    def test_cancel_nonexistent_batch_returns_404(self, mock_batch_store, client):
        """Cancelling a non-existent batch should return 404."""
        mock_batch_store.get_batch_by_user.return_value = None

        response = client.delete("/api/v1/batches/nonexistent")
        assert response.status_code == 404
