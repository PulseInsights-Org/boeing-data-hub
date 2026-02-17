"""
Integration tests for auth routes.

Tests GET /api/v1/auth/me and POST /api/v1/auth/logout.
Verifies auth enforcement, header parsing, and response format.
Version: 1.0.0
"""
import os
import pytest
from unittest.mock import patch, AsyncMock

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
            "groups": ["admin"],
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
class TestAuthMe:
    """Tests for GET /api/v1/auth/me."""

    def test_me_returns_user_info(self, client):
        """Should return current user info from the JWT token."""
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == "test-user-id"
        assert data["email"] == "test@test.com"
        assert data["username"] == "testuser"
        assert "admin" in data["groups"]

    def test_me_requires_auth(self, unauthenticated_client):
        """GET /auth/me without auth should return 401 or 403."""
        response = unauthenticated_client.get("/api/v1/auth/me")
        assert response.status_code in (401, 403)


@pytest.mark.integration
class TestAuthLogout:
    """Tests for POST /api/v1/auth/logout."""

    @patch("app.routes.auth.global_signout_user", new_callable=AsyncMock)
    def test_logout_with_valid_token(self, mock_signout, client):
        """Logout with a valid Bearer token should call global sign-out."""
        mock_signout.return_value = {"success": True}

        response = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": "Bearer test-access-token-123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["global_signout_success"] is True
        mock_signout.assert_called_once_with("test-access-token-123")

    @patch("app.routes.auth.global_signout_user", new_callable=AsyncMock)
    def test_logout_cognito_failure_still_returns_success(self, mock_signout, client):
        """Logout should return success even if Cognito global sign-out fails."""
        mock_signout.return_value = {"success": False, "error": "Token expired"}

        response = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": "Bearer expired-token"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["global_signout_success"] is False

    def test_logout_missing_authorization_header(self, client):
        """Logout without Authorization header should return 401."""
        response = client.post("/api/v1/auth/logout")
        assert response.status_code == 401

    def test_logout_invalid_authorization_format(self, client):
        """Logout with non-Bearer auth header should return 401."""
        response = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert response.status_code == 401
