"""Generate Project_Walkthrough.docx."""

from doc_helpers import (
    create_doc, add_table, add_diagram_placeholder, add_code_block,
    add_file_entry, add_bullet, save_doc,
)


def generate():
    doc = create_doc("Project Walkthrough")

    # ── 1. Project Overview ──────────────────────────────────────────
    doc.add_heading("1. Project Overview", level=1)
    doc.add_paragraph(
        "Boeing Data Hub is an end-to-end product data pipeline that extracts aviation part "
        "information from Boeing's Part Number Availability (PNA) APIs, normalizes and stages "
        "the data, publishes products to a Shopify retail storefront, and continuously "
        "synchronizes prices and inventory on an automated schedule."
    )
    doc.add_paragraph("The system has four main components:")
    add_bullet(doc, "React Frontend — A single-page dashboard for search, publish, and sync monitoring.")
    add_bullet(doc, "FastAPI Backend — REST API handling authentication, business logic, and orchestration.")
    add_bullet(doc, "Celery + Redis — Distributed task queue for asynchronous extraction, publishing, and sync operations.")
    add_bullet(doc, "Supabase PostgreSQL — Managed database for product data, batch tracking, and sync scheduling.")

    # ── 2. System Architecture ───────────────────────────────────────
    doc.add_heading("2. System Architecture", level=1)
    doc.add_paragraph(
        "The system follows a layered architecture: Routes -> Services -> Stores -> Clients. "
        "Long-running operations are offloaded to Celery workers via Redis queues."
    )
    add_diagram_placeholder(doc, "System Architecture")
    doc.add_paragraph(
        "Data flows as follows: The React frontend sends authenticated requests to the "
        "FastAPI backend. Route handlers delegate to service classes, which orchestrate "
        "business logic. Services interact with database stores (Supabase) and external "
        "API clients (Boeing, Shopify). Bulk operations are enqueued as Celery tasks "
        "and processed asynchronously by dedicated workers."
    )

    # ── 3. Technologies Used ─────────────────────────────────────────
    doc.add_heading("3. Technologies Used", level=1)
    add_table(doc,
        ["Layer", "Technology", "Purpose"],
        [
            ["Frontend Framework", "React 18.3 + TypeScript 5.8", "Single-page application UI"],
            ["Frontend Build", "Vite 5.4 (SWC)", "Fast development server and production bundler"],
            ["Frontend Styling", "Tailwind CSS 3.4 + Radix UI", "Utility-first CSS and accessible component primitives"],
            ["Frontend State", "React Query (TanStack) 5.x", "Server state caching and synchronization"],
            ["Frontend Realtime", "Supabase JS 2.x", "PostgreSQL change subscriptions for live updates"],
            ["Backend Framework", "FastAPI 0.115", "Async Python REST API framework"],
            ["Backend Server", "Uvicorn 0.30", "ASGI production server"],
            ["Task Queue", "Celery 5.3 + Redis 5.0", "Distributed async task processing with scheduling"],
            ["Database", "Supabase (PostgreSQL)", "Managed relational database and file storage"],
            ["Authentication", "AWS Cognito + Aviation Gateway", "Federated SSO with JWT token verification"],
            ["Boeing API Client", "httpx (async)", "OAuth2-authenticated part data extraction"],
            ["Shopify API Client", "httpx (async)", "REST + GraphQL product management"],
            ["AI Reports", "Google Gemini 2.0 Flash", "AI-generated sync cycle summary reports"],
            ["Email", "Resend", "Transactional email delivery for reports"],
            ["Reverse Proxy", "Nginx + Let's Encrypt", "HTTPS termination and request routing"],
            ["Infrastructure", "AWS EC2 (Ubuntu 22.04)", "Compute hosting for all backend services"],
            ["CI/CD", "GitHub Actions", "Automated deployment pipeline"],
        ],
    )

    # ── 4. Folder Structure ──────────────────────────────────────────
    doc.add_heading("4. Folder Structure & Responsibilities", level=1)

    doc.add_heading("4.1 Root Directory", level=2)
    add_file_entry(doc, ".github/workflows/deploy-backend.yml", "CI/CD pipeline for backend deployment to EC2")
    add_file_entry(doc, "database/", "SQL migration scripts for Supabase PostgreSQL schema")
    add_file_entry(doc, "docs/", "Generated documentation files")

    doc.add_heading("4.2 Backend — backend/", level=2)
    add_file_entry(doc, "backend/app/main.py", "FastAPI entry point; lifespan manager; auto-starts Celery workers")
    add_file_entry(doc, "backend/app/container.py", "Dependency injection container with lazy singletons (lru_cache)")
    add_file_entry(doc, "backend/requirements.txt", "Production Python dependencies")
    add_file_entry(doc, "backend/requirements-dev.txt", "Development/testing dependencies (pytest, ruff, black, mypy)")

    doc.add_heading("Routes — backend/app/routes/", level=3)
    add_file_entry(doc, "routes/__init__.py", "Aggregates all routers under /api/v1 prefix")
    add_file_entry(doc, "routes/auth.py", "/auth/me and /auth/logout endpoints")
    add_file_entry(doc, "routes/extraction.py", "/extraction/search and /extraction/bulk-search")
    add_file_entry(doc, "routes/batches.py", "/batches CRUD with pagination and cancellation")
    add_file_entry(doc, "routes/publishing.py", "/publishing/publish, /bulk-publish, product updates")
    add_file_entry(doc, "routes/products.py", "/products/published, /staging, /raw-data queries")
    add_file_entry(doc, "routes/search.py", "/search/multi-part Shopify SKU lookup")
    add_file_entry(doc, "routes/sync.py", "/sync dashboard, history, failures, hourly stats, reactivation")
    add_file_entry(doc, "routes/health.py", "/health liveness check")
    add_file_entry(doc, "routes/legacy.py", "Backward-compatible redirects from /api/* to /api/v1/*")

    doc.add_heading("Services — backend/app/services/", level=3)
    add_file_entry(doc, "services/extraction_service.py", "Boeing search -> normalize -> stage pipeline")
    add_file_entry(doc, "services/normalization_service.py", "Transforms raw Boeing JSON to staging format")
    add_file_entry(doc, "services/publishing_service.py", "Saga-pattern product publishing with rollback")
    add_file_entry(doc, "services/shopify_orchestrator.py", "High-level Shopify CRUD (create, update, search)")
    add_file_entry(doc, "services/shopify_inventory_service.py", "Inventory levels and cost management")
    add_file_entry(doc, "services/shopify_update_service.py", "Price and inventory sync updates")
    add_file_entry(doc, "services/sync_dispatch_service.py", "Hourly sync scheduler and dispatch logic")
    add_file_entry(doc, "services/search_service.py", "Multi-SKU search against Shopify")
    add_file_entry(doc, "services/report_service.py", "AI report generation and email distribution")
    add_file_entry(doc, "services/auth_service.py", "Cognito global sign-out")
    add_file_entry(doc, "services/batch_service.py", "Batch progress calculation")
    add_file_entry(doc, "services/products_service.py", "Product listing and detail queries")

    doc.add_heading("Clients — backend/app/clients/", level=3)
    add_file_entry(doc, "clients/boeing_client.py", "Boeing OAuth2 authentication and PNA API calls")
    add_file_entry(doc, "clients/shopify_client.py", "Shopify REST and GraphQL HTTP client")
    add_file_entry(doc, "clients/supabase_client.py", "Supabase database operations and file storage")
    add_file_entry(doc, "clients/gemini_client.py", "Google Generative AI client for report generation")
    add_file_entry(doc, "clients/resend_client.py", "Resend email delivery client")

    doc.add_heading("Database Stores — backend/app/db/", level=3)
    add_file_entry(doc, "db/base_store.py", "Abstract base class with Supabase client reference")
    add_file_entry(doc, "db/raw_data_store.py", "Raw Boeing API response storage (boeing_raw_data)")
    add_file_entry(doc, "db/staging_store.py", "Normalized product staging CRUD (product_staging)")
    add_file_entry(doc, "db/product_store.py", "Published product records (product)")
    add_file_entry(doc, "db/batch_store.py", "Batch tracking with progress counters (batches)")
    add_file_entry(doc, "db/image_store.py", "Image download and Supabase Storage upload")
    add_file_entry(doc, "db/sync_store.py", "Sync schedule CRUD and bucket management")
    add_file_entry(doc, "db/sync_analytics.py", "Slot distribution summaries for dashboard")
    add_file_entry(doc, "db/report_store.py", "Report history storage (sync_reports)")

    doc.add_heading("Celery Tasks — backend/app/celery_app/tasks/", level=3)
    add_file_entry(doc, "tasks/extraction.py", "process_bulk_search, extract_chunk — Boeing data fetching")
    add_file_entry(doc, "tasks/normalization.py", "normalize_chunk — data normalization pipeline")
    add_file_entry(doc, "tasks/publishing.py", "publish_batch, publish_product — Shopify publishing saga")
    add_file_entry(doc, "tasks/batch.py", "check_batch_completion, cancel_batch, cleanup_stale_batches")
    add_file_entry(doc, "tasks/sync_dispatch.py", "dispatch_hourly, dispatch_retry, end_of_day_cleanup")
    add_file_entry(doc, "tasks/sync_boeing.py", "process_boeing_batch — Boeing sync data fetching")
    add_file_entry(doc, "tasks/sync_shopify.py", "update_shopify_product — Shopify price/inventory updates")
    add_file_entry(doc, "tasks/report_generation.py", "AI report generation and email delivery tasks")

    doc.add_heading("Core — backend/app/core/", level=3)
    add_file_entry(doc, "core/config.py", "Pydantic Settings class with 40+ environment variables")
    add_file_entry(doc, "core/auth.py", "get_current_user dependency — Cognito JWT verification")
    add_file_entry(doc, "core/cognito.py", "JWKS fetching, token signature and claims validation")
    add_file_entry(doc, "core/exceptions.py", "Custom exception hierarchy (Retryable/NonRetryable)")
    add_file_entry(doc, "core/middleware.py", "CORS middleware configuration")

    doc.add_heading("Utilities — backend/app/utils/", level=3)
    add_file_entry(doc, "utils/boeing_normalize.py", "Field normalization, dimension parsing, price calculation")
    add_file_entry(doc, "utils/shopify_payload_builder.py", "Shopify product payload construction")
    add_file_entry(doc, "utils/rate_limiter.py", "Redis token bucket rate limiter (Boeing 2/min, Shopify 30/min)")
    add_file_entry(doc, "utils/hash_utils.py", "Deterministic hashing for change detection")
    add_file_entry(doc, "utils/slot_manager.py", "Hour bucket allocation for sync scheduling")
    add_file_entry(doc, "utils/dispatch_lock.py", "Redis-backed distributed lock for dispatch tasks")
    add_file_entry(doc, "utils/change_detection.py", "Hash-based change tracking for sync updates")
    add_file_entry(doc, "utils/cycle_tracker.py", "Sync cycle tracking utilities")
    add_file_entry(doc, "utils/schedule_helpers.py", "Sync scheduling helper functions")

    doc.add_heading("4.3 Frontend — frontend/", level=2)
    add_file_entry(doc, "frontend/src/App.tsx", "Root component: routing, providers, auth context")
    add_file_entry(doc, "frontend/src/main.tsx", "Application entry point (React root render)")
    add_file_entry(doc, "frontend/src/pages/Index.tsx", "Main dashboard with three-tab interface")
    add_file_entry(doc, "frontend/src/contexts/AuthContext.tsx", "SSO authentication context (Aviation Gateway)")
    add_file_entry(doc, "frontend/src/components/dashboard/", "Dashboard panels: SearchPanel, PublishedProductsPanel, AutoSyncPanel, etc.")
    add_file_entry(doc, "frontend/src/hooks/", "Custom hooks: useProducts, useBulkOperations, useSyncDashboard, etc.")
    add_file_entry(doc, "frontend/src/services/", "API service modules: authService, bulkService, syncService, etc.")
    add_file_entry(doc, "frontend/src/types/product.ts", "TypeScript interfaces for all data models")
    add_file_entry(doc, "frontend/src/components/ui/", "Radix UI primitives wrapped with Tailwind styling")

    doc.add_heading("4.4 Tests — backend/tests/", level=2)
    add_file_entry(doc, "tests/conftest.py", "Pytest fixtures: mock clients, auth bypass, test settings")
    add_file_entry(doc, "tests/unit/", "26 unit test files — individual component isolation tests")
    add_file_entry(doc, "tests/integration/", "10 integration test files — route and task tests")
    add_file_entry(doc, "tests/e2e/", "3 end-to-end tests — full pipeline tests (extraction, publishing, sync)")

    # ── 5. How the System Works ──────────────────────────────────────
    doc.add_heading("5. How the System Works", level=1)

    doc.add_heading("5.1 Extraction Pipeline", level=2)
    doc.add_paragraph(
        "1. User enters part numbers in the React dashboard (SearchPanel.tsx)."
    )
    doc.add_paragraph(
        "2. Frontend POSTs to /api/v1/extraction/bulk-search (routes/extraction.py)."
    )
    doc.add_paragraph(
        "3. Route handler creates a batch record (batch_store.py) and enqueues "
        "process_bulk_search Celery task (tasks/extraction.py)."
    )
    doc.add_paragraph(
        "4. Task splits part numbers into chunks of 10 and enqueues extract_chunk for each."
    )
    doc.add_paragraph(
        "5. Each extract_chunk calls Boeing API (boeing_client.py) with rate limiting (rate_limiter.py, 2 req/min)."
    )
    doc.add_paragraph(
        "6. Raw responses are stored in boeing_raw_data table (raw_data_store.py)."
    )
    doc.add_paragraph(
        "7. normalize_chunk task normalizes the data (boeing_normalize.py) and upserts into product_staging (staging_store.py)."
    )
    doc.add_paragraph(
        "8. Batch progress is tracked in real-time via Supabase subscriptions to the frontend."
    )

    doc.add_heading("5.2 Publishing Pipeline", level=2)
    doc.add_paragraph(
        "1. User selects products and clicks Publish (ProductTable.tsx)."
    )
    doc.add_paragraph(
        "2. Frontend POSTs to /api/v1/publishing/publish or /bulk-publish (routes/publishing.py)."
    )
    doc.add_paragraph(
        "3. publish_product task (tasks/publishing.py) uses the saga pattern:"
    )
    doc.add_paragraph(
        "   a. Reads staged product from product_staging table."
    )
    doc.add_paragraph(
        "   b. Calls Shopify orchestrator (shopify_orchestrator.py) to create the product."
    )
    doc.add_paragraph(
        "   c. Sets inventory levels and costs (shopify_inventory_service.py)."
    )
    doc.add_paragraph(
        "   d. Records the published product in the product table (product_store.py)."
    )
    doc.add_paragraph(
        "   e. Creates a sync schedule entry (sync_store.py) for automatic future updates."
    )
    doc.add_paragraph(
        "   f. On failure, compensating transactions roll back partial work."
    )

    doc.add_heading("5.3 Auto-Sync Pipeline", level=2)
    doc.add_paragraph(
        "1. Celery Beat triggers dispatch_hourly task every hour at :45 (tasks/sync_dispatch.py)."
    )
    doc.add_paragraph(
        "2. Dispatcher reads the current hour bucket (0-23) from sync_dispatch_service.py."
    )
    doc.add_paragraph(
        "3. Products assigned to that bucket are fetched from product_sync_schedule."
    )
    doc.add_paragraph(
        "4. Products are grouped into batches of 10 and process_boeing_batch tasks are enqueued."
    )
    doc.add_paragraph(
        "5. Each batch fetches latest data from Boeing (boeing_client.py)."
    )
    doc.add_paragraph(
        "6. Hash-based change detection (hash_utils.py) identifies price/inventory changes."
    )
    doc.add_paragraph(
        "7. Changed products get update_shopify_product tasks to update Shopify."
    )
    doc.add_paragraph(
        "8. Sync results are recorded in sync_history and product_sync_schedule is updated."
    )
    doc.add_paragraph(
        "9. Failed syncs are retried every 4 hours; deactivated after 5 consecutive failures."
    )

    # ── 6. Database Design ───────────────────────────────────────────
    doc.add_heading("6. Database Design", level=1)
    doc.add_paragraph("All tables are hosted in Supabase (PostgreSQL).")

    add_table(doc,
        ["Table", "Key Columns", "Description"],
        [
            ["users", "id, username, email, created_at", "User accounts linked to Cognito identities"],
            ["boeing_raw_data", "id, user_id, search_query, raw_payload (JSONB)", "Archived raw Boeing API responses for debugging and recovery"],
            ["product_staging", "id, user_id, batch_id, sku, title, price, inventory_quantity, status", "Normalized Boeing data staged for review before publishing. Unique on (user_id, sku)"],
            ["product", "id, user_id, sku, shopify_product_id, shopify_variant_id, title, price", "Published products with Shopify references. Unique on (user_id, sku)"],
            ["batches", "id, batch_type, status, total_items, extracted/normalized/published/failed counts, failed_items (JSONB)", "Tracks bulk operations (extract or publish) with real-time progress"],
            ["product_sync_schedule", "id, user_id, sku, hour_bucket, sync_status, consecutive_failures, is_active", "Sync scheduler: assigns each product to an hourly bucket for periodic updates"],
            ["sync_reports", "id, cycle_id, report_text, summary_stats (JSONB), email_sent", "AI-generated sync cycle reports with email delivery tracking"],
        ],
    )

    # ── 7. Configuration & Environment Variables ─────────────────────
    doc.add_heading("7. Configuration & Environment Variables", level=1)
    doc.add_paragraph(
        "The backend reads configuration from a .env file via Pydantic Settings "
        "(backend/app/core/config.py). Below is a sample grouped by category."
    )

    add_code_block(doc, """# ── Authentication (AWS Cognito) ──
COGNITO_REGION=us-east-1
COGNITO_USER_POOL_ID=<placeholder>
COGNITO_APP_CLIENT_ID=<placeholder>

# ── Database (Supabase) ──
SUPABASE_URL=<placeholder>
SUPABASE_SERVICE_ROLE_KEY=<placeholder>
SUPABASE_STORAGE_BUCKET=product-images

# ── Shopify ──
SHOPIFY_STORE_DOMAIN=<placeholder>.myshopify.com
SHOPIFY_ADMIN_API_TOKEN=<placeholder>
SHOPIFY_API_VERSION=2025-01
SHOPIFY_LOCATION_MAP={}
SHOPIFY_INVENTORY_LOCATION_CODES={}

# ── Boeing API ──
BOEING_CLIENT_ID=<placeholder>
BOEING_CLIENT_SECRET=<placeholder>
BOEING_USERNAME=<placeholder>
BOEING_PASSWORD=<placeholder>
BOEING_OAUTH_TOKEN_URL=https://api.developer.boeingservices.com/oauth2/v2.0/token
BOEING_PNA_OAUTH_URL=https://api.developer.boeingservices.com/boeing-part-price-availability/token/v1/oauth
BOEING_PNA_PRICE_URL=https://api.developer.boeingservices.com/boeing-part-price-availability/price-availability/v1/wtoken

# ── Redis & Task Queue ──
REDIS_URL=redis://localhost:6379/0
AUTO_START_CELERY=false

# ── Rate Limiting ──
BOEING_RATE_LIMIT_CAPACITY=2
SHOPIFY_API_RATE_LIMIT=30

# ── Sync Scheduler ──
SYNC_MODE=testing
SYNC_ENABLED=true
SYNC_FREQUENCY=weekly
SYNC_DISPATCH_MINUTE=*/10
SYNC_RETRY_HOURS=4
SYNC_MAX_FAILURES=5

# ── AI Reports ──
GEMINI_API_KEY=<placeholder>
RESEND_API_KEY=<placeholder>
RESEND_FROM_ADDRESS=reports@skynetparts.com
REPORT_RECIPIENTS=["dev@skynetparts.com"]""")

    # ── 8. Security & Access Controls ────────────────────────────────
    doc.add_heading("8. Security & Access Controls", level=1)

    doc.add_heading("8.1 Authentication", level=2)
    doc.add_paragraph(
        "Users authenticate via Aviation Gateway, a federated SSO system backed by AWS Cognito. "
        "The frontend receives a JWT access token which is sent as a Bearer token on every API "
        "request. The backend validates the token against Cognito's JWKS endpoint, verifying "
        "signature, expiry, issuer, and token_use claims."
    )

    doc.add_heading("8.2 Authorization", level=2)
    doc.add_paragraph(
        "Route-level authorization uses FastAPI dependencies. The get_current_user dependency "
        "validates the JWT and extracts user info. The require_groups dependency checks Cognito "
        "group memberships for role-based access (e.g., admin-only routes)."
    )

    doc.add_heading("8.3 Rate Limiting", level=2)
    doc.add_paragraph(
        "A Redis-backed token bucket rate limiter (utils/rate_limiter.py) prevents API quota "
        "exhaustion. Boeing API: 2 requests/minute. Shopify API: 30 requests/minute. Celery "
        "task annotations enforce queue-level limits."
    )

    doc.add_heading("8.4 Secrets Management", level=2)
    doc.add_paragraph(
        "All secrets are stored in .env files (never committed to git) for local development, "
        "and in GitHub Actions secrets for CI/CD. On the EC2 server, the .env file is managed "
        "manually and excluded from rsync deployments."
    )

    # ── 9. Deployment Process ────────────────────────────────────────
    doc.add_heading("9. Deployment Process", level=1)
    doc.add_paragraph(
        "Deployment is automated via GitHub Actions. On push to the main branch (with changes "
        "in the backend/ directory), the pipeline:"
    )
    add_bullet(doc, "Checks out the code and sets up SSH access to the EC2 instance.")
    add_bullet(doc, "Rsyncs the backend directory to /home/ubuntu/boeing-data-hub/backend/, excluding .env, venv, tests, and cache files.")
    add_bullet(doc, "SSHs into the EC2 instance, activates the Python virtual environment, and installs dependencies.")
    add_bullet(doc, "Runs the redeploy.sh script which stops all 6 systemd services, reloads the daemon, and restarts them in sequence.")
    add_bullet(doc, "Verifies all services are running: boeing-backend, boeing-celery-extract, boeing-celery-publish, boeing-celery-sync, boeing-celery-default, boeing-celery-beat.")

    doc.add_heading("EC2 Infrastructure", level=2)
    add_table(doc,
        ["Component", "Details"],
        [
            ["Server", "AWS EC2 (Ubuntu 22.04)"],
            ["Domain", "api.boeing-data-hub.skynetparts.com"],
            ["Reverse Proxy", "Nginx with Let's Encrypt SSL"],
            ["Process Manager", "systemd (6 services)"],
            ["Message Broker", "Redis (localhost:6379)"],
        ],
    )

    # ── 10. Logging & Monitoring ─────────────────────────────────────
    doc.add_heading("10. Logging & Monitoring", level=1)

    doc.add_heading("10.1 Application Logs", level=2)
    doc.add_paragraph(
        "Python's standard logging module is configured at INFO level. Each module uses "
        "logging.getLogger(__name__). In production, logs are captured by systemd and "
        "accessible via journalctl:"
    )
    add_code_block(doc, """sudo journalctl -u boeing-backend -f
sudo journalctl -u boeing-celery-extract -f
sudo journalctl -u boeing-celery-sync -f""")

    doc.add_heading("10.2 Health Check", level=2)
    doc.add_paragraph(
        'GET /health returns {"status": "healthy"}. This endpoint is unauthenticated and '
        "used by monitoring tools and the deployment pipeline."
    )

    doc.add_heading("10.3 Sync Dashboard", level=2)
    doc.add_paragraph(
        "The /api/v1/sync/dashboard endpoint provides real-time sync monitoring: total "
        "products, active count, success rate, slot distribution, and failure counts. "
        "The frontend Auto-Sync tab visualizes this data."
    )

    # ── 11. Future Enhancements ──────────────────────────────────────
    doc.add_heading("11. Future Enhancements", level=1)
    add_bullet(doc, "[TODO: Verify with the team] Dockerization of backend services for containerized deployment.")
    add_bullet(doc, "[TODO: Verify with the team] Frontend deployment pipeline (currently manual build and deploy).")
    add_bullet(doc, "[TODO: Verify with the team] Enhanced monitoring with APM tools (Datadog, New Relic, or Sentry).")
    add_bullet(doc, "[TODO: Verify with the team] User-facing settings page for sync frequency and notification preferences.")
    add_bullet(doc, "[TODO: Verify with the team] Multi-tenant support for managing multiple Shopify stores.")

    # ── 12. Developer Notes ──────────────────────────────────────────
    doc.add_heading("12. Developer Notes — Quick Start", level=1)
    doc.add_paragraph("Follow these steps to get the project running from scratch:")

    doc.add_heading("Clone the Repository", level=3)
    add_code_block(doc, "git clone https://github.com/PulseInsights-Org/boeing-data-hub.git\ncd boeing-data-hub")

    doc.add_heading("Backend Setup", level=3)
    add_code_block(doc, """cd backend
python -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\\Scripts\\activate        # Windows
pip install -r requirements.txt
pip install -r requirements-dev.txt""")

    doc.add_heading("Configure Environment", level=3)
    add_code_block(doc, """cp .env.example .env
# Edit .env with your actual values for:
# COGNITO_*, SUPABASE_*, SHOPIFY_*, BOEING_*, REDIS_URL""")

    doc.add_heading("Start Redis", level=3)
    add_code_block(doc, """# Docker
docker run -d -p 6379:6379 redis:7

# Or install directly
sudo apt install redis-server && sudo systemctl start redis""")

    doc.add_heading("Run the Backend", level=3)
    add_code_block(doc, """# Option A: Auto-start (FastAPI + Celery together)
AUTO_START_CELERY=true uvicorn app.main:app --reload

# Option B: Manual workers (recommended for development)
# Terminal 1 - API Server:
uvicorn app.main:app --reload --port 8000

# Terminal 2 - Extraction worker:
celery -A app.celery_app worker --pool=solo -Q extraction,normalization -l info

# Terminal 3 - Publishing worker:
celery -A app.celery_app worker --pool=solo -Q publishing -l info

# Terminal 4 - Sync workers:
celery -A app.celery_app worker --pool=solo -Q sync_boeing,sync_shopify -l info

# Terminal 5 - Default worker:
celery -A app.celery_app worker --pool=solo -Q default -l info

# Terminal 6 - Beat scheduler:
celery -A app.celery_app beat -l info""")

    doc.add_heading("Frontend Setup", level=3)
    add_code_block(doc, """cd frontend
npm install
npm run dev       # Starts Vite dev server on http://localhost:8080""")

    doc.add_heading("Run Tests", level=3)
    add_code_block(doc, """cd backend
pytest tests/ -v              # All tests
pytest tests/unit/ -v         # Unit tests only
pytest tests/integration/ -v  # Integration tests only
pytest tests/e2e/ -v          # End-to-end tests""")

    doc.add_heading("Verify It Works", level=3)
    add_code_block(doc, """# Backend health check
curl http://localhost:8000/health
# Expected: {"status": "healthy"}

# Open frontend
open http://localhost:8080""")

    # ── 13. Summary ──────────────────────────────────────────────────
    doc.add_heading("13. Summary", level=1)
    doc.add_paragraph(
        "Boeing Data Hub is a production-grade data pipeline connecting Boeing's PNA APIs to "
        "a Shopify retail store. It uses FastAPI for the API layer, Celery + Redis for async "
        "task processing, Supabase PostgreSQL for data persistence, and a React + TypeScript "
        "frontend for the user dashboard. The system is deployed on AWS EC2 via GitHub Actions "
        "and includes comprehensive rate limiting, error handling, and automated sync scheduling."
    )

    return save_doc(doc, "Project_Walkthrough.docx")
