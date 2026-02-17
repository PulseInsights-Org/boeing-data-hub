"""
Integration tests for search routes.

Tests POST /api/v1/search/multi-part.
Verifies request validation, service delegation, and response format.
Note: This endpoint does NOT require auth (no Depends(get_current_user)).
Version: 1.0.0
"""
import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

os.environ.setdefault("AUTO_START_CELERY", "false")

from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client."""
    with patch.dict(os.environ, {"AUTO_START_CELERY": "false"}):
        from app.main import app
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


@pytest.mark.integration
class TestMultiPartSearch:
    """Tests for POST /api/v1/search/multi-part."""

    @patch("app.routes.search._service")
    def test_multi_part_search_finds_products(self, mock_service, client):
        """Search with valid SKUs should return found products."""
        mock_service.search_multiple_skus = AsyncMock(return_value={
            "success": True,
            "found_products": [
                {
                    "sku": "WF338109",
                    "shopify_product_id": "99001",
                    "shopify_variant_id": "55001",
                    "title": "WF338109",
                    "handle": "wf338109",
                    "status": "active",
                    "price": "28.05",
                    "compare_at_price": None,
                    "inventory_quantity": 150,
                    "vendor": "BDI",
                    "product_type": None,
                    "description": None,
                    "tags": [],
                    "images": [],
                    "metafields": [],
                },
            ],
            "not_found_skus": ["NONEXISTENT"],
            "summary": {
                "total_requested": 2,
                "unique_searched": 2,
                "found": 1,
                "not_found": 1,
                "duplicates_removed": 0,
            },
        })

        response = client.post(
            "/api/v1/search/multi-part",
            json={"part_numbers": ["WF338109", "NONEXISTENT"]},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["found_products"]) == 1
        assert data["found_products"][0]["sku"] == "WF338109"
        assert "NONEXISTENT" in data["not_found_skus"]

    @patch("app.routes.search._service")
    def test_multi_part_search_empty_results(self, mock_service, client):
        """Search with no matching SKUs should return empty found list."""
        mock_service.search_multiple_skus = AsyncMock(return_value={
            "success": True,
            "found_products": [],
            "not_found_skus": ["FAKE1", "FAKE2"],
            "summary": {
                "total_requested": 2,
                "unique_searched": 2,
                "found": 0,
                "not_found": 2,
                "duplicates_removed": 0,
            },
        })

        response = client.post(
            "/api/v1/search/multi-part",
            json={"part_numbers": ["FAKE1", "FAKE2"]},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["summary"]["found"] == 0
        assert data["summary"]["not_found"] == 2

    def test_multi_part_search_empty_list_returns_422(self, client):
        """Search with empty part_numbers should return 422."""
        response = client.post(
            "/api/v1/search/multi-part",
            json={"part_numbers": []},
        )
        assert response.status_code == 422

    def test_multi_part_search_missing_field_returns_422(self, client):
        """Search without part_numbers field should return 422."""
        response = client.post("/api/v1/search/multi-part", json={})
        assert response.status_code == 422

    @patch("app.routes.search._service")
    def test_multi_part_search_summary_counts(self, mock_service, client):
        """Summary should include correct totals."""
        mock_service.search_multiple_skus = AsyncMock(return_value={
            "success": True,
            "found_products": [
                {
                    "sku": "WF338109",
                    "shopify_product_id": "99001",
                    "shopify_variant_id": "55001",
                    "title": "WF338109",
                    "handle": "wf338109",
                    "status": "active",
                    "tags": [],
                    "images": [],
                    "metafields": [],
                },
            ],
            "not_found_skus": [],
            "summary": {
                "total_requested": 3,
                "unique_searched": 1,
                "found": 1,
                "not_found": 0,
                "duplicates_removed": 2,
            },
        })

        response = client.post(
            "/api/v1/search/multi-part",
            json={"part_numbers": ["WF338109", "WF338109", "WF338109"]},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["summary"]["total_requested"] == 3
        assert data["summary"]["duplicates_removed"] == 2
