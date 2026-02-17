"""
Integration tests for legacy backward-compatibility routes.

Verifies that old /api/* paths still work and delegate to the same handlers
as the new /api/v1/* routes. These routes exist during frontend migration.
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


@pytest.mark.integration
class TestLegacyExtractionRoutes:
    """Tests for legacy Boeing/extraction routes."""

    @patch("app.routes.extraction._get_service")
    def test_legacy_product_search(self, mock_get_service, client):
        """GET /api/boeing/product-search should work like v1 extraction/search."""
        mock_service = MagicMock()
        mock_service.search_products = AsyncMock(return_value=[
            {"sku": "WF338109", "title": "WF338109"},
        ])
        mock_get_service.return_value = mock_service

        response = client.get("/api/boeing/product-search", params={"query": "WF338109"})
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1

    @patch("app.celery_app.tasks.extraction.process_bulk_search")
    @patch("app.routes.extraction.batch_store")
    def test_legacy_bulk_search(self, mock_batch_store, mock_task, client):
        """POST /api/bulk-search should work like v1 extraction/bulk-search."""
        mock_batch_store.get_batch_by_idempotency_key.return_value = None
        mock_batch_store.create_batch.return_value = {
            "id": "batch-legacy-001",
            "total_items": 1,
            "status": "processing",
        }
        mock_celery_result = MagicMock()
        mock_celery_result.id = "celery-task-id"
        mock_task.delay.return_value = mock_celery_result
        mock_batch_store.client.table.return_value.update.return_value.eq.return_value.execute.return_value = None

        response = client.post("/api/bulk-search", json={"part_numbers": ["WF338109"]})
        assert response.status_code == 200
        data = response.json()
        assert data["batch_id"] == "batch-legacy-001"


@pytest.mark.integration
class TestLegacyPublishingRoutes:
    """Tests for legacy Shopify/publishing routes."""

    @patch("app.routes.publishing._get_service")
    def test_legacy_check_sku(self, mock_get_service, client):
        """GET /api/shopify/check should work like v1 publishing/check."""
        mock_service = MagicMock()
        mock_service.find_product_by_sku = AsyncMock(return_value="99001")
        mock_get_service.return_value = mock_service

        response = client.get("/api/shopify/check", params={"sku": "WF338109"})
        assert response.status_code == 200
        data = response.json()
        assert data["shopifyProductId"] == "99001"

    @patch("app.routes.publishing._get_service")
    def test_legacy_update_product(self, mock_get_service, client):
        """PUT /api/shopify/products/{id} should work like v1."""
        mock_service = MagicMock()
        mock_service.update_product = AsyncMock(return_value={
            "success": True,
            "shopifyProductId": "99001",
        })
        mock_get_service.return_value = mock_service

        response = client.put(
            "/api/shopify/products/99001",
            json={"sku": "WF338109"},
        )
        assert response.status_code == 200


@pytest.mark.integration
class TestLegacyBatchRoutes:
    """Tests for legacy batch routes."""

    @patch("app.routes.batches.batch_store")
    def test_legacy_list_batches(self, mock_batch_store, client):
        """GET /api/batches should work like v1 batches."""
        mock_batch_store.list_batches.return_value = ([], 0)

        response = client.get("/api/batches")
        assert response.status_code == 200
        data = response.json()
        assert "batches" in data


@pytest.mark.integration
class TestLegacyAuthRoutes:
    """Tests for legacy auth routes."""

    def test_legacy_auth_me(self, client):
        """GET /api/auth/me should work like v1 auth/me."""
        response = client.get("/api/auth/me")
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == "test-user-id"

    @patch("app.routes.auth.global_signout_user", new_callable=AsyncMock)
    def test_legacy_auth_logout(self, mock_signout, client):
        """POST /api/auth/logout should work like v1 auth/logout."""
        mock_signout.return_value = {"success": True}

        response = client.post(
            "/api/auth/logout",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200


@pytest.mark.integration
class TestLegacySearchRoutes:
    """Tests for legacy search routes."""

    @patch("app.routes.search._service")
    def test_legacy_multi_part_search(self, mock_service, client):
        """POST /api/shopify/multi-part-search should work like v1 search/multi-part."""
        mock_service.search_multiple_skus = AsyncMock(return_value={
            "success": True,
            "found_products": [],
            "not_found_skus": ["WF338109"],
            "summary": {
                "total_requested": 1,
                "unique_searched": 1,
                "found": 0,
                "not_found": 1,
                "duplicates_removed": 0,
            },
        })

        response = client.post(
            "/api/shopify/multi-part-search",
            json={"part_numbers": ["WF338109"]},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
