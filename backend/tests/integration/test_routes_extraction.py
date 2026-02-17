"""
Integration tests for extraction routes.

Tests GET /api/v1/extraction/search and POST /api/v1/extraction/bulk-search.
Verifies auth enforcement, request validation, and correct service delegation.
Version: 1.0.0
"""
import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

os.environ.setdefault("AUTO_START_CELERY", "false")

from fastapi.testclient import TestClient


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
class TestExtractionSearch:
    """Tests for GET /api/v1/extraction/search."""

    @patch("app.routes.extraction._get_service")
    def test_search_returns_products(self, mock_get_service, client):
        """Search with valid query should return normalized products."""
        mock_service = MagicMock()
        mock_service.search_products = AsyncMock(return_value=[
            {"sku": "WF338109", "title": "WF338109", "vendor": "BDI"},
        ])
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/extraction/search", params={"query": "WF338109"})
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["sku"] == "WF338109"

    @patch("app.routes.extraction._get_service")
    def test_search_empty_result(self, mock_get_service, client):
        """Search with no matches should return empty list."""
        mock_service = MagicMock()
        mock_service.search_products = AsyncMock(return_value=[])
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/extraction/search", params={"query": "NONEXISTENT"})
        assert response.status_code == 200
        data = response.json()
        assert data == []

    def test_search_missing_query_returns_422(self, client):
        """Search without query parameter should return 422 validation error."""
        response = client.get("/api/v1/extraction/search")
        assert response.status_code == 422

    def test_search_requires_auth(self, unauthenticated_client):
        """Search without auth token should return 401 or 403."""
        response = unauthenticated_client.get(
            "/api/v1/extraction/search",
            params={"query": "WF338109"},
        )
        assert response.status_code in (401, 403)

    @patch("app.routes.extraction._get_service")
    def test_search_service_error_returns_500(self, mock_get_service, client):
        """Search that triggers a service exception should return 500."""
        mock_service = MagicMock()
        mock_service.search_products = AsyncMock(
            side_effect=RuntimeError("Boeing API down")
        )
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/extraction/search", params={"query": "WF338109"})
        assert response.status_code == 500


@pytest.mark.integration
class TestExtractionBulkSearch:
    """Tests for POST /api/v1/extraction/bulk-search."""

    @patch("app.celery_app.tasks.extraction.process_bulk_search")
    @patch("app.routes.extraction.batch_store")
    def test_bulk_search_creates_batch(self, mock_batch_store, mock_task, client):
        """Bulk search should create a batch and return batch_id."""
        mock_batch_store.get_batch_by_idempotency_key.return_value = None
        mock_batch_store.create_batch.return_value = {
            "id": "batch-123",
            "total_items": 2,
            "status": "processing",
        }
        mock_celery_result = MagicMock()
        mock_celery_result.id = "celery-task-id"
        mock_task.delay.return_value = mock_celery_result
        mock_batch_store.client.table.return_value.update.return_value.eq.return_value.execute.return_value = None

        response = client.post(
            "/api/v1/extraction/bulk-search",
            json={"part_numbers": ["WF338109", "AN3-12A"]},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["batch_id"] == "batch-123"
        assert data["total_items"] == 2
        assert data["status"] == "processing"

    @patch("app.routes.extraction.batch_store")
    def test_bulk_search_idempotency(self, mock_batch_store, client):
        """Bulk search with existing idempotency key should return existing batch."""
        mock_batch_store.get_batch_by_idempotency_key.return_value = {
            "id": "existing-batch",
            "total_items": 3,
            "status": "completed",
        }

        response = client.post(
            "/api/v1/extraction/bulk-search",
            json={
                "part_numbers": ["WF338109", "AN3-12A", "MS20426AD3-5"],
                "idempotency_key": "test-key-123",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["batch_id"] == "existing-batch"
        assert "idempotent" in data["message"].lower()

    def test_bulk_search_requires_auth(self, unauthenticated_client):
        """Bulk search without auth should return 401 or 403."""
        response = unauthenticated_client.post(
            "/api/v1/extraction/bulk-search",
            json={"part_numbers": ["WF338109"]},
        )
        assert response.status_code in (401, 403)

    def test_bulk_search_empty_list_returns_422(self, client):
        """Bulk search with empty part_numbers should return validation error."""
        response = client.post(
            "/api/v1/extraction/bulk-search",
            json={"part_numbers": []},
        )
        assert response.status_code == 422
