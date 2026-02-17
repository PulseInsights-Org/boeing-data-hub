# Boeing Data Hub — Backend Restructuring Plan V1

This plan incorporates validated feedback from senior technical review against the actual codebase. Every recommendation was verified against source code before inclusion.

---

## Two Core Principles

### 1. Pipeline-Consistent Naming

Every file is named by its **business pipeline**, not by the external system it talks to.

When a developer sees an error in `celery_app.tasks.extraction`, they check `services/extraction_service.py` and `routes/extraction.py`. All three share the root name: **extraction**.

Backed by production FastAPI conventions:
- zhanymkanov/fastapi-best-practices (Netflix Dispatch-inspired)
- Viktor Sapozhok's FastAPI 3-Tier Design: *"Each new service requires matching files across all four packages with identical naming"*
- Camillo Visini's FastAPI Service Abstraction: routers/foo.py, services/foo.py, schemas/foo.py share root name

Clients are the exception — named by external system (boeing_client.py, shopify_client.py) because they are HTTP adapters, not pipelines.

### 2. Unidirectional Dependency Flow

Every layer has one job. Dependencies flow in one direction only.

```
Routes / Tasks (thin wrappers)
  |
  v
Services (ALL business logic)
  |               |
  v               v
Clients           DB Stores
(HTTP calls)      (database CRUD)
  |               |
  v               v
External APIs     Supabase / PostgreSQL
```

**The rule**: Routes call services. Services call clients (for HTTP) and stores (for DB). Never skip a layer. Routes and tasks NEVER import clients or stores directly.

---

## What's Wrong Today

### The Name Mismatch Problem

```
routes/shopify.py  ->  tasks/publishing.py  ->  services/shopify_service.py
     "shopify"           "publishing"              "shopify" again
```

A developer sees a publishing task error and looks for `services/publishing_service.py` — it doesn't exist. The logic is in `services/shopify_service.py`.

### After Restructuring

```
routes/extraction.py  ->  tasks/extraction.py      ->  services/extraction_service.py
routes/publishing.py  ->  tasks/publishing.py       ->  services/publishing_service.py
routes/sync.py        ->  tasks/sync_dispatch.py    ->  services/sync_dispatch_service.py
                          tasks/sync_boeing.py      ->  services/boeing_fetch_service.py
                          tasks/sync_shopify.py     ->  services/shopify_update_service.py
```

### The Inverted Dependency Problem (shopify_client.py)

`supabase_client.py` is the gold standard: 50 lines, creates SDK client only. DB stores use it for queries, services orchestrate business logic.

`shopify_client.py` is 1112 lines: creates HTTP client AND contains payload transformation, metafield building, inventory mapping, pricing calculations, GraphQL mutations. Routes and tasks call the client directly. The service layer is bypassed.

After restructuring, every client follows `supabase_client.py`:
- **supabase_client.py** (~50 lines) — SDK client creation only
- **boeing_client.py** (~150 lines) — OAuth + raw API calls, returns raw JSON
- **shopify_client.py** (~280 lines) — REST + GraphQL raw HTTP calls only

All transformation moves to services: `services/utils/shopify_payload_builder.py` + `publishing_service.py`.

---

## The Debugging Navigation Model

| Error in... | Route | Task | Service | Client |
|---|---|---|---|---|
| Product extraction | `extraction.py` | `extraction.py` | `extraction_service.py` | `boeing_client.py` |
| Data normalization | -- | `normalization.py` | `normalization_service.py` | -- |
| Shopify publishing | `publishing.py` | `publishing.py` | `publishing_service.py` | `shopify_client.py` |
| Sync dispatch | `sync.py` | `sync_dispatch.py` | `sync_dispatch_service.py` | -- |
| Sync Boeing fetch | -- | `sync_boeing.py` | `boeing_fetch_service.py` | `boeing_client.py` |
| Sync Shopify update | -- | `sync_shopify.py` | `shopify_update_service.py` | `shopify_client.py` |
| Batch tracking | `batches.py` | `batch.py` | `batch_service.py` | -- |
| Multi-part search | `search.py` | -- | `search_service.py` | `shopify_client.py` |
| Authentication | `auth.py` | -- | `auth_service.py` | -- |
| Product data view | `products.py` | -- | `products_service.py` | -- |

**Navigation rule**: File name = pipeline. Folder = layer.

---

## Target Folder Structure

