"""
Integration tests for sync routes.

Tests GET /api/v1/sync/dashboard, GET /api/v1/sync/products,
GET /api/v1/sync/history, GET /api/v1/sync/failures,
GET /api/v1/sync/hourly-stats, and GET /api/v1/sync/product/{sku}.
Verifies auth enforcement, sync store mocking, and response format.
Version: 1.0.0
"""
import os
import pytest
from unittest.mock import patch, MagicMock

os.environ.setdefault("AUTO_START_CELERY", "false")

from fastapi.testclient import TestClient


def _build_mock_supabase_client(data=None, count=0):
    """Build a fully chainable mock Supabase client."""
    mock_client = MagicMock()
    mock_table = MagicMock()
    mock_response = MagicMock()
    mock_response.data = data or []
    mock_response.count = count

    for method in ("select", "eq", "order", "range", "limit", "ilike", "gte", "gt"):
        getattr(mock_table, method).return_value = mock_table
    mock_table.execute.return_value = mock_response
    mock_client.table.return_value = mock_table
    return mock_client


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
class TestSyncDashboard:
    """Tests for GET /api/v1/sync/dashboard."""

    @patch("app.routes.sync.get_sync_store")
    def test_dashboard_returns_complete_data(self, mock_get_sync_store, client):
        """Dashboard should return all required fields."""
        mock_store = MagicMock()
        mock_store.get_sync_status_summary.return_value = {
            "total_products": 50,
            "active_products": 45,
            "inactive_products": 5,
            "success_rate_percent": 92.5,
            "high_failure_count": 2,
            "status_counts": {
                "pending": 5,
                "syncing": 3,
                "success": 40,
                "failed": 2,
            },
        }
        mock_store.get_slot_distribution_summary.return_value = {
            "slot_counts": {0: 10, 1: 15, 2: 5},
            "active_slots": [0, 1],
            "filling_slots": [2],
            "active_count": 2,
            "filling_count": 1,
            "dormant_count": 3,
            "efficiency_percent": 85.0,
        }
        mock_get_sync_store.return_value = mock_store

        response = client.get("/api/v1/sync/dashboard")
        assert response.status_code == 200
        data = response.json()
        assert data["total_products"] == 50
        assert data["active_products"] == 45
        assert "status_counts" in data
        assert "slot_distribution" in data
        assert isinstance(data["slot_distribution"], list)

    def test_dashboard_requires_auth(self, unauthenticated_client):
        """Dashboard without auth should return 401 or 403."""
        response = unauthenticated_client.get("/api/v1/sync/dashboard")
        assert response.status_code in (401, 403)


@pytest.mark.integration
class TestSyncProducts:
    """Tests for GET /api/v1/sync/products."""

    @patch("app.routes.sync._get_client")
    def test_get_sync_products_returns_list(self, mock_get_client, client):
        """Should return paginated list of sync products."""
        product_data = [{
            "id": 1,
            "sku": "WF338109",
            "user_id": "test-user-id",
            "hour_bucket": 3,
            "sync_status": "success",
            "last_sync_at": "2025-01-15T10:00:00Z",
            "consecutive_failures": 0,
            "last_error": None,
            "last_price": 25.50,
            "last_quantity": 150,
            "last_inventory_status": "in_stock",
            "last_location_summary": "Dallas Central: 100",
            "is_active": True,
            "created_at": "2025-01-10T10:00:00Z",
            "updated_at": "2025-01-15T10:00:00Z",
        }]
        mock_get_client.return_value = _build_mock_supabase_client(
            data=product_data, count=1
        )

        response = client.get("/api/v1/sync/products")
        assert response.status_code == 200
        data = response.json()
        assert "products" in data
        assert "total" in data
        assert len(data["products"]) == 1
        assert data["products"][0]["sku"] == "WF338109"

    def test_sync_products_requires_auth(self, unauthenticated_client):
        """Sync products without auth should return 401 or 403."""
        response = unauthenticated_client.get("/api/v1/sync/products")
        assert response.status_code in (401, 403)


@pytest.mark.integration
class TestSyncHistory:
    """Tests for GET /api/v1/sync/history."""

    @patch("app.routes.sync._get_client")
    def test_get_sync_history_returns_items(self, mock_get_client, client):
        """Should return recent sync history."""
        history_data = [{
            "sku": "WF338109",
            "sync_status": "success",
            "last_sync_at": "2025-01-15T10:00:00Z",
            "last_price": 25.50,
            "last_quantity": 150,
            "last_inventory_status": "in_stock",
            "last_error": None,
            "hour_bucket": 3,
        }]
        mock_get_client.return_value = _build_mock_supabase_client(
            data=history_data, count=1
        )

        response = client.get("/api/v1/sync/history")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert len(data["items"]) == 1

    def test_sync_history_requires_auth(self, unauthenticated_client):
        """Sync history without auth should return 401 or 403."""
        response = unauthenticated_client.get("/api/v1/sync/history")
        assert response.status_code in (401, 403)


@pytest.mark.integration
class TestSyncFailures:
    """Tests for GET /api/v1/sync/failures."""

    @patch("app.routes.sync._get_client")
    def test_get_failures_returns_failed_products(self, mock_get_client, client):
        """Should return products with sync failures."""
        failure_data = [{
            "sku": "AN3-12A",
            "consecutive_failures": 3,
            "last_error": "Timeout connecting to Boeing API",
            "last_sync_at": "2025-01-15T10:00:00Z",
            "hour_bucket": 5,
            "is_active": True,
        }]
        mock_get_client.return_value = _build_mock_supabase_client(
            data=failure_data, count=1
        )

        response = client.get("/api/v1/sync/failures")
        assert response.status_code == 200
        data = response.json()
        assert "products" in data
        assert len(data["products"]) == 1
        assert data["products"][0]["consecutive_failures"] == 3


@pytest.mark.integration
class TestSyncHourlyStats:
    """Tests for GET /api/v1/sync/hourly-stats."""

    @patch("app.routes.sync._get_client")
    def test_hourly_stats_returns_all_hours(self, mock_get_client, client):
        """Should return stats for all hour buckets."""
        mock_get_client.return_value = _build_mock_supabase_client(data=[])

        response = client.get("/api/v1/sync/hourly-stats")
        assert response.status_code == 200
        data = response.json()
        assert "hours" in data
        assert "current_hour" in data
        assert isinstance(data["hours"], list)
