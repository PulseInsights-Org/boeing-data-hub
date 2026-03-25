"""
Unit tests for Tier 3 publish deduplication in publish_product task.

Tests cover:
- Tier 1: staging record has shopify_product_id → UPDATE (existing behaviour)
- Tier 2: find_product_by_sku returns a match → UPDATE (existing behaviour)
- Tier 3: product_store lookup returns shopify_product_id → UPDATE (new)
- Tier 4: no match anywhere → CREATE (existing behaviour)
- Tier 3 fail-open: product_store query raises → falls through to CREATE
- Full 4-tier precedence ordering

Version: 1.0.0
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

PUBLISH_MODULE = "app.celery_app.tasks.publishing"


pytestmark = pytest.mark.unit


def _make_staging_record(**overrides):
    """Minimal staging record that passes publish_product validation."""
    record = {
        "sku": "WF338109",
        "title": "WF338109",
        "boeing_name": "GASKET",
        "list_price": 25.50,
        "net_price": 23.00,
        "price": 28.05,
        "inventory_quantity": 100,
        "location_summary": "Dallas Central: 100",
        "location_availabilities": [
            {"location": "Dallas Central", "quantity": 100},
        ],
        "shopify_product_id": None,
        "image_url": None,
        "boeing_image_url": None,
        "boeing_thumbnail_url": None,
        "body_html": "",
        "shopify": {},
    }
    record.update(overrides)
    return record


def _mock_settings():
    """Settings with a valid location map."""
    settings = MagicMock()
    settings.shopify_location_map = {"Dallas Central": "Dallas Central"}
    return settings


class TestTier1StagingShopifyId:
    """Tier 1: staging record already has shopify_product_id → UPDATE."""

    @patch(f"{PUBLISH_MODULE}.check_batch_completion")
    @patch(f"{PUBLISH_MODULE}.get_sync_store")
    @patch(f"{PUBLISH_MODULE}.get_settings", side_effect=_mock_settings)
    @patch(f"{PUBLISH_MODULE}.get_batch_store")
    @patch(f"{PUBLISH_MODULE}.get_image_store")
    @patch(f"{PUBLISH_MODULE}.get_product_store")
    @patch(f"{PUBLISH_MODULE}.get_staging_store")
    @patch(f"{PUBLISH_MODULE}.get_shopify_orchestrator")
    @patch(f"{PUBLISH_MODULE}.run_async")
    def test_updates_when_staging_has_shopify_id(
        self, mock_run_async, mock_orch_fn, mock_ss_fn,
        mock_ps_fn, mock_is_fn, mock_bs_fn, mock_settings_fn,
        mock_sync_fn, mock_check,
    ):
        from app.celery_app.tasks.publishing import publish_product

        record = _make_staging_record(shopify_product_id="existing-99001")

        mock_shopify = MagicMock()
        mock_orch_fn.return_value = mock_shopify
        mock_staging = MagicMock()
        mock_ss_fn.return_value = mock_staging
        mock_product = MagicMock()
        mock_ps_fn.return_value = mock_product
        mock_image = MagicMock()
        mock_is_fn.return_value = mock_image
        mock_batch = MagicMock()
        mock_bs_fn.return_value = mock_batch
        mock_sync = MagicMock()
        mock_sync_fn.return_value = mock_sync

        # run_async dispatcher: return staging record for first call, then handle rest
        call_count = {"n": 0}

        def run_async_side_effect(coro):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return record  # get_product_staging_by_part_number
            if call_count["n"] == 2:
                return {"product": {"id": "existing-99001"}}  # update_product
            return MagicMock()

        mock_run_async.side_effect = run_async_side_effect

        result = publish_product(
            "batch-001", "WF338109", "test-user", assigned_slot=5
        )

        assert result["action"] == "updated"
        assert result["shopify_product_id"] == "existing-99001"


class TestTier3ProductStoreLookup:
    """Tier 3: product_store has shopify_product_id → UPDATE instead of CREATE."""

    @patch(f"{PUBLISH_MODULE}.check_batch_completion")
    @patch(f"{PUBLISH_MODULE}.get_sync_store")
    @patch(f"{PUBLISH_MODULE}.get_settings", side_effect=_mock_settings)
    @patch(f"{PUBLISH_MODULE}.get_batch_store")
    @patch(f"{PUBLISH_MODULE}.get_image_store")
    @patch(f"{PUBLISH_MODULE}.get_product_store")
    @patch(f"{PUBLISH_MODULE}.get_staging_store")
    @patch(f"{PUBLISH_MODULE}.get_shopify_orchestrator")
    @patch(f"{PUBLISH_MODULE}.run_async")
    def test_tier3_updates_when_product_store_has_id(
        self, mock_run_async, mock_orch_fn, mock_ss_fn,
        mock_ps_fn, mock_is_fn, mock_bs_fn, mock_settings_fn,
        mock_sync_fn, mock_check,
    ):
        """When Tier 1 and Tier 2 miss but product_store has shopify_product_id → UPDATE."""
        from app.celery_app.tasks.publishing import publish_product

        record = _make_staging_record()  # no shopify_product_id

        mock_shopify = MagicMock()
        mock_orch_fn.return_value = mock_shopify
        mock_staging = MagicMock()
        mock_ss_fn.return_value = mock_staging
        mock_product = MagicMock()
        mock_ps_fn.return_value = mock_product
        mock_image = MagicMock()
        mock_is_fn.return_value = mock_image
        mock_batch = MagicMock()
        mock_bs_fn.return_value = mock_batch
        mock_sync = MagicMock()
        mock_sync_fn.return_value = mock_sync

        call_count = {"n": 0}

        def run_async_side_effect(coro):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return record  # get_product_staging_by_part_number
            if call_count["n"] == 2:
                return None  # find_product_by_sku → Tier 2 miss
            if call_count["n"] == 3:
                # Tier 3: product_store.get_product_by_part_number → HIT
                return {"sku": "WF338109", "shopify_product_id": "tier3-99001"}
            if call_count["n"] == 4:
                # update_product result
                return {"product": {"id": "tier3-99001"}}
            return MagicMock()

        mock_run_async.side_effect = run_async_side_effect

        result = publish_product(
            "batch-001", "WF338109", "test-user", assigned_slot=5
        )

        assert result["action"] == "updated"
        assert result["shopify_product_id"] == "tier3-99001"

    @patch(f"{PUBLISH_MODULE}.check_batch_completion")
    @patch(f"{PUBLISH_MODULE}.get_sync_store")
    @patch(f"{PUBLISH_MODULE}.get_settings", side_effect=_mock_settings)
    @patch(f"{PUBLISH_MODULE}.get_batch_store")
    @patch(f"{PUBLISH_MODULE}.get_image_store")
    @patch(f"{PUBLISH_MODULE}.get_product_store")
    @patch(f"{PUBLISH_MODULE}.get_staging_store")
    @patch(f"{PUBLISH_MODULE}.get_shopify_orchestrator")
    @patch(f"{PUBLISH_MODULE}.run_async")
    def test_tier3_fail_open_falls_to_create(
        self, mock_run_async, mock_orch_fn, mock_ss_fn,
        mock_ps_fn, mock_is_fn, mock_bs_fn, mock_settings_fn,
        mock_sync_fn, mock_check,
    ):
        """If Tier 3 product_store lookup raises, falls through to Tier 4 CREATE."""
        from app.celery_app.tasks.publishing import publish_product

        record = _make_staging_record()

        mock_shopify = MagicMock()
        mock_orch_fn.return_value = mock_shopify
        mock_staging = MagicMock()
        mock_ss_fn.return_value = mock_staging
        mock_product = MagicMock()
        mock_ps_fn.return_value = mock_product
        mock_image = MagicMock()
        mock_is_fn.return_value = mock_image
        mock_batch = MagicMock()
        mock_bs_fn.return_value = mock_batch
        mock_sync = MagicMock()
        mock_sync_fn.return_value = mock_sync

        call_count = {"n": 0}

        def run_async_side_effect(coro):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return record  # get_product_staging_by_part_number
            if call_count["n"] == 2:
                return None  # find_product_by_sku → Tier 2 miss
            if call_count["n"] == 3:
                raise Exception("DB connection lost")  # Tier 3 fails
            if call_count["n"] == 4:
                # publish_product (CREATE) → Tier 4
                return {"product": {"id": "new-99001"}}
            return MagicMock()

        mock_run_async.side_effect = run_async_side_effect

        result = publish_product(
            "batch-001", "WF338109", "test-user", assigned_slot=5
        )

        assert result["action"] == "created"
        assert result["shopify_product_id"] == "new-99001"


class TestTier4CreateNewProduct:
    """Tier 4: no existing product found anywhere → CREATE."""

    @patch(f"{PUBLISH_MODULE}.check_batch_completion")
    @patch(f"{PUBLISH_MODULE}.get_sync_store")
    @patch(f"{PUBLISH_MODULE}.get_settings", side_effect=_mock_settings)
    @patch(f"{PUBLISH_MODULE}.get_batch_store")
    @patch(f"{PUBLISH_MODULE}.get_image_store")
    @patch(f"{PUBLISH_MODULE}.get_product_store")
    @patch(f"{PUBLISH_MODULE}.get_staging_store")
    @patch(f"{PUBLISH_MODULE}.get_shopify_orchestrator")
    @patch(f"{PUBLISH_MODULE}.run_async")
    def test_creates_when_no_existing_product(
        self, mock_run_async, mock_orch_fn, mock_ss_fn,
        mock_ps_fn, mock_is_fn, mock_bs_fn, mock_settings_fn,
        mock_sync_fn, mock_check,
    ):
        """No Tier 1/2/3 match → CREATE new Shopify product."""
        from app.celery_app.tasks.publishing import publish_product

        record = _make_staging_record()

        mock_shopify = MagicMock()
        mock_orch_fn.return_value = mock_shopify
        mock_staging = MagicMock()
        mock_ss_fn.return_value = mock_staging
        mock_product = MagicMock()
        mock_ps_fn.return_value = mock_product
        mock_image = MagicMock()
        mock_is_fn.return_value = mock_image
        mock_batch = MagicMock()
        mock_bs_fn.return_value = mock_batch
        mock_sync = MagicMock()
        mock_sync_fn.return_value = mock_sync

        call_count = {"n": 0}

        def run_async_side_effect(coro):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return record  # get_product_staging_by_part_number
            if call_count["n"] == 2:
                return None  # find_product_by_sku → Tier 2 miss
            if call_count["n"] == 3:
                return None  # product_store → Tier 3 miss (no record)
            if call_count["n"] == 4:
                return {"product": {"id": "brand-new-99001"}}  # publish_product CREATE
            return MagicMock()

        mock_run_async.side_effect = run_async_side_effect

        result = publish_product(
            "batch-001", "WF338109", "test-user", assigned_slot=5
        )

        assert result["action"] == "created"
        assert result["shopify_product_id"] == "brand-new-99001"

    @patch(f"{PUBLISH_MODULE}.check_batch_completion")
    @patch(f"{PUBLISH_MODULE}.get_sync_store")
    @patch(f"{PUBLISH_MODULE}.get_settings", side_effect=_mock_settings)
    @patch(f"{PUBLISH_MODULE}.get_batch_store")
    @patch(f"{PUBLISH_MODULE}.get_image_store")
    @patch(f"{PUBLISH_MODULE}.get_product_store")
    @patch(f"{PUBLISH_MODULE}.get_staging_store")
    @patch(f"{PUBLISH_MODULE}.get_shopify_orchestrator")
    @patch(f"{PUBLISH_MODULE}.run_async")
    def test_creates_when_product_store_record_has_no_shopify_id(
        self, mock_run_async, mock_orch_fn, mock_ss_fn,
        mock_ps_fn, mock_is_fn, mock_bs_fn, mock_settings_fn,
        mock_sync_fn, mock_check,
    ):
        """Product exists in product_store but has no shopify_product_id → CREATE."""
        from app.celery_app.tasks.publishing import publish_product

        record = _make_staging_record()

        mock_shopify = MagicMock()
        mock_orch_fn.return_value = mock_shopify
        mock_staging = MagicMock()
        mock_ss_fn.return_value = mock_staging
        mock_product = MagicMock()
        mock_ps_fn.return_value = mock_product
        mock_image = MagicMock()
        mock_is_fn.return_value = mock_image
        mock_batch = MagicMock()
        mock_bs_fn.return_value = mock_batch
        mock_sync = MagicMock()
        mock_sync_fn.return_value = mock_sync

        call_count = {"n": 0}

        def run_async_side_effect(coro):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return record  # get_product_staging_by_part_number
            if call_count["n"] == 2:
                return None  # find_product_by_sku → Tier 2 miss
            if call_count["n"] == 3:
                # product_store → record exists but no shopify_product_id
                return {"sku": "WF338109", "shopify_product_id": None}
            if call_count["n"] == 4:
                return {"product": {"id": "new-99002"}}  # CREATE
            return MagicMock()

        mock_run_async.side_effect = run_async_side_effect

        result = publish_product(
            "batch-001", "WF338109", "test-user", assigned_slot=5
        )

        assert result["action"] == "created"


class TestTier2FindBySku:
    """Tier 2: find_product_by_sku returns a match → UPDATE."""

    @patch(f"{PUBLISH_MODULE}.check_batch_completion")
    @patch(f"{PUBLISH_MODULE}.get_sync_store")
    @patch(f"{PUBLISH_MODULE}.get_settings", side_effect=_mock_settings)
    @patch(f"{PUBLISH_MODULE}.get_batch_store")
    @patch(f"{PUBLISH_MODULE}.get_image_store")
    @patch(f"{PUBLISH_MODULE}.get_product_store")
    @patch(f"{PUBLISH_MODULE}.get_staging_store")
    @patch(f"{PUBLISH_MODULE}.get_shopify_orchestrator")
    @patch(f"{PUBLISH_MODULE}.run_async")
    def test_tier2_updates_when_sku_found_in_shopify(
        self, mock_run_async, mock_orch_fn, mock_ss_fn,
        mock_ps_fn, mock_is_fn, mock_bs_fn, mock_settings_fn,
        mock_sync_fn, mock_check,
    ):
        """Tier 1 misses but Tier 2 (find_product_by_sku) finds → UPDATE."""
        from app.celery_app.tasks.publishing import publish_product

        record = _make_staging_record()

        mock_shopify = MagicMock()
        mock_orch_fn.return_value = mock_shopify
        mock_staging = MagicMock()
        mock_ss_fn.return_value = mock_staging
        mock_product = MagicMock()
        mock_ps_fn.return_value = mock_product
        mock_image = MagicMock()
        mock_is_fn.return_value = mock_image
        mock_batch = MagicMock()
        mock_bs_fn.return_value = mock_batch
        mock_sync = MagicMock()
        mock_sync_fn.return_value = mock_sync

        call_count = {"n": 0}

        def run_async_side_effect(coro):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return record  # get_product_staging_by_part_number
            if call_count["n"] == 2:
                return "tier2-found-99001"  # find_product_by_sku → HIT
            if call_count["n"] == 3:
                return {"product": {"id": "tier2-found-99001"}}  # update_product
            return MagicMock()

        mock_run_async.side_effect = run_async_side_effect

        result = publish_product(
            "batch-001", "WF338109", "test-user", assigned_slot=5
        )

        assert result["action"] == "updated"
        assert result["shopify_product_id"] == "tier2-found-99001"
