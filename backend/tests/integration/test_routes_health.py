"""
Integration tests for the health check endpoint.

Verifies GET /health returns 200 with status "healthy".
No authentication required for this endpoint.
Version: 1.0.0
"""
import os
import pytest
from unittest.mock import patch

os.environ.setdefault("AUTO_START_CELERY", "false")

from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client with Celery auto-start disabled."""
    with patch.dict(os.environ, {"AUTO_START_CELERY": "false"}):
        from app.main import app
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


@pytest.mark.integration
class TestHealthRoutes:
    """Integration tests for the /health endpoint."""

    def test_health_returns_200(self, client):
        """GET /health should return HTTP 200."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_status_healthy(self, client):
        """GET /health should include status 'healthy' in JSON body."""
        response = client.get("/health")
        data = response.json()
        assert data["status"] == "healthy"

    def test_health_response_is_json(self, client):
        """GET /health should return a valid JSON response."""
        response = client.get("/health")
        assert response.headers["content-type"] == "application/json"
        data = response.json()
        assert isinstance(data, dict)

    def test_health_no_auth_required(self, client):
        """GET /health should NOT require authentication headers."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_wrong_method_not_allowed(self, client):
        """POST /health should return 405 Method Not Allowed."""
        response = client.post("/health")
        assert response.status_code == 405
