"""
Phase 9 — Full cleanup and self-contained migration verification.

Verifies that Phase 9 cleanup is complete:
1. No old files exist (services, schemas, routes)
2. No routes/v1/ directory exists
3. Services are self-contained (no inheritance from old services)
4. Schemas are self-contained (no old schema imports)
5. Routes are flat in routes/ directory
6. main.py uses single aggregated router
7. container.py uses pipeline-named services directly
8. All pipeline services can be imported
9. No imports from deleted files anywhere in app/

Run with: pytest tests/test_phase9_cleanup.py -v --noconftest
"""
import ast
import os

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BACKEND = os.path.join(os.path.dirname(__file__), "..")
APP = os.path.join(BACKEND, "app")


def _read_source(rel_path: str) -> str:
    """Read a source file relative to backend/app/."""
    full = os.path.join(APP, rel_path)
    assert os.path.isfile(full), f"File not found: {rel_path}"
    with open(full, encoding="utf-8") as f:
        return f.read()


def _all_py_files(root: str) -> list[str]:
    """Return all .py files under root (relative paths), excluding __pycache__."""
    result = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for f in filenames:
            if f.endswith(".py"):
                result.append(os.path.relpath(os.path.join(dirpath, f), APP))
    return result


def _get_imports(source: str) -> list[str]:
    """Extract all import module paths from source."""
    tree = ast.parse(source)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


# =========================================================================
# 1. Old files deleted
# =========================================================================

class TestOldServicesDeleted:
    """Old service files have been removed."""

    OLD_SERVICES = [
        "services/boeing_service.py",
        "services/shopify_service.py",
        "services/zap_service.py",
        "services/cognito_admin.py",
    ]

    @pytest.mark.parametrize("service_file", OLD_SERVICES)
    def test_old_service_deleted(self, service_file):
        path = os.path.join(APP, service_file)
        assert not os.path.isfile(path), \
            f"Old service file {service_file} should be deleted"


class TestOldSchemasDeleted:
    """Old schema files have been removed."""

    OLD_SCHEMAS = [
        "schemas/boeing.py",
        "schemas/shopify.py",
        "schemas/bulk.py",
        "schemas/zap.py",
    ]

    @pytest.mark.parametrize("schema_file", OLD_SCHEMAS)
    def test_old_schema_deleted(self, schema_file):
        path = os.path.join(APP, schema_file)
        assert not os.path.isfile(path), \
            f"Old schema file {schema_file} should be deleted"


class TestOldRoutesDeleted:
    """Old route files have been removed."""

    OLD_ROUTES = [
        "routes/boeing.py",
        "routes/shopify.py",
        "routes/bulk.py",
        "routes/zap.py",
        "routes/multi_part_search.py",
    ]

    @pytest.mark.parametrize("route_file", OLD_ROUTES)
    def test_old_route_deleted(self, route_file):
        path = os.path.join(APP, route_file)
        assert not os.path.isfile(path), \
            f"Old route file {route_file} should be deleted"


class TestV1DirectoryDeleted:
    """routes/v1/ directory no longer exists."""

    def test_no_v1_directory(self):
        v1_dir = os.path.join(APP, "routes/v1")
        assert not os.path.isdir(v1_dir), \
            "routes/v1/ directory should be deleted"


# =========================================================================
# 2. Services are self-contained
# =========================================================================

class TestServicesSelfContained:
    """Pipeline services own their logic directly (no inheritance)."""

    def test_extraction_no_boeing_service(self):
        source = _read_source("services/extraction_service.py")
        assert "BoeingService" not in source
        assert "from app.services.boeing_service" not in source

    def test_publishing_no_shopify_service(self):
        source = _read_source("services/publishing_service.py")
        assert "ShopifyService" not in source
        assert "from app.services.shopify_service" not in source

    def test_webhook_no_zap_service(self):
        source = _read_source("services/webhook_service.py")
        assert "ZapService" not in source
        assert "from app.services.zap_service" not in source

    def test_auth_no_cognito_admin(self):
        source = _read_source("services/auth_service.py")
        assert "cognito_admin" not in source
        assert "from app.services.cognito_admin" not in source

    def test_extraction_has_own_class(self):
        source = _read_source("services/extraction_service.py")
        assert "class ExtractionService:" in source

    def test_publishing_has_own_class(self):
        source = _read_source("services/publishing_service.py")
        assert "class PublishingService:" in source

    def test_webhook_has_own_class(self):
        source = _read_source("services/webhook_service.py")
        assert "class WebhookService:" in source

    def test_auth_has_own_class(self):
        source = _read_source("services/auth_service.py")
        assert "class AuthService:" in source


