"""
Pytest configuration and shared fixtures for Boeing Data Hub tests.

Provides mock clients, stores, services, and sample test data.
Version: 1.0.0
"""
import sys
from unittest.mock import MagicMock as _MagicMock

# Pre-mock transitive dependencies unavailable on Python 3.14.
# The supabase SDK pulls in storage3 -> pyiceberg, supabase_auth,
# supabase_functions — none of which build on 3.14.
# Remove any partially-cached imports first, then inject mocks.
for _key in list(sys.modules):
    if any(_key.startswith(_p) for _p in ("storage3", "supabase", "pyiceberg", "pyroaring")):
        del sys.modules[_key]

for _mod in (
    "pyiceberg", "pyiceberg.catalog", "pyiceberg.catalog.rest", "pyroaring",
    "supabase", "supabase._sync_client", "supabase._async_client",
    "supabase.client", "supabase.lib",
    "supabase_auth", "supabase_auth.errors", "supabase_auth.types",
    "supabase_functions", "supabase_functions.errors", "supabase_functions.utils",
    "storage3", "storage3._async", "storage3._sync",
    "storage3._async.analytics", "storage3._sync.analytics",
):
    sys.modules[_mod] = _MagicMock()

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# FastAPI test client
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    from app.main import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# Auth fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_cognito_token():
    """Mock Cognito JWT token for testing."""
    return (
        "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiJ0ZXN0LXVzZXItaWQiLCJlbWFpbCI6InRlc3RAdGVzdC5jb20ifQ."
        "mock-signature"
    )


@pytest.fixture
def auth_headers(mock_cognito_token):
    """Authorization headers for protected endpoints."""
    return {"Authorization": f"Bearer {mock_cognito_token}"}


@pytest.fixture
def mock_current_user():
    """Bypass auth dependency for testing."""
    return {"sub": "test-user-id", "email": "test@test.com"}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_settings():
    """Settings object with test defaults (no real credentials)."""
    from app.core.config import Settings
    return Settings(
        supabase_url="https://test.supabase.co",
        supabase_key="test-supabase-key",
        supabase_service_role_key="test-supabase-key",
        shopify_store_domain="test-store.myshopify.com",
        shopify_admin_api_token="shpat_test_token",
        shopify_api_version="2024-10",
        shopify_location_map={"Dallas Central": "Dallas Central"},
        shopify_inventory_location_codes={"Dallas Central": "1D1"},
        boeing_client_id="test-boeing-id",
        boeing_client_secret="test-boeing-secret",
        cognito_user_pool_id="us-east-1_TestPool",
        cognito_app_client_id="test-client-id",
    )


# ---------------------------------------------------------------------------
# Clients (mocked)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_shopify_client():
    """Mocked ShopifyClient (HTTP transport only)."""
    client = MagicMock()
    client.call_shopify = AsyncMock(return_value={})
    client.call_shopify_graphql = AsyncMock(return_value={})
    client.delete_product = AsyncMock(return_value=True)
    client.to_gid = MagicMock(side_effect=lambda entity, val: f"gid://shopify/{entity}/{val}")
    return client


@pytest.fixture
def mock_boeing_client():
    """Mocked BoeingClient."""
    client = MagicMock()
    client.search_products = AsyncMock(return_value={"products": []})
    client.get_product_details = AsyncMock(return_value={})
    client.get_price_availability = AsyncMock(return_value={})
    return client


@pytest.fixture
def mock_supabase_client():
    """Mocked SupabaseClient."""
    client = MagicMock()
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.insert.return_value = mock_table
    mock_table.upsert.return_value = mock_table
    mock_table.update.return_value = mock_table
    mock_table.delete.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[])
    client.client.table.return_value = mock_table
    return client


# ---------------------------------------------------------------------------
# DB Stores (mocked)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_batch_store():
    """Mocked BatchStore."""
    store = MagicMock()
    store.create_batch = MagicMock(return_value="test-batch-id")
    store.get_batch = MagicMock(return_value={"id": "test-batch-id", "status": "processing"})
    store.update_status = MagicMock()
    store.record_success = MagicMock()
    store.record_failure = MagicMock()
    return store


@pytest.fixture
def mock_raw_data_store():
    """Mocked RawDataStore."""
    store = MagicMock()
    store.upsert_raw_data = AsyncMock()
    store.get_raw_data = AsyncMock(return_value=None)
    return store


@pytest.fixture
def mock_staging_store():
    """Mocked StagingStore."""
    store = MagicMock()
    store.upsert_product_staging = AsyncMock()
    store.get_product_staging_by_part_number = AsyncMock(return_value=None)
    store.update_product_staging_shopify_id = AsyncMock()
    store.update_product_staging_image = AsyncMock()
    store.list_product_staging = AsyncMock(return_value=[])
    return store


@pytest.fixture
def mock_product_store():
    """Mocked ProductStore."""
    store = MagicMock()
    store.upsert_product = AsyncMock()
    store.get_product_by_sku = AsyncMock(return_value=None)
    store.list_products = AsyncMock(return_value=[])
    store.update_product_pricing = AsyncMock()
    return store


@pytest.fixture
def mock_image_store():
    """Mocked ImageStore."""
    store = MagicMock()
    store.upload_image_from_url = AsyncMock(
        return_value=("https://test.supabase.co/storage/img.png", "products/img.png")
    )
    return store


