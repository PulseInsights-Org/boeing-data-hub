"""
Unit tests for CORS middleware configuration.
Version: 1.0.0
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.middleware import apply_cors


@pytest.mark.unit
class TestApplyCors:
    """Tests for the apply_cors middleware function."""

    def test_apply_cors_adds_middleware(self):
        """CORS middleware is attached to the FastAPI app."""
        app = FastAPI()
        apply_cors(app)
        # Middleware stack should have CORSMiddleware
        middleware_classes = [m.cls.__name__ for m in app.user_middleware]
        assert "CORSMiddleware" in middleware_classes

    def test_cors_allows_any_origin(self):
        """Wildcard origin is allowed."""
        app = FastAPI()
        apply_cors(app)

        @app.get("/test")
        def test_endpoint():
            return {"ok": True}

        client = TestClient(app)
        resp = client.get("/test", headers={"Origin": "https://example.com"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "*"

    def test_cors_preflight(self):
        """OPTIONS preflight requests are handled."""
        app = FastAPI()
        apply_cors(app)

        @app.post("/test")
        def test_endpoint():
            return {"ok": True}

        client = TestClient(app)
        resp = client.options(
            "/test",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert resp.status_code == 200
