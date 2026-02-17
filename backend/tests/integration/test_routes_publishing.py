"""
Integration tests for publishing routes.

Tests POST /api/v1/publishing/publish, PUT /api/v1/publishing/products/{id},
GET /api/v1/publishing/check, and POST /api/v1/publishing/metafields/setup.
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
class TestPublishProduct:
    """Tests for POST /api/v1/publishing/publish."""

    @patch("app.celery_app.tasks.publishing.publish_batch")
    @patch("app.routes.publishing.batch_store")
    def test_publish_creates_batch_and_queues_task(self, mock_batch_store, mock_pub_batch, client):
        """Publish should create a batch and queue the Celery task."""
        mock_batch_store.create_batch.return_value = {
            "id": "pub-batch-001",
            "total_items": 1,
            "status": "processing",
        }
        mock_celery_result = MagicMock()
        mock_celery_result.id = "celery-pub-task-id"
        mock_pub_batch.delay.return_value = mock_celery_result
        mock_batch_store.client.table.return_value.update.return_value.eq.return_value.execute.return_value = None

        response = client.post(
            "/api/v1/publishing/publish",
            json={"part_number": "WF338109"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["batch_id"] == "pub-batch-001"

    def test_publish_requires_auth(self, unauthenticated_client):
        """Publish without auth should return 401 or 403."""
        response = unauthenticated_client.post(
            "/api/v1/publishing/publish",
            json={"part_number": "WF338109"},
        )
        assert response.status_code in (401, 403)

    def test_publish_missing_part_number_returns_422(self, client):
        """Publish without part_number should return 422."""
        response = client.post("/api/v1/publishing/publish", json={})
        assert response.status_code == 422


@pytest.mark.integration
class TestUpdateProduct:
    """Tests for PUT /api/v1/publishing/products/{id}."""

    @patch("app.routes.publishing._get_service")
    def test_update_product_success(self, mock_get_service, client):
        """Update product should delegate to publishing service."""
        mock_service = MagicMock()
        mock_service.update_product = AsyncMock(return_value={
            "success": True,
            "shopifyProductId": "99001",
        })
        mock_get_service.return_value = mock_service

        response = client.put(
            "/api/v1/publishing/products/99001",
            json={"sku": "WF338109", "title": "Updated Title"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["shopifyProductId"] == "99001"

    def test_update_product_requires_auth(self, unauthenticated_client):
        """Update product without auth should return 401 or 403."""
        response = unauthenticated_client.put(
            "/api/v1/publishing/products/99001",
            json={"sku": "WF338109"},
        )
        assert response.status_code in (401, 403)


@pytest.mark.integration
class TestCheckSKU:
    """Tests for GET /api/v1/publishing/check."""

    @patch("app.routes.publishing._get_service")
    def test_check_sku_found(self, mock_get_service, client):
        """Check SKU that exists should return the Shopify product ID."""
        mock_service = MagicMock()
        mock_service.find_product_by_sku = AsyncMock(return_value="99001")
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/publishing/check", params={"sku": "WF338109"})
        assert response.status_code == 200
        data = response.json()
        assert data["shopifyProductId"] == "99001"

    @patch("app.routes.publishing._get_service")
    def test_check_sku_not_found(self, mock_get_service, client):
        """Check SKU that does not exist should return null shopifyProductId."""
        mock_service = MagicMock()
        mock_service.find_product_by_sku = AsyncMock(return_value=None)
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/publishing/check", params={"sku": "NONEXISTENT"})
        assert response.status_code == 200
        data = response.json()
        assert data["shopifyProductId"] is None

    def test_check_sku_requires_auth(self, unauthenticated_client):
        """Check SKU without auth should return 401 or 403."""
        response = unauthenticated_client.get(
            "/api/v1/publishing/check",
            params={"sku": "WF338109"},
        )
        assert response.status_code in (401, 403)

    def test_check_sku_missing_param_returns_422(self, client):
        """Check SKU without sku parameter should return 422."""
        response = client.get("/api/v1/publishing/check")
        assert response.status_code == 422


@pytest.mark.integration
class TestSetupMetafields:
    """Tests for POST /api/v1/publishing/metafields/setup."""

    @patch("app.routes.publishing._get_service")
    def test_setup_metafields_success(self, mock_get_service, client):
        """Setup metafields should return success."""
        mock_service = MagicMock()
        mock_service.setup_metafield_definitions = AsyncMock()
        mock_get_service.return_value = mock_service

        response = client.post("/api/v1/publishing/metafields/setup")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_setup_metafields_requires_auth(self, unauthenticated_client):
        """Setup metafields without auth should return 401 or 403."""
        response = unauthenticated_client.post("/api/v1/publishing/metafields/setup")
        assert response.status_code in (401, 403)