# =========================================================================
# 3. Schemas are self-contained
# =========================================================================

class TestSchemasSelfContained:
    """Pipeline schemas define their own classes (no re-exports)."""

    def test_extraction_no_boeing_import(self):
        imports = _get_imports(_read_source("schemas/extraction.py"))
        assert not any("schemas.boeing" in i for i in imports)

    def test_publishing_no_shopify_import(self):
        imports = _get_imports(_read_source("schemas/publishing.py"))
        assert not any("schemas.shopify" in i for i in imports)

    def test_batches_no_bulk_import(self):
        imports = _get_imports(_read_source("schemas/batches.py"))
        assert not any("schemas.bulk" in i for i in imports)

    def test_webhooks_no_zap_import(self):
        imports = _get_imports(_read_source("schemas/webhooks.py"))
        assert not any("schemas.zap" in i for i in imports)

    def test_extraction_defines_class(self):
        source = _read_source("schemas/extraction.py")
        assert "class ExtractionSearchResponse" in source

    def test_publishing_defines_classes(self):
        source = _read_source("schemas/publishing.py")
        assert "class PublishRequest" in source
        assert "class PublishResponse" in source

    def test_batches_defines_classes(self):
        source = _read_source("schemas/batches.py")
        assert "class BulkSearchRequest" in source
        assert "class BulkPublishRequest" in source
        assert "class BatchStatusResponse" in source

    def test_webhooks_defines_classes(self):
        source = _read_source("schemas/webhooks.py")
        assert "class WebhookRequest" in source
        assert "class QuoteItem" in source
        assert "class QuotePayload" in source


# =========================================================================
# 4. Routes are flat
# =========================================================================

class TestFlatRouteStructure:
    """All route files are directly in routes/ (no v1/ subfolder)."""

    EXPECTED_ROUTES = [
        "routes/extraction.py",
        "routes/publishing.py",
        "routes/batches.py",
        "routes/products.py",
        "routes/sync.py",
        "routes/search.py",
        "routes/webhooks.py",
        "routes/auth.py",
        "routes/health.py",
        "routes/__init__.py",
    ]

    @pytest.mark.parametrize("route_file", EXPECTED_ROUTES)
    def test_route_exists(self, route_file):
        path = os.path.join(APP, route_file)
        assert os.path.isfile(path), f"Route file {route_file} not found"

    def test_init_has_v1_router(self):
        source = _read_source("routes/__init__.py")
        assert "v1_router" in source
        assert "APIRouter" in source


# =========================================================================
# 5. Routes are self-contained (no delegation)
# =========================================================================

class TestRoutesSelfContained:
    """Routes do not delegate to old route files."""

    def test_no_bulk_imports(self):
        """No route file imports from the deleted bulk.py."""
        for route in ["extraction.py", "publishing.py", "batches.py", "products.py"]:
            source = _read_source(f"routes/{route}")
            assert "app.routes.bulk" not in source, \
                f"routes/{route} still imports from deleted bulk.py"

    def test_no_multi_part_search_import(self):
        source = _read_source("routes/search.py")
        assert "app.routes.multi_part_search" not in source

    def test_no_old_sync_import(self):
        """Sync route should not import old sync route handlers."""
        source = _read_source("routes/sync.py")
        imports = _get_imports(source)
        # Should not have imports like "from app.routes.sync import ..."
        # (which would be circular anyway since this IS routes/sync.py)
        old_sync_imports = [i for i in imports if i == "app.routes.sync"]
        assert len(old_sync_imports) == 0

    def test_no_old_route_imports_anywhere(self):
        """No route file imports from deleted old route files."""
        deleted_modules = [
            "app.routes.boeing",
            "app.routes.shopify",
            "app.routes.zap",
            "app.routes.bulk",
            "app.routes.multi_part_search",
        ]
        for route in os.listdir(os.path.join(APP, "routes")):
            if not route.endswith(".py"):
                continue
            source = _read_source(f"routes/{route}")
            imports = _get_imports(source)
            for deleted in deleted_modules:
                assert deleted not in imports, \
                    f"routes/{route} imports from deleted {deleted}"