```
backend/
  app/
    main.py                         -- App factory, lifespan, mount routers with /api/v1 prefix
    container.py                    -- Lazy DI container for FastAPI + Celery

    core/
      config.py                     -- Pydantic settings from env vars
      exceptions.py                 -- All custom exceptions
      auth.py                       -- FastAPI auth dependencies (JWT validation)
      cognito.py                    -- Cognito JWKS verification
      middleware.py                 -- NEW: CORS, request logging
      constants/                    -- NEW: business constants split by domain
        __init__.py                 -- Re-exports all constants
        extraction.py               -- Boeing API defaults (batch size, system user)
        publishing.py               -- Shopify mappings (UOM, cert, metafield defs, tags, category GID)
        sync.py                     -- Sync scheduler defaults (currency, stuck threshold)
        pricing.py                  -- Markup factors, fallback image URL, default condition/cert

    clients/                        -- Named by EXTERNAL SYSTEM (HTTP adapters only)
      boeing_client.py              -- Boeing OAuth + API calls
      shopify_client.py             -- Shopify REST + GraphQL raw HTTP calls only
      supabase_client.py            -- Supabase connection factory

    db/                             -- Named by DATABASE TABLE (one file per table)
      base_store.py                 -- NEW: Supabase client access + error translation only
      raw_data_store.py             -- boeing_raw_data table
      staging_store.py              -- product_staging table
      product_store.py              -- product table
      image_store.py                -- Supabase storage (image download + upload)
      batch_store.py                -- batches table (existing, minimal changes)
      sync_store.py                 -- product_sync_schedule CRUD
      sync_analytics.py             -- product_sync_schedule dashboard queries
      user_store.py                 -- users table (existing, minimal changes)

    services/                       -- Named by PIPELINE (matches routes + tasks)
      extraction_service.py         -- Boeing search + raw data storage
      normalization_service.py      -- Boeing response normalization + staging storage
      publishing_service.py         -- Shopify publish, reconciliation, idempotency, image upload
      sync_dispatch_service.py      -- Hourly dispatch, retry dispatch, end-of-day cleanup
      boeing_fetch_service.py       -- Boeing batch fetch + hash-based change detection
      shopify_update_service.py     -- Shopify product update after change detected
      batch_service.py              -- Batch creation, progress calculation
      search_service.py             -- Multi-part Shopify GraphQL search
      auth_service.py               -- Cognito admin operations
      products_service.py           -- Product data access (staging, published, raw)
      utils/                        -- Service utilities (transformation, mapping)
        __init__.py
        shopify_payload_builder.py  -- All Shopify payload transformation (from shopify_client.py)

    schemas/                        -- Named by PIPELINE (matches routes)
      extraction.py                 -- Boeing search request/response models
      publishing.py                 -- Shopify publish request/response models
      sync.py                       -- Sync dashboard + management models
      products.py                   -- Product data models
      batches.py                    -- Batch status + progress models
      search.py                     -- Multi-part search models
      auth.py                       -- Auth request/response models

    routes/                         -- Named by PIPELINE (flat folder, v1 prefix at mount time)
      __init__.py                   -- Aggregates routers, applies /api/v1 prefix
      health.py                     -- Health check + system status + sync status
      extraction.py                 -- Boeing search + bulk extraction
      publishing.py                 -- Shopify publish + bulk publish + update + check
      sync.py                       -- Sync dashboard + management + trigger (thin, delegates to services)
      batches.py                    -- Batch CRUD
      products.py                   -- Product viewing (staging, published, raw-data)
      search.py                     -- Multi-part Shopify search
      auth.py                       -- Authentication

    utils/                          -- Named by FUNCTION
      boeing_normalize.py           -- Boeing response normalization
      rate_limiter.py               -- Redis token-bucket rate limiter
      sync_helpers.py               -- Slot distribution, batch grouping, time buckets
      hash_utils.py                 -- Hash computation, change detection

    celery_app/                     -- Named by PIPELINE (matches services)
      celery_config.py              -- Queue definitions, rate limits, beat schedule
      tasks/
        base.py                     -- Base task class with Container integration
        extraction.py               -- Thin wrapper -> calls extraction_service
        normalization.py            -- Thin wrapper -> calls normalization_service
        publishing.py               -- Thin wrapper -> calls publishing_service
        batch.py                    -- Thin wrapper -> calls batch_service
        sync_dispatch.py            -- Thin wrapper -> calls sync_dispatch_service (dispatch, retry, cleanup)
        sync_boeing.py              -- Thin wrapper -> calls boeing_fetch_service (process_boeing_batch)
        sync_shopify.py             -- Thin wrapper -> calls shopify_update_service (update_shopify_product)

  tests/
  scripts/
  requirements.txt
  requirements-dev.txt
```

---

## What Goes Where — The Layer Rules

| Layer | Who calls it | What it does | What it does NOT do |
|-------|-------------|--------------|---------------------|
| **Client** | Services only | Create SDK, make raw HTTP/GraphQL calls | Transform data, build payloads, apply business rules |
| **DB Store** | Services only | Run queries, return rows. One file per table | Business logic, pricing, orchestration |
| **Service** | Routes and Tasks | ALL business logic: transform, validate, orchestrate | Handle HTTP responses, define API contracts |
| **Route** | FastAPI framework | Receive request, call service, return response | Any logic beyond request/response handling |
| **Task** | Celery framework | Receive queue message, call service, handle retry | Any logic beyond task orchestration |
| **Schema** | Routes | Pydantic models for request/response validation | Live inline in routes (must be separate files) |
| **Constants** | Anywhere | Every hardcoded business value, defined once | Live scattered across services/clients/tasks |

---

## Route Versioning Strategy

Routes live in a **flat folder** (`routes/`). No `v1/` subfolder.

The `/api/v1` prefix is applied at mount time in `routes/__init__.py`:

```
extraction_router  ->  mounted at  /api/v1/extraction
publishing_router  ->  mounted at  /api/v1/publishing
sync_router        ->  mounted at  /api/v1/sync
batches_router     ->  mounted at  /api/v1/batches
products_router    ->  mounted at  /api/v1/products
search_router      ->  mounted at  /api/v1/search
auth_router        ->  mounted at  /api/v1/auth
health_router      ->  mounted at  /  (no version prefix)
```

