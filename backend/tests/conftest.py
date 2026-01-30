"""
Pytest configuration and fixtures for Boeing Data Hub backend tests.
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def mock_cognito_token():
    """Mock Cognito JWT token for testing."""
    return "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0LXVzZXItaWQiLCJlbWFpbCI6InRlc3RAdGVzdC5jb20ifQ.mock-signature"


@pytest.fixture
def auth_headers(mock_cognito_token):
    """Create Authorization headers for testing."""
    return {"Authorization": f"Bearer {mock_cognito_token}"}