# =========================================================================
# 6. main.py uses aggregated router
# =========================================================================

class TestMainPyClean:
    """main.py uses single v1_router import, no old imports."""

    def setup_method(self):
        self.source = _read_source("main.py")
        self.imports = _get_imports(self.source)

    def test_imports_v1_router(self):
        assert "app.routes" in self.imports

    def test_no_old_router_imports(self):
        old_imports = [
            "app.routes.boeing",
            "app.routes.shopify",
            "app.routes.zap",
            "app.routes.bulk",
            "app.routes.multi_part_search",
        ]
        for old in old_imports:
            assert old not in self.imports, \
                f"main.py still imports from deleted {old}"

    def test_no_old_service_imports(self):
        old_services = [
            "app.services.boeing_service",
            "app.services.shopify_service",
            "app.services.zap_service",
            "app.services.cognito_admin",
        ]
        for old in old_services:
            assert old not in self.imports, \
                f"main.py still imports from deleted {old}"

    def test_uses_include_router(self):
        assert "include_router" in self.source


# =========================================================================
# 7. container.py uses pipeline services
# =========================================================================

class TestContainerPipelineServices:
    """container.py uses pipeline-named service classes."""

    def setup_method(self):
        self.source = _read_source("container.py")

    def test_uses_extraction_service(self):
        assert "ExtractionService" in self.source

    def test_uses_publishing_service(self):
        assert "PublishingService" in self.source

    def test_uses_webhook_service(self):
        assert "WebhookService" in self.source

    def test_no_old_service_names(self):
        assert "BoeingService" not in self.source
        assert "ShopifyService" not in self.source
        assert "ZapService" not in self.source

    def test_no_old_service_imports(self):
        imports = _get_imports(self.source)
        old_modules = [
            "app.services.boeing_service",
            "app.services.shopify_service",
            "app.services.zap_service",
            "app.services.cognito_admin",
        ]
        for old in old_modules:
            assert old not in imports, \
                f"container.py imports from deleted {old}"


# =========================================================================
# 8. No imports from deleted files anywhere
# =========================================================================

class TestNoDeadImports:
    """No file in app/ imports from deleted files."""

    DELETED_MODULES = [
        "app.services.boeing_service",
        "app.services.shopify_service",
        "app.services.zap_service",
        "app.services.cognito_admin",
        "app.schemas.boeing",
        "app.schemas.shopify",
        "app.schemas.bulk",
        "app.schemas.zap",
        "app.routes.boeing",
        "app.routes.shopify",
        "app.routes.zap",
        "app.routes.bulk",
        "app.routes.multi_part_search",
    ]

    def test_no_dead_imports_in_app(self):
        all_files = _all_py_files(APP)
        violations = []
        for rel_path in all_files:
            try:
                source = _read_source(rel_path)
                imports = _get_imports(source)
            except (AssertionError, SyntaxError):
                continue
            for deleted in self.DELETED_MODULES:
                if deleted in imports:
                    violations.append(f"{rel_path} imports {deleted}")
        assert not violations, \
            f"Dead imports found:\n" + "\n".join(violations)


# =========================================================================
# 9. Backward-compat aliases still exist
# =========================================================================

class TestBackwardCompatAliases:
    """Backward-compat aliases preserved for external consumers."""

    def test_boeing_search_response_alias(self):
        source = _read_source("schemas/extraction.py")
        assert "BoeingSearchResponse" in source

    def test_shopify_publish_aliases(self):
        source = _read_source("schemas/publishing.py")
        assert "ShopifyPublishRequest" in source
        assert "ShopifyPublishResponse" in source
        assert "ShopifyUpdateRequest" in source
        assert "ShopifyCheckResponse" in source

    def test_zap_webhook_alias(self):
        source = _read_source("schemas/webhooks.py")
        assert "ZapWebhookRequest" in source


# =========================================================================
# 10. Pipeline services importable
# =========================================================================