Route files use short relative paths (`/search`, `/bulk`, `/publish`). Full URL becomes `/api/v1/extraction/search`.

If v2 is needed later: create `extraction_v2.py` and mount at `/api/v2/extraction`. No folder restructuring.

---

## API Backward Compatibility Strategy

**Problem validated**: The frontend has 18 hardcoded API paths across 7 service files that will break on route restructuring.

### Current Frontend API Paths (from actual source)

| Frontend File | Current Path | New Path |
|---|---|---|
| `boeingService.ts` | `/api/boeing/product-search` | `/api/v1/extraction/search` |
| `shopifyService.ts` | `/api/shopify/publish` | `/api/v1/publishing/publish` |
| `shopifyService.ts` | `/api/shopify/products/{id}` | `/api/v1/publishing/products/{id}` |
| `shopifyService.ts` | `/api/shopify/check` | `/api/v1/publishing/check` |
| `bulkService.ts` | `/api/bulk-search` | `/api/v1/extraction/bulk` |
| `bulkService.ts` | `/api/bulk-publish` | `/api/v1/publishing/bulk` |
| `bulkService.ts` | `/api/batches` | `/api/v1/batches` |
| `bulkService.ts` | `/api/batches/{id}` | `/api/v1/batches/{id}` |
| `bulkService.ts` | `/api/products/staging` | `/api/v1/products/staging` |
| `bulkService.ts` | `/api/products/raw-data/{pn}` | `/api/v1/products/raw-data/{pn}` |
| `syncService.ts` | `/api/sync/dashboard` | `/api/v1/sync/dashboard` |
| `syncService.ts` | `/api/sync/products` | `/api/v1/sync/products` |
| `syncService.ts` | `/api/sync/history` | `/api/v1/sync/history` |
| `syncService.ts` | `/api/sync/failures` | `/api/v1/sync/failures` |
| `syncService.ts` | `/api/sync/hourly-stats` | `/api/v1/sync/hourly-stats` |
| `syncService.ts` | `/api/sync/product/{sku}` | `/api/v1/sync/products/{sku}` |
| `syncService.ts` | `/api/sync/product/{sku}/reactivate` | `/api/v1/sync/products/{sku}/reactivate` |
| `syncService.ts` | `/api/sync/trigger/{sku}` | `/api/v1/sync/trigger/{sku}` |

### Migration Strategy: Two-Phase Rollout

**Phase A** — Deploy backend with BOTH old and new routes active:
- New route handlers at `/api/v1/*` (canonical)
- Legacy route file (`routes/legacy.py`) that re-mounts old endpoints calling the same services
- No frontend changes needed yet

**Phase B** — Update frontend to use new paths, then remove legacy routes:
- Update all frontend service files to use `/api/v1/*` paths
- Deploy frontend
- After confirming old paths have zero traffic, remove `routes/legacy.py`

### Endpoint naming fix (from review)

Use plural consistently: `/api/v1/sync/products/{sku}` not `/api/v1/sync/product/{sku}`.