@pytest.fixture
def mock_sync_store():
    """Mocked SyncStore."""
    store = MagicMock()
    store.upsert_sync_schedule = MagicMock()
    store.get_due_skus = MagicMock(return_value=[])
    store.update_sync_success = MagicMock()
    store.update_sync_failure = MagicMock()
    store.mark_syncing = MagicMock()
    return store


@pytest.fixture
def mock_sync_analytics():
    """Mocked SyncAnalytics."""
    analytics = MagicMock()
    analytics.get_slot_distribution_summary = MagicMock(return_value={
        "total_products": 10,
        "active_slots": [0, 1],
        "filling_slots": [2],
        "dormant_slots": list(range(3, 24)),
    })
    analytics.get_sync_status_summary = MagicMock(return_value={
        "total_products": 10,
        "active_products": 8,
        "inactive_products": 2,
    })
    return analytics


# ---------------------------------------------------------------------------
# Shopify services (mocked)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_shopify_inventory(mock_shopify_client, mock_settings):
    """Mocked ShopifyInventoryService."""
    svc = MagicMock()
    svc.get_location_map = AsyncMock(return_value={"Dallas Central": 12345})
    svc.set_inventory_levels = AsyncMock()
    svc.set_inventory_levels_graphql = AsyncMock()
    svc.set_inventory_cost = AsyncMock()
    svc.set_product_category = AsyncMock()
    svc.create_metafield_definitions = AsyncMock()
    return svc


@pytest.fixture
def mock_shopify_orchestrator():
    """Mocked ShopifyOrchestrator."""
    orch = MagicMock()
    orch.publish_product = AsyncMock(return_value={"product": {"id": 99001, "handle": "test"}})
    orch.update_product = AsyncMock(return_value={"product": {"id": 99001}})
    orch.find_product_by_sku = AsyncMock(return_value=None)
    orch.get_variant_by_sku = AsyncMock(return_value=None)
    orch.update_product_pricing = AsyncMock(return_value={"product": {"id": 99001}})
    orch.update_inventory = AsyncMock()
    orch.update_inventory_by_location = AsyncMock()
    orch.delete_product = AsyncMock(return_value=True)
    orch.create_metafield_definitions = AsyncMock()
    return orch


# ---------------------------------------------------------------------------
# Pipeline services (mocked)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_extraction_service():
    """Mocked ExtractionService."""
    svc = MagicMock()
    svc.search = AsyncMock(return_value=[])
    svc.extract_and_stage = AsyncMock(return_value={})
    return svc


@pytest.fixture
def mock_publishing_service(mock_shopify_orchestrator, mock_staging_store, mock_product_store, mock_image_store):
    """Mocked PublishingService."""
    svc = MagicMock()
    svc.publish_product_by_part_number = AsyncMock(return_value={"success": True, "shopifyProductId": "99001"})
    svc.publish_product_for_batch = AsyncMock(return_value={
        "success": True, "shopify_product_id": "99001", "action": "created", "is_new_product": True,
    })
    svc.update_product = AsyncMock(return_value={"success": True, "shopifyProductId": "99001"})
    svc.find_product_by_sku = AsyncMock(return_value=None)
    svc.setup_metafield_definitions = AsyncMock()
    return svc


# ---------------------------------------------------------------------------
# Sample test data
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_boeing_record():
    """Sample normalized Boeing product record."""
    return {
        "sku": "WF338109",
        "title": "WF338109",
        "boeing_name": "GASKET, O-RING",
        "boeing_description": "O-Ring Gasket for hydraulic system",
        "vendor": "BDI",
        "supplier_name": "Aviall",
        "list_price": 25.50,
        "net_price": 23.00,
        "currency": "USD",
        "base_uom": "EA",
        "inventory_quantity": 150,
        "country_of_origin": "US",
        "dim_length": 2.5,
        "dim_width": 1.0,
        "dim_height": 0.5,
        "dim_uom": "IN",
        "weight": 0.1,
        "weight_unit": "lb",
        "condition": "NE",
        "location_summary": "Dallas Central: 100; Chicago Warehouse: 50",
        "location_availabilities": [
            {"location": "Dallas Central", "quantity": 100},
            {"location": "Chicago Warehouse", "quantity": 50},
        ],
        "boeing_image_url": "https://boeing.com/images/WF338109.jpg",
        "boeing_thumbnail_url": "https://boeing.com/thumbs/WF338109.jpg",
    }


@pytest.fixture
def sample_shopify_product():
    """Sample Shopify product response."""
    return {
        "product": {
            "id": 99001,
            "handle": "wf338109",
            "title": "WF338109",
            "variants": [
                {
                    "id": 55001,
                    "sku": "WF338109",
                    "price": "28.05",
                    "inventory_item_id": 77001,
                    "inventory_quantity": 150,
                }
            ],
        }
    }


@pytest.fixture
def sample_batch():
    """Sample batch record."""
    return {
        "id": "batch-001",
        "status": "processing",
        "total_count": 5,
        "success_count": 0,
        "failure_count": 0,
        "part_numbers": ["WF338109", "AN3-12A", "MS20426AD3-5", "NAS1149F0332P", "AN960C10L"],
        "user_id": "test-user-id",
        "created_at": "2025-01-15T10:00:00Z",
    }


@pytest.fixture
def sample_staging_record(sample_boeing_record):
    """Sample product staging record (after extraction + normalization)."""
    record = dict(sample_boeing_record)
    record.update({
        "user_id": "test-user-id",
        "shopify_product_id": None,
        "image_url": None,
        "image_path": None,
        "body_html": "<p>O-Ring Gasket for hydraulic system</p>",
    })
    return record
