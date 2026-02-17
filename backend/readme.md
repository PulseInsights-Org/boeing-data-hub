# Boeing Data Hub — Backend

FastAPI + Celery backend for the Boeing Data Hub pipeline. Handles product extraction from Boeing APIs, normalization, publishing to Shopify, and automated sync scheduling.

## Prerequisites

- Python 3.11+
- Redis running locally (required for Celery broker)
- Environment variables configured (see [Environment Variables](#environment-variables))
- `pip install -r requirements.txt`

## Running the Server

All commands must be run from the `backend/` directory.

### Option A: Separate Terminals (recommended for debugging)

Open **6 terminals** in the `backend/` folder:

**Terminal 1 — FastAPI Server**
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

**Terminal 2 — Extraction & Normalization Worker**
```bash
celery -A app.celery_app worker --pool=solo -Q extraction,normalization -l info -n extract@%h
```

**Terminal 3 — Publishing Worker**
```bash
celery -A app.celery_app worker --pool=solo -Q publishing -l info -n publish@%h
```

**Terminal 4 — Sync Worker (Boeing + Shopify)**
```bash
celery -A app.celery_app worker --pool=solo -Q sync_boeing,sync_shopify --concurrency=1 -l info -n sync@%h
```

**Terminal 5 — Default Worker (dispatchers, batch tasks)**
```bash
celery -A app.celery_app worker --pool=solo -Q default -l info -n default@%h
```

**Terminal 6 — Celery Beat (scheduler)**
```bash
celery -A app.celery_app beat -l info
```

### Option B: All Workers in One Terminal

```bash
celery -A app.celery_app worker --pool=solo -Q extraction,normalization,publishing,default,sync_boeing,sync_shopify -l info
```

> The `-n` flag gives each worker a unique name for log identification.
> On Windows, `--pool=solo` is required (prefork doesn't work).

## Project Structure

```
backend/
├── app/
│   ├── main.py                    # FastAPI entry point
│   ├── container.py               # Dependency injection (lazy singletons)
│   │
│   ├── clients/                   # External API clients (HTTP only)
│   │   ├── boeing_client.py       #   Boeing OAuth + product/pricing API
│   │   ├── shopify_client.py      #   Shopify REST + GraphQL transport
│   │   └── supabase_client.py     #   Supabase DB + storage client
│   │
│   ├── core/                      # App configuration & cross-cutting concerns
│   │   ├── auth.py                #   FastAPI auth dependency (Cognito JWT)
│   │   ├── cognito.py             #   AWS Cognito token verification
│   │   ├── config.py              #   Settings (env vars via pydantic)
│   │   ├── exceptions.py          #   RetryableError, NonRetryableError
│   │   ├── middleware.py          #   CORS middleware
│   │   └── constants/             #   Business constants
│   │       ├── extraction.py      #     Batch size limits
│   │       ├── pricing.py         #     Markup factor, fallback image
│   │       ├── publishing.py      #     Shopify field mappings
│   │       └── sync.py            #     Sync scheduler thresholds
│   │
│   ├── db/                        # Data access layer (Supabase stores)
│   │   ├── base_store.py          #   Abstract base (shared client/storage)
│   │   ├── batch_store.py         #   Batch CRUD + progress tracking
│   │   ├── image_store.py         #   Image download + Supabase upload
│   │   ├── product_store.py       #   Published product records
│   │   ├── raw_data_store.py      #   Raw Boeing API responses
│   │   ├── staging_store.py       #   Normalized staging records
│   │   ├── sync_analytics.py      #   Slot distribution & status summaries
│   │   ├── sync_store.py          #   Sync schedule CRUD + bucket mgmt
│   │   └── user_store.py          #   User account data
│   │
│   ├── routes/                    # FastAPI route handlers
│   │   ├── __init__.py            #   Mounts all routers under /api/v1
│   │   ├── auth.py                #   /auth/me, /auth/logout
│   │   ├── batches.py             #   /batches CRUD + cancel
│   │   ├── extraction.py          #   /extraction/search, /extraction/bulk-search
│   │   ├── health.py              #   /health
│   │   ├── legacy.py              #   /api/* backward-compat redirects
│   │   ├── products.py            #   /products listing + staging
│   │   ├── publishing.py          #   /publishing/publish, bulk-publish
│   │   ├── search.py              #   /search/multi-part
│   │   └── sync.py                #   /sync dashboard + management
│   │
│   ├── schemas/                   # Pydantic request/response models
│   │   ├── batches.py             #   BulkSearchRequest, BatchStatusResponse
│   │   ├── extraction.py          #   SearchRequest
│   │   ├── products.py            #   NormalizedProduct
│   │   ├── publishing.py          #   PublishRequest, PublishResponse
│   │   ├── search.py              #   MultiPartSearchRequest
│   │   ├── sync.py                #   SyncDashboard, SyncProduct
│   │   └── webhooks.py            #   Webhook schemas
│   │
│   ├── services/                  # Business logic layer
│   │   ├── auth_service.py        #   Cognito global sign-out
│   │   ├── batch_service.py       #   Progress calculation
│   │   ├── boeing_fetch_service.py#   Boeing API data fetching
│   │   ├── extraction_service.py  #   Search → extract → normalize → stage
│   │   ├── normalization_service.py#  Boeing response normalization
│   │   ├── products_service.py    #   Product listing + detail
│   │   ├── publishing_service.py  #   Stage → publish → record (saga)
│   │   ├── search_service.py      #   Multi-part Shopify search
│   │   ├── shopify_update_service.py# Sync: price + inventory updates
│   │   ├── sync_dispatch_service.py#  Hourly dispatch + retry logic
│   │   └── webhook_service.py     #   Webhook handling
│   │
│   ├── utils/                     # Shared utilities
│   │   ├── boeing_normalize.py    #   Boeing field normalization rules
│   │   ├── hash_utils.py          #   Deterministic hashing for change detection
│   │   ├── rate_limiter.py        #   Redis-backed token bucket rate limiter
│   │   ├── shopify_inventory.py   #   Inventory levels, costs, locations
│   │   ├── shopify_orchestrator.py#   Product CRUD orchestration
│   │   ├── shopify_payload_builder.py# Shopify product payload construction
│   │   └── sync_helpers.py        #   Slot assignment, batch grouping
│   │
│   └── celery_app/                # Async task queue
│       ├── celery_config.py       #   Broker, queues, beat schedule, rate limits
│       └── tasks/
│           ├── base.py            #   BaseTask with DI container
│           ├── batch.py           #   check_batch_completion, cancel, cleanup
│           ├── extraction.py      #   process_bulk_search, extract_chunk
│           ├── normalization.py   #   normalize_chunk
│           ├── publishing.py      #   publish_batch, publish_product
│           ├── sync_boeing.py     #   process_boeing_batch
│           ├── sync_dispatch.py   #   dispatch_hourly, dispatch_retry, cleanup
│           └── sync_shopify.py    #   update_shopify_product, sync_immediate
│
└── tests/
    ├── conftest.py                # Shared fixtures + sys.modules mocks
    ├── unit/          (26 files)  # Fast, isolated, no external deps
    ├── integration/   (10 files)  # Route + Celery tests with mocked deps
    └── e2e/           (3 files)   # Full pipeline flow tests
```

## API Routes

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/api/v1/extraction/search?query=...` | Search Boeing for a part number |
| `POST` | `/api/v1/extraction/bulk-search` | Bulk search (returns batch_id) |
| `POST` | `/api/v1/publishing/publish` | Publish single product to Shopify |
| `POST` | `/api/v1/publishing/bulk-publish` | Bulk publish to Shopify |
| `PUT` | `/api/v1/publishing/products/{id}` | Update a Shopify product |
| `GET` | `/api/v1/publishing/check?sku=...` | Check if SKU exists in Shopify |
| `POST` | `/api/v1/publishing/metafields/setup` | Create Shopify metafield definitions |
| `GET` | `/api/v1/batches` | List batches (paginated) |
| `GET` | `/api/v1/batches/{id}` | Get batch status + progress |
| `DELETE` | `/api/v1/batches/{id}` | Cancel a batch |
| `GET` | `/api/v1/products/staging` | List staged products |
| `GET` | `/api/v1/products/published` | List published products |
| `POST` | `/api/v1/search/multi-part` | Search multiple SKUs in Shopify |
| `GET` | `/api/v1/sync/dashboard` | Sync scheduler dashboard |
| `GET` | `/api/v1/auth/me` | Current user info |
| `POST` | `/api/v1/auth/logout` | Global sign-out |

Legacy routes under `/api/*` (without `/v1`) are also supported for backward compatibility.

## Celery Queues & Tasks

| Queue | Tasks | Rate Limit |
|-------|-------|------------|
| `extraction` | `process_bulk_search`, `extract_chunk` | Boeing: 2/min |
| `normalization` | `normalize_chunk` | — |
| `publishing` | `publish_batch`, `publish_product` | Shopify: 30/min |
| `default` | `check_batch_completion`, `cancel_batch`, `cleanup_stale_batches`, `dispatch_hourly`, `dispatch_retry`, `end_of_day_cleanup` | — |
| `sync_boeing` | `process_boeing_batch` | Boeing: 2/min |
| `sync_shopify` | `update_shopify_product`, `sync_single_product_immediate` | Shopify: 30/min |

**Beat Schedule (periodic tasks):**
- `dispatch-hourly-sync` — Every hour at :45 (production) or every 10 min (testing)
- `dispatch-retry-sync` — Every 4 hours at :15
- `end-of-day-cleanup` — Daily at 00:00 UTC

## Environment Variables

Create a `.env` file in `backend/`:

```env
# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
SUPABASE_STORAGE_BUCKET=product-images

# Shopify
SHOPIFY_STORE_DOMAIN=your-store.myshopify.com
SHOPIFY_ADMIN_API_TOKEN=shpat_...
SHOPIFY_API_VERSION=2024-10
SHOPIFY_LOCATION_MAP={"Dallas Central": "Dallas Central"}
SHOPIFY_INVENTORY_LOCATION_CODES={"Dallas Central": "1D1"}

# Boeing API
BOEING_CLIENT_ID=your-client-id
BOEING_CLIENT_SECRET=your-client-secret
BOEING_OAUTH_TOKEN_URL=https://...
BOEING_PNA_OAUTH_URL=https://...
BOEING_PNA_PRICE_URL=https://...
BOEING_USERNAME=your-username
BOEING_PASSWORD=your-password

# AWS Cognito
COGNITO_USER_POOL_ID=us-east-1_...
COGNITO_APP_CLIENT_ID=your-app-client-id

# Redis
REDIS_URL=redis://localhost:6379/0

# Sync Mode ("production" or "testing")
SYNC_MODE=testing

# Optional: Auto-start Celery with FastAPI (default: true)
AUTO_START_CELERY=true
```

## Testing

```bash
# Run all tests (unit + integration + e2e)
pytest tests/unit/ tests/integration/ tests/e2e/ -v

# Run only unit tests (fast, no external deps)
pytest tests/unit/ -v

# Run by marker
pytest -m unit -v
pytest -m integration -v
pytest -m e2e -v
```

**Test count:** 571 tests (456 unit, 91 integration, 24 e2e)

## Notes

- The FastAPI server runs on `http://127.0.0.1:8000`
- Redis must be running before starting Celery workers
- On Windows, Celery requires `--pool=solo` (prefork is not supported)
- The app auto-starts Celery worker + beat on boot unless `AUTO_START_CELERY=false`
- API docs available at `http://127.0.0.1:8000/docs` (Swagger UI)