Move `/sync/status` endpoint under `/health` route (since it's a system status endpoint, not a sync data endpoint).

---

## Celery Task Name Stability Strategy

**Problem validated**: All 11 tasks have explicit `name=` parameters, but the names include module paths (`celery_app.tasks.sync_dispatcher.dispatch_hourly_sync`). Additionally:
- `include=` in celery_config.py references old module paths
- `task_routes` dict references old task name strings
- `task_annotations` dict references old task name strings
- `beat_schedule` references old task name strings
- No `send_task()` usage found (all invocations use `.delay()`)

### Deployment context (from actual CI/CD)

Three separate systemd services on EC2: `boeing-backend`, `boeing-celery`, `boeing-celery-beat`. Restarted together via `systemctl restart` in deployment workflow. Brief window where old worker processes may still be running.

### Migration Strategy: Stable Names First

**Step 1** (before ANY file moves): Change all `name=` parameters to STABLE names that don't include module paths:

| Current name= | Stable name= |
|---|---|
| `celery_app.tasks.extraction.process_bulk_search` | `tasks.extraction.process_bulk_search` |
| `celery_app.tasks.extraction.extract_chunk` | `tasks.extraction.extract_chunk` |
| `celery_app.tasks.normalization.normalize_chunk` | `tasks.normalization.normalize_chunk` |
| `celery_app.tasks.publishing.publish_batch` | `tasks.publishing.publish_batch` |
| `celery_app.tasks.publishing.publish_product` | `tasks.publishing.publish_product` |
| `celery_app.tasks.sync_dispatcher.dispatch_hourly_sync` | `tasks.sync_dispatch.dispatch_hourly` |
| `celery_app.tasks.sync_dispatcher.dispatch_retry_sync` | `tasks.sync_dispatch.dispatch_retry` |
| `celery_app.tasks.sync_dispatcher.end_of_day_cleanup` | `tasks.sync_dispatch.end_of_day_cleanup` |
| `celery_app.tasks.sync_dispatcher.sync_boeing_batch` | `tasks.sync_boeing.process_boeing_batch` |
| `celery_app.tasks.sync_dispatcher.sync_shopify_product` | `tasks.sync_shopify.update_shopify_product` |
| `celery_app.tasks.sync_dispatcher.sync_single_product_immediate` | `tasks.sync_shopify.sync_single_product_immediate` |
| `celery_app.tasks.batch.check_batch_completion` | `tasks.batch.check_batch_completion` |

**Step 2**: Update `task_routes`, `task_annotations`, and `beat_schedule` to use the new stable names.

**Step 3**: Deploy and verify. Now task names are decoupled from file paths.

**Step 4**: Safely move files, rename modules. Task names don't change. Beat schedule continues uninterrupted.

### Task Retry Standardization (from review)

Tasks are the retry/idempotency boundary. Standardize across all tasks:

| Task Type | autoretry_for | max_retries | rate_limit |
|---|---|---|---|
| Orchestrators | None (max_retries=0) | 0 | -- |
| Boeing API tasks | RetryableError, ConnectionError, TimeoutError | 2-3 | 2/m |
| Shopify API tasks | RetryableError, ConnectionError, TimeoutError, httpx errors | 3 | 30/m |
| DB-only tasks | RetryableError | 3 | -- |

Business logic stays in services. Tasks own the retry/backoff policy.

---

## Scale Handling: 5000 Products Pipeline

### Current Bottlenecks (validated from source)

| Stage | Rate Limit | Time for 5000 products |
|---|---|---|
| Extraction (Boeing API) | 2 req/min, 10 SKUs per batch | 500 batches / 2 per min = ~250 min (~4.2 hours) |
| Normalization | No rate limit (CPU bound) | Minutes |
| Publishing (Shopify API) | 30 req/min, 1 product per task | 5000 / 30 per min = ~167 min (~2.8 hours) |
| **Total pipeline** | | **~7 hours** |

### Architecture for Scale

**Queue backpressure**: With 5000 products, the publishing queue gets 5000 tasks enqueued at once. Redis memory is proportional to task payload size. Current payloads are small (batch_id + part_number + user_id), so 5000 tasks ~ 2-5 MB in Redis. This is fine.

**Progress tracking**: Current batch_store uses database triggers to increment counters (`extracted_count`, `normalized_count`, `published_count`). This is efficient for 5000 items — triggers fire per-row, no polling needed. Frontend uses Supabase realtime subscription for live updates.

**Partial failure handling**: With 5000 products, some WILL fail (invalid prices, missing inventory, Shopify errors). Current system already handles this:
- `NonRetryableError` skips product and records failure
- `RetryableError` retries with exponential backoff
- `check_batch_completion` runs after each product to detect batch completion
- batch_store tracks per-product failures

**Chunk size optimization**: Extraction uses `BOEING_BATCH_SIZE=10` (env configurable). For 5000 products this creates 500 extraction tasks. This is fine — each task is independent and rate-limited.

### What Needs to Change for 5000-Product Scale

**1. Batch progress query efficiency**

The current `check_batch_completion` task runs after EVERY product publish (5000 times per batch). It queries the database to check if all items are done. At scale, this should use cached counters rather than full table scans.

Recommended: Add a Redis-based counter for in-flight progress. Only query the database for final completion verification.

**2. Publishing task deduplication**

If the same batch is accidentally triggered twice, 5000 duplicate tasks get queued. Current system has `idempotency_key` on batches but not on individual publish tasks.

Recommended: Check `product_staging.status = "published"` at the start of `publish_product` task and skip if already published (the current code already does an idempotent Shopify upsert, so this is optimization, not correctness).

**3. Failure accumulation visibility**

With 5000 products, a 5% failure rate means 250 failures. The current batch status endpoint returns all failures inline.

Recommended: Add a `/api/v1/batches/{id}/failures` endpoint with pagination for large batches.

**4. Worker concurrency for publishing**

Current config: `--concurrency=2`. For 5000 products at 30/min rate limit, increasing to `--concurrency=4` could help overlap network latency, but Shopify rate limiting is the actual bottleneck, not concurrency.

Recommendation: Keep `concurrency=2` for publishing workers. The rate limit is the binding constraint.

---

## Constants Split by Domain

**Problem validated**: A single `core/constants.py` file with 17 categories of constants will grow to 300+ lines and become a merge-conflict hotspot.

### Structure: `core/constants/` package

```
core/constants/
  __init__.py         -- Re-exports all constants for convenience
  pricing.py          -- PRICE_MARKUP_FACTOR, DEFAULT_CERT, DEFAULT_CONDITION, FALLBACK_IMAGE_URL
  publishing.py       -- PRODUCT_CATEGORY_GID, PRODUCT_TAGS, UOM_MAPPING, CERT_MAPPING,
                         TRACE_ALLOWED_DOMAINS, METAFIELD_DEFINITIONS
  extraction.py       -- DEFAULT_SUPPLIER, SYSTEM_USER_ID
  sync.py             -- DEFAULT_CURRENCY, MIN_PRODUCTS_FOR_ACTIVE_SLOT
  auth.py             -- JWKS_CACHE_TTL
  rate_limiting.py    -- DEFAULT_CAPACITY, DEFAULT_REFILL_RATE, REDIS_KEY_PREFIX
```

Each file stays under 80 lines. Different pipelines own different constant files. No merge conflicts.

Usage: `from app.core.constants.pricing import PRICE_MARKUP_FACTOR` or `from app.core.constants import PRICE_MARKUP_FACTOR` (via `__init__.py` re-export).

---

## Database Layer: Table-Specific Files

### base_store.py — Kept Minimal (from review)

**Risk validated**: Generic `_insert/_upsert/_select/_update` helpers can become leaky abstractions with Supabase because query composition, filters, returning clauses, and pagination are not uniform across tables.

**Decision**: `base_store.py` provides ONLY:
- Supabase client access (via `SupabaseClient`)
- Common error translation (APIError -> HTTPException with logging)

Each table store implements its own explicit queries. More readable, fewer surprises.

### Split Map

| Database Table | New File | Methods |
|---|---|---|
| (base) | `base_store.py` (~40 lines) | Client access, error translation |
| `boeing_raw_data` | `raw_data_store.py` (~30 lines) | `insert_boeing_raw_data()` |
| `product_staging` | `staging_store.py` (~200 lines) | `upsert_product_staging()`, `get_by_part_number()`, `update_shopify_id()`, `update_image()` |
| `product` | `product_store.py` (~150 lines) | `upsert_product()`, `get_by_part_number()`, `get_by_sku()`, `update_pricing()` |
| (Supabase storage) | `image_store.py` (~200 lines) | `upload_image_from_url()` |
| `batches` | `batch_store.py` (existing) | Keep as-is |
| `users` | `user_store.py` (existing) | Keep as-is |
| `product_sync_schedule` | `sync_store.py` (~380 lines) | Keep CRUD operations |
| `product_sync_schedule` | `sync_analytics.py` (~200 lines) | Dashboard/stats queries |

---

## Saga Compensation: Reconciliation Over Deletion

**Problem validated**: Current `publish_product` task (publishing.py:257-290) deletes the Shopify product when DB save fails after Shopify CREATE. This is risky:
- Deletion might fail (leaves orphan logged as CRITICAL but no automated repair)
- If failure is transient, deletion destroys a product that could have been saved on retry
- Images already uploaded to Supabase storage are now orphaned

### Recommended Approach

Replace destructive compensation with a reconciliation pattern:

1. **On DB failure after Shopify CREATE**: Save a `publish_state = "shopify_created_pending_db"` record to a lightweight `reconciliation_log` (or add a status column to `product_staging`)
2. **Retry DB write** 2-3 times with backoff before giving up
3. **Reconciliation task** (runs periodically or on-demand): Finds products in `shopify_created_pending_db` state and attempts to complete the DB save
4. **Only delete** if product is confirmed unreferenceable after reconciliation window (e.g., 24 hours with no successful retry)

This ensures no accidental product deletion and provides a clear path for manual repair.

---

## Files That Get Split

| File Today | Lines | Becomes |
|---|---|---|
| `clients/shopify_client.py` | 1112 | `clients/shopify_client.py` (~280, HTTP only) + `services/utils/shopify_payload_builder.py` (~350, transformation) |
| `db/supabase_store.py` | 794 | `db/base_store.py` (~40) + `db/raw_data_store.py` (~30) + `db/staging_store.py` (~200) + `db/product_store.py` (~150) + `db/image_store.py` (~200) |
| `db/sync_store.py` | 766 | `db/sync_store.py` (~380) + `db/sync_analytics.py` (~200) |
| `celery_app/tasks/sync_dispatcher.py` | 660 | `services/sync_dispatch_service.py` (~150) + `services/boeing_fetch_service.py` (~150) + `services/shopify_update_service.py` (~120) + thin `tasks/sync_dispatch.py` (~60) + `tasks/sync_boeing.py` (~50) + `tasks/sync_shopify.py` (~60) |
| `routes/sync.py` | 564 | `schemas/sync.py` (~130) + `routes/sync.py` (~250) |
| `utils/sync_helpers.py` | 562 | `utils/sync_helpers.py` (~300) + `utils/hash_utils.py` (~120) |
| `routes/bulk.py` | 543 | Split across `routes/extraction.py`, `routes/publishing.py`, `routes/batches.py`, `routes/products.py` |
| `routes/multi_part_search.py` | 487 | `schemas/search.py` (~80) + `services/search_service.py` (~200) + `routes/search.py` (~80) |
| `celery_app/tasks/publishing.py` | 436 | `services/publishing_service.py` (~280) + thin `tasks/publishing.py` (~80) |
| `main.py` | 300 | `routes/health.py` (~80) + slimmed `main.py` (~200) |

Note: If `shopify_payload_builder.py` exceeds 400 lines during implementation, split `services/utils/` into separate files: `metafield_builder.py`, `pricing_mapper.py`, `inventory_mapper.py`. Plan the interface now, split when needed.

---

## API Route Structure

All endpoints under `/api/v1/` with pipeline-based prefixes. Plural nouns consistently.

### Extraction — `/api/v1/extraction`
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/search?query={sku}` | Search single product from Boeing |
| POST | `/bulk` | Start bulk extraction job |

### Publishing — `/api/v1/publishing`
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/publish` | Publish single product to Shopify |
| POST | `/bulk` | Start bulk publishing job |
| PUT | `/products/{id}` | Update published Shopify product |
| GET | `/check?sku={sku}` | Check if SKU exists in Shopify |
| POST | `/metafields/setup` | Create metafield definitions |

### Sync — `/api/v1/sync`
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/dashboard` | Sync overview and stats |
| GET | `/products` | List sync-scheduled products |
| GET | `/products/{sku}` | Single product sync status |
| POST | `/products/{sku}/reactivate` | Reactivate failed product |
| GET | `/history` | Recent sync history |
| GET | `/failures` | Failed sync products |
| GET | `/hourly-stats` | Per-hour statistics |
| POST | `/trigger/{sku}` | Trigger immediate sync |

### Batches — `/api/v1/batches`
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | List all batches |
| GET | `/{id}` | Get batch status and progress |
| GET | `/{id}/failures` | Paginated failure list (for large batches) |
| DELETE | `/{id}` | Cancel running batch |

### Products — `/api/v1/products`
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/staging` | List staging products |
| GET | `/published` | List published products |
| GET | `/published/{id}` | Get single published product |
| GET | `/raw-data/{part_number}` | Get raw Boeing API response |

### Search — `/api/v1/search`
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/multi-part` | Search multiple SKUs in Shopify |

### Auth — `/api/v1/auth`
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/me` | Get current user profile |
| POST | `/logout` | Logout and revoke token |

### System (no version prefix)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/health/sync-status` | Sync scheduler + rate limiter status |

---

## Data Flow After Restructuring

### Single Product Search
```
Route (extraction.py) -> ExtractionService -> BoeingClient (HTTP) + RawDataStore + StagingStore
```

### Bulk Search Pipeline (5000 products)
```
Route (extraction.py) -> BatchService (create batch)
  -> Task (extraction.py) -> ExtractionService -> BoeingClient (HTTP, 10 SKUs/call, 2/min)
     -> RawDataStore (audit trail) -> 500 extraction tasks over ~250 min
  -> Task (normalization.py) -> NormalizationService -> boeing_normalize (transform) + StagingStore (save)
     -> Chains from extraction, runs as fast as data arrives
```

### Publish Pipeline (5000 products)
```
Route (publishing.py) -> BatchService (create batch)
  -> Task (publishing.py) -> PublishingService -> ShopifyPayloadBuilder (transform)
     -> ShopifyClient (HTTP, 30/min) + ImageStore (upload) + ProductStore + StagingStore
     -> Reconciliation: if DB fails -> log state, retry, only delete after 24h window
     -> 5000 individual tasks over ~167 min
```

### Auto-Sync Pipeline (3-service split)
```
Celery Beat -> Task (sync_dispatch.py) -> SyncDispatchService.dispatch_hourly()
  -> SyncStore (get products for time bucket, group into batches)
  -> Enqueue: Task (sync_boeing.py) per batch

Task (sync_boeing.py) -> BoeingFetchService.process_boeing_batch()
  -> BoeingClient (HTTP) -> hash_utils (change detection)
  -> If changed: Enqueue Task (sync_shopify.py) per changed product

Task (sync_shopify.py) -> ShopifyUpdateService.update_shopify_product()
  -> ShopifyClient (HTTP) + ProductStore (update pricing) + SyncStore (update status)
```

**Key design**: `routes/sync.py` stays thin — it only calls `sync_dispatch_service` for dashboard/management operations. The Boeing fetch and Shopify update tasks are enqueued by the dispatch service and upstream tasks, NOT by the route.

### Sync Pipeline — 3-Service Split Rationale

The sync pipeline has three distinct concerns with different dependencies, rate limits, and failure modes:

| Service | Responsibility | External Dependency | Rate Limit | Task File |
|---|---|---|---|---|
| `sync_dispatch_service.py` | Time bucket selection, batch grouping, retry scheduling, end-of-day cleanup | SyncStore (DB only) | None | `tasks/sync_dispatch.py` |
| `boeing_fetch_service.py` | Boeing API fetch, hash-based change detection, identifying stale products | BoeingClient + hash_utils | 2/min (Boeing API) | `tasks/sync_boeing.py` |
| `shopify_update_service.py` | Shopify product update (price, inventory, status) | ShopifyClient + ProductStore + SyncStore | 30/min (Shopify API) | `tasks/sync_shopify.py` |

**Why not one `sync_service.py`?** A single 350-line service would mix three rate-limited external APIs with DB-only dispatch logic. When a Boeing API error occurs, the developer has to read past Shopify update code to find the issue. Split services let you debug each stage independently and scale each queue separately.

**`routes/sync.py` stays thin**: The route file handles dashboard queries and management actions (reactivate, trigger). It delegates to `sync_dispatch_service` only. It does NOT import Boeing or Shopify task modules. The task chain is: dispatch enqueues Boeing tasks, Boeing tasks enqueue Shopify tasks. The route never touches the middle of the chain.

---

## File Migration Map

### Routes
| Current | New | Reason |
|---------|-----|--------|
| `routes/boeing.py` | `routes/extraction.py` | Pipeline naming |
| `routes/shopify.py` | `routes/publishing.py` | Pipeline naming |
| `routes/bulk.py` | Split into `extraction.py`, `publishing.py`, `batches.py`, `products.py` | Mixed resources |
| `routes/sync.py` | `routes/sync.py` | Models to schemas |
| `routes/products.py` | `routes/products.py` | Merged with staging + raw-data |
| `routes/zap.py` | DELETED | Zapier/webhooks no longer used |
| `routes/multi_part_search.py` | `routes/search.py` | Simpler name |
| `routes/auth.py` | `routes/auth.py` | Same |
| -- | `routes/legacy.py` (TEMPORARY) | Old paths for backward compat |

### Services
| Current | New | Reason |
|---------|-----|--------|
| `services/boeing_service.py` | `services/extraction_service.py` | Pipeline naming |
| `services/shopify_service.py` | Merged into `services/publishing_service.py` | Pipeline naming |
| `services/zap_service.py` | DELETED | Zapier/webhooks no longer used |
| `services/cognito_admin.py` | `services/auth_service.py` | Pipeline naming |
| -- | `services/normalization_service.py` (NEW) | Normalization logic from tasks/normalization.py + utils/boeing_normalize.py |
| -- | `services/utils/shopify_payload_builder.py` (NEW) | Transformation logic from shopify_client.py |
| -- | `services/sync_dispatch_service.py` (NEW) | Dispatch/retry/cleanup from tasks/sync_dispatcher.py |
| -- | `services/boeing_fetch_service.py` (NEW) | Boeing batch fetch from tasks/sync_dispatcher.py |
| -- | `services/shopify_update_service.py` (NEW) | Shopify update from tasks/sync_dispatcher.py |
| -- | `services/batch_service.py` (NEW) | From routes/bulk.py |
| -- | `services/search_service.py` (NEW) | From routes/multi_part_search.py |
| -- | `services/products_service.py` (NEW) | Maintains layer invariant |

### Schemas
| Current | New |
|---------|-----|
| `schemas/boeing.py` | `schemas/extraction.py` |
| `schemas/shopify.py` | `schemas/publishing.py` |
| `schemas/bulk.py` | `schemas/batches.py` |
| `schemas/zap.py` | DELETED (Zapier/webhooks removed) |
| `schemas/products.py` | `schemas/products.py` (same) |
| `schemas/auth.py` | `schemas/auth.py` (same) |
| -- | `schemas/sync.py` (NEW, from routes/sync.py) |
| -- | `schemas/search.py` (NEW, from routes/multi_part_search.py) |

### Tasks
| Current | New |
|---------|-----|
| `tasks/extraction.py` | `tasks/extraction.py` (calls extraction_service) |
| `tasks/normalization.py` | `tasks/normalization.py` (calls normalization_service) |
| `tasks/publishing.py` | `tasks/publishing.py` (slimmed, calls publishing_service) |
| `tasks/sync_dispatcher.py` | Split into `tasks/sync_dispatch.py` + `tasks/sync_boeing.py` + `tasks/sync_shopify.py` |
| `tasks/batch.py` | `tasks/batch.py` (calls batch_service) |

### DB
| Current | New |
|---------|-----|
| `db/supabase_store.py` | Split into 5 files (by table, quotes removed) |
| `db/sync_store.py` | Split: CRUD + analytics |
| `db/batch_store.py` | Same |
| `db/user_store.py` | Same |

---

## Implementation Phases (Revised Ordering)

### Phase 0 — Stabilize Celery Task Names (DEPLOY FIRST)
- Change all `name=` parameters to stable names (without module path prefix)
- Update `task_routes`, `task_annotations`, `beat_schedule` to match new names
- Deploy and verify: workers register tasks, beat schedules fire, all `.delay()` calls work
- This decouples task identity from file location BEFORE any files move

### Phase 1 — Foundation (no breaking changes)
- Create `core/constants/` package with domain-specific constant files
- Create `container.py` with lazy DI container (NO imports of route/service modules at import time)
- Create `core/middleware.py` with CORS configuration
- Create `schemas/sync.py` with models extracted from routes/sync.py
- Create `schemas/search.py` with models extracted from routes/multi_part_search.py

### Phase 2 — Move celery_app inside app/
- Move `celery_app/` directory into `app/celery_app/`
- Update `include=` paths in celery_config.py (add `sync_dispatch`, `sync_boeing`, `sync_shopify`, remove `sync_dispatcher`)
- Update Celery subprocess commands in main.py (`-A app.celery_app`)
- Update systemd service files on EC2
- Split `tasks/sync_dispatcher.py` into `tasks/sync_dispatch.py`, `tasks/sync_boeing.py`, `tasks/sync_shopify.py`
- Task names don't change (stabilized in Phase 0)

### Phase 3 — Split shopify_client.py
- Create `services/utils/shopify_payload_builder.py` with all transformation logic
- Strip `clients/shopify_client.py` down to HTTP calls only
- Move mapping tables to `core/constants/publishing.py`

### Phase 4 — Split database files
- Create `db/base_store.py` (minimal: client access + error translation)
- Split `supabase_store.py` into `raw_data_store.py`, `staging_store.py`, `product_store.py`, `image_store.py`
- Delete `quotes` table related code (Zapier/webhooks removed)
- Split `sync_store.py` into `sync_store.py` (core) + `sync_analytics.py`
- Update all imports across codebase

### Phase 5 — Extract services from tasks (pipeline naming)
- Create `services/extraction_service.py` (from boeing_service.py + extraction task logic)
- Create `services/normalization_service.py` (from normalization task logic + boeing_normalize.py orchestration)
- Create `services/publishing_service.py` (from shopify_service.py + task logic)
- Create `services/sync_dispatch_service.py` (hourly dispatch, retry, end-of-day cleanup from sync_dispatcher.py)
- Create `services/boeing_fetch_service.py` (Boeing batch fetch + change detection from sync_dispatcher.py)
- Create `services/shopify_update_service.py` (Shopify product update from sync_dispatcher.py)
- Create `services/batch_service.py` (from routes/bulk.py inline logic)
- Create `services/search_service.py` (from routes/multi_part_search.py logic)
- Create `services/products_service.py` (thin, maintains layer invariant)
- Delete `services/zap_service.py` (Zapier/webhooks removed)
- Rename `services/cognito_admin.py` to `services/auth_service.py`
- Slim all Celery tasks to thin wrappers
- Replace saga deletion with reconciliation pattern
- CRITICAL: Services must NEVER import task modules (prevents import cycles). If a service needs to enqueue work, inject a task dispatcher interface.

### Phase 6 — Restructure routes (pipeline naming + backward compat)
- Create `routes/__init__.py` with router aggregation and `/api/v1` prefix
- Create all new route files (extraction, publishing, batches, products, sync, search, auth, health)
- Delete `routes/zap.py` and `schemas/zap.py` (Zapier/webhooks removed)
- Create `routes/legacy.py` — mounts old paths calling same services (backward compat)
- Rename schemas: boeing.py -> extraction.py, shopify.py -> publishing.py, etc.
- Slim main.py
- Deploy with both old and new routes active

### Phase 7 — Frontend migration + constants integration
- Update all frontend service files to use `/api/v1/*` paths
- Replace every hardcoded constant with imports from `core/constants/`
- Split `sync_helpers.py` -> keep slot/batch logic + extract hash functions to `hash_utils.py`
- Deploy frontend with new paths
- Do constants replacement pipeline-by-pipeline (publishing first, then extraction, then sync) to avoid high-churn PRs

### Phase 8 — Cleanup and verification
- Verify old API paths have zero traffic
- Remove `routes/legacy.py`
- Delete old files replaced by new structure
- Remove singleton factory functions
- Update all test imports
- Run full test suite
- Production smoke test: worker registers tasks, beat schedules show next-due times, one extraction + one publish in staging environment
- Verify every file under 400 lines
- Verify no hardcoded constants outside `core/constants/`

---

## Verification Checklist

### Functional
- Every API endpoint responds correctly on `/api/v1/` paths
- Legacy API paths respond correctly (during Phase A)
- Celery workers register all tasks with stable names
- Celery Beat fires scheduled tasks at configured intervals
- Bulk search -> extraction -> normalization pipeline works end-to-end
- Bulk publish -> Shopify creation with reconciliation works
- Sync dispatch -> Boeing fetch (sync_boeing task) -> change detection -> Shopify update (sync_shopify task) works
- 5000-product batch completes with acceptable failure rate (<5%)
- Batch progress tracking updates in real-time

### Structural
- No file in `backend/app/` exceeds 400 lines
- No business constant appears outside `core/constants/`
- Every pipeline has matching names across routes, tasks, services, schemas
- No route or task file imports clients or stores directly
- No service file imports task modules
- All test imports updated and passing
- Health check returns 200

### Deployment
- `boeing-backend` systemd service starts
- `boeing-celery` systemd service registers all tasks
- `boeing-celery-beat` shows next-due times for all scheduled tasks
- Rate limiter initializes with correct token count
- EC2 deployment workflow completes without errors

---

## Answers to Technical Review Questions

**Q: What are the current production route prefixes?**
A: Mixed. Auth uses `prefix="/api/auth"`. Sync uses `prefix="/api/sync"`. Bulk uses `prefix="/api"`. Boeing, Shopify, Zap, Products, and Search have no prefix — endpoints use full paths inline (e.g., `/api/boeing/product-search`). All 18 frontend paths are documented in the backward compatibility section above.

**Q: How is Celery configured today?**
A: Uses explicit `include=` list in celery_config.py (not autodiscover). All 11 tasks have explicit `name=` parameters. Beat schedule references task names as strings. No `send_task()` usage found — all invocations use `.delay()`.

**Q: Do you have direct Postgres connections?**
A: No. All DB operations go through the Supabase Python SDK. No psycopg, asyncpg, or SQLAlchemy usage found. Pure code reorg, no DDL migration step needed.

**Q: How do you run workers in deployment?**
A: Three separate systemd services on a single EC2 instance: `boeing-backend` (uvicorn), `boeing-celery` (worker), `boeing-celery-beat` (scheduler). Deployed together via GitHub Actions rsync + `systemctl restart`. Same venv, same codebase. Brief inconsistency window during restart.

---

## Reference Sources

- zhanymkanov/fastapi-best-practices — Domain-based module structure
- Camillo Visini: FastAPI Service Abstraction — Same root name across layers
- Viktor Sapozhok: FastAPI 3-Tier Design — Matching filenames across packages
- TestDriven.io: FastAPI + Celery — Thin tasks calling services
- FastAPI Official: Bigger Applications — Multi-file router organization
