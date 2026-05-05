"""
Integration tests for product routes.

Tests GET /api/v1/products/staging, GET /api/v1/products/published,
GET /api/v1/products/published/{id}, and GET /api/v1/products/raw-data/{pn}.
Verifies auth enforcement, Supabase client mocking, and response format.
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

    # All chainable methods return mock_table
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
class TestPublishedProducts:
    """Tests for GET /api/v1/products/published."""

    @patch("app.routes.products._get_client")
    def test_get_published_products_returns_list(self, mock_get_client, client):
        """Should return paginated list of published products."""
        product_data = [
            {
                "id": "prod-001",
                "sku": "WF338109",
                "title": "WF338109",
                "body_html": None,
                "vendor": "BDI",
                "price": 28.05,
                "cost_per_item": 25.50,
                "currency": "USD",
                "inventory_quantity": 150,
                "weight": 0.1,
                "weight_unit": "lb",
                "country_of_origin": "US",
                "dim_length": 2.5,
                "dim_width": 1.0,
                "dim_height": 0.5,
                "dim_uom": "IN",
                "shopify_product_id": "99001",
                "image_url": None,
                "created_at": "2025-01-15T10:00:00Z",
                "updated_at": "2025-01-15T10:05:00Z",
            }
        ]
        mock_get_client.return_value = _build_mock_supabase_client(
            data=product_data, count=1
        )

        response = client.get("/api/v1/products/published")
        assert response.status_code == 200
        data = response.json()
        assert "products" in data
        assert "total" in data
        assert len(data["products"]) == 1
        assert data["products"][0]["sku"] == "WF338109"

    @patch("app.routes.products._get_client")
    def test_get_published_products_empty(self, mock_get_client, client):
        """Should return empty list when no products exist."""
        mock_get_client.return_value = _build_mock_supabase_client(data=[], count=0)

        response = client.get("/api/v1/products/published")
        assert response.status_code == 200
        data = response.json()
        assert data["products"] == []
        assert data["total"] == 0

    def test_published_products_requires_auth(self, unauthenticated_client):
        """Published products without auth should return 401 or 403."""
        response = unauthenticated_client.get("/api/v1/products/published")
        assert response.status_code in (401, 403)


@pytest.mark.integration
class TestStagingProducts:
    """Tests for GET /api/v1/products/staging."""

    @patch("app.routes.products._get_client")
    def test_get_staging_products_returns_list(self, mock_get_client, client):
        """Should return paginated list of staging products."""
        staging_data = [
            {
                "sku": "WF338109",
                "title": "WF338109",
                "status": "fetched",
                "user_id": "test-user-id",
                "created_at": "2025-01-15T10:00:00Z",
            }
        ]
        mock_get_client.return_value = _build_mock_supabase_client(
            data=staging_data, count=1
        )

        response = client.get("/api/v1/products/staging")
        assert response.status_code == 200
        data = response.json()
        assert "products" in data
        assert "total" in data
        assert len(data["products"]) == 1

    @patch("app.routes.products._get_client")
    def test_get_staging_products_with_filters(self, mock_get_client, client):
        """Should support status and batch_id filters."""
        mock_get_client.return_value = _build_mock_supabase_client(data=[], count=0)

        response = client.get(
            "/api/v1/products/staging",
            params={"status": "fetched", "batch_id": "batch-001"},
        )
        assert response.status_code == 200

    def test_staging_products_requires_auth(self, unauthenticated_client):
        """Staging products without auth should return 401 or 403."""
        response = unauthenticated_client.get("/api/v1/products/staging")
        assert response.status_code in (401, 403)


@pytest.mark.integration
class TestPublishedProductDetail:
    """Tests for GET /api/v1/products/published/{product_id}."""

    @patch("app.routes.products._get_client")
    def test_get_published_product_found(self, mock_get_client, client):
        """Should return a single published product by ID."""
        product_data = [{
            "id": "prod-001",
            "sku": "WF338109",
            "title": "WF338109",
            "body_html": None,
            "vendor": "BDI",
            "price": 28.05,
            "cost_per_item": 25.50,
            "currency": "USD",
            "inventory_quantity": 150,
            "weight": 0.1,
            "weight_unit": "lb",
            "country_of_origin": "US",
            "dim_length": 2.5,
            "dim_width": 1.0,
            "dim_height": 0.5,
            "dim_uom": "IN",
            "shopify_product_id": "99001",
            "image_url": None,
            "created_at": "2025-01-15T10:00:00Z",
            "updated_at": "2025-01-15T10:05:00Z",
        }]
        mock_get_client.return_value = _build_mock_supabase_client(data=product_data)

        response = client.get("/api/v1/products/published/prod-001")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "prod-001"
        assert data["sku"] == "WF338109"

    @patch("app.routes.products._get_client")
    def test_get_published_product_not_found(self, mock_get_client, client):
        """Should return 404 when product does not exist."""
        mock_get_client.return_value = _build_mock_supabase_client(data=[])

        response = client.get("/api/v1/products/published/nonexistent")
        assert response.status_code == 404


class _FakeStagingService:
    """In-memory stand-in for StagingService used by the integration tests."""

    def __init__(self, upsert_response=None):
        self.upsert_response = upsert_response or {}
        self.upsert_calls = []
        self.status_calls = []

    async def upsert_product(self, product_id, payload, user_id):
        self.upsert_calls.append(
            {"product_id": product_id, "payload": payload, "user_id": user_id}
        )
        return self.upsert_response

    async def update_status(self, product_id, status, user_id):
        self.status_calls.append(
            {"product_id": product_id, "status": status, "user_id": user_id}
        )


@pytest.mark.integration
class TestStagingProductUpsert:
    """Tests for PUT /api/v1/products/staging/{product_id}."""

    def test_upsert_returns_product(self, client):
        """Should return the upserted row from the service layer."""
        from app.container import get_staging_service
        from app.main import app

        upserted = {
            "id": "prod-001",
            "sku": "WF338109",
            "title": "Updated title",
            "user_id": "test-user-id",
            "status": "enriched",
        }
        fake = _FakeStagingService(upsert_response=upserted)
        app.dependency_overrides[get_staging_service] = lambda: fake
        try:
            response = client.put(
                "/api/v1/products/staging/prod-001",
                json={
                    "sku": "WF338109",
                    "title": "Updated title",
                    "status": "enriched",
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["product"]["id"] == "prod-001"
            assert data["product"]["title"] == "Updated title"

            assert len(fake.upsert_calls) == 1
            call = fake.upsert_calls[0]
            assert call["product_id"] == "prod-001"
            assert call["user_id"] == "test-user-id"
            assert call["payload"]["sku"] == "WF338109"
            assert call["payload"]["title"] == "Updated title"
            assert call["payload"]["status"] == "enriched"
        finally:
            app.dependency_overrides.pop(get_staging_service, None)

    def test_upsert_requires_auth(self, unauthenticated_client):
        """Upsert without auth should return 401 or 403."""
        response = unauthenticated_client.put(
            "/api/v1/products/staging/prod-001",
            json={"sku": "WF338109"},
        )
        assert response.status_code in (401, 403)


@pytest.mark.integration
class TestStagingProductStatus:
    """Tests for PATCH /api/v1/products/staging/{product_id}/status."""

    def test_status_update_succeeds(self, client):
        """Should call the service with the right args and return success."""
        from app.container import get_staging_service
        from app.main import app

        fake = _FakeStagingService()
        app.dependency_overrides[get_staging_service] = lambda: fake
        try:
            response = client.patch(
                "/api/v1/products/staging/prod-001/status",
                json={"status": "published"},
            )
            assert response.status_code == 200
            assert response.json() == {"success": True}
            assert fake.status_calls == [
                {
                    "product_id": "prod-001",
                    "status": "published",
                    "user_id": "test-user-id",
                }
            ]
        finally:
            app.dependency_overrides.pop(get_staging_service, None)

    def test_status_update_requires_auth(self, unauthenticated_client):
        """Status update without auth should return 401 or 403."""
        response = unauthenticated_client.patch(
            "/api/v1/products/staging/prod-001/status",
            json={"status": "published"},
        )
        assert response.status_code in (401, 403)