class TestServiceImports:
    """All pipeline services can be imported."""

    def test_import_extraction_service(self):
        from app.services.extraction_service import ExtractionService
        assert ExtractionService is not None

    def test_import_publishing_service(self):
        from app.services.publishing_service import PublishingService
        assert PublishingService is not None

    def test_import_webhook_service(self):
        from app.services.webhook_service import WebhookService
        assert WebhookService is not None

    def test_import_auth_service(self):
        from app.services.auth_service import AuthService
        assert AuthService is not None

    def test_import_search_service(self):
        from app.services.search_service import SearchService
        assert SearchService is not None

    def test_import_sync_service(self):
        from app.services.sync_service import SyncService
        assert SyncService is not None


# =========================================================================
# 11. Pipeline schemas importable
# =========================================================================

class TestSchemaImports:
    """All pipeline schemas can be imported."""

    def test_import_extraction_schema(self):
        from app.schemas.extraction import ExtractionSearchResponse
        assert ExtractionSearchResponse is not None

    def test_import_publishing_schemas(self):
        from app.schemas.publishing import PublishRequest, PublishResponse
        assert PublishRequest is not None
        assert PublishResponse is not None

    def test_import_batches_schemas(self):
        from app.schemas.batches import BulkSearchRequest, BulkPublishRequest
        assert BulkSearchRequest is not None
        assert BulkPublishRequest is not None

    def test_import_webhooks_schemas(self):
        from app.schemas.webhooks import WebhookRequest, QuotePayload
        assert WebhookRequest is not None
        assert QuotePayload is not None

    def test_import_backward_compat(self):
        from app.schemas.extraction import BoeingSearchResponse
        from app.schemas.publishing import ShopifyPublishRequest
        from app.schemas.webhooks import ZapWebhookRequest
        assert BoeingSearchResponse is not None
        assert ShopifyPublishRequest is not None
        assert ZapWebhookRequest is not None


# =========================================================================
# 12. Target folder structure verification
# =========================================================================

class TestTargetFolderStructure:
    """Verify the final folder structure matches the target."""

    def test_routes_directory_contents(self):
        """routes/ has exactly the expected files (no extras)."""
        routes_dir = os.path.join(APP, "routes")
        files = {f for f in os.listdir(routes_dir)
                 if f.endswith(".py") and f != "__pycache__"}
        expected = {
            "__init__.py",
            "extraction.py",
            "publishing.py",
            "batches.py",
            "products.py",
            "sync.py",
            "search.py",
            "webhooks.py",
            "auth.py",
            "health.py",
        }
        assert files == expected, \
            f"Unexpected files in routes/: {files - expected}" if files > expected \
            else f"Missing files in routes/: {expected - files}"

    def test_services_directory_has_pipeline_files(self):
        """services/ has all pipeline service files."""
        services_dir = os.path.join(APP, "services")
        files = {f for f in os.listdir(services_dir) if f.endswith(".py")}
        expected_services = {
            "extraction_service.py",
            "publishing_service.py",
            "webhook_service.py",
            "auth_service.py",
            "search_service.py",
            "sync_service.py",
        }
        assert expected_services.issubset(files), \
            f"Missing services: {expected_services - files}"

    def test_no_old_service_files(self):
        """services/ does NOT contain old service files."""
        services_dir = os.path.join(APP, "services")
        files = {f for f in os.listdir(services_dir) if f.endswith(".py")}
        old_files = {"boeing_service.py", "shopify_service.py",
                     "zap_service.py", "cognito_admin.py"}
        found_old = files & old_files
        assert not found_old, f"Old service files still exist: {found_old}"

    def test_schemas_directory_has_pipeline_files(self):
        """schemas/ has all pipeline schema files."""
        schemas_dir = os.path.join(APP, "schemas")
        files = {f for f in os.listdir(schemas_dir) if f.endswith(".py")}
        expected_schemas = {
            "extraction.py",
            "publishing.py",
            "batches.py",
            "webhooks.py",
            "products.py",
            "search.py",
            "sync.py",
            "auth.py",
        }
        assert expected_schemas.issubset(files), \
            f"Missing schemas: {expected_schemas - files}"

    def test_no_old_schema_files(self):
        """schemas/ does NOT contain old schema files."""
        schemas_dir = os.path.join(APP, "schemas")
        files = {f for f in os.listdir(schemas_dir) if f.endswith(".py")}
        old_files = {"boeing.py", "shopify.py", "bulk.py", "zap.py"}
        found_old = files & old_files
        assert not found_old, f"Old schema files still exist: {found_old}"
