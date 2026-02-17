# Boeing Data Hub — Backend Restructuring Plan

## The Core Principle: Pipeline-Consistent Naming

The single most important change in this restructuring is **naming consistency across layers**. Every file is named by its **business pipeline**, not by the external system it talks to.

When a junior developer sees an error in `celery_app.tasks.extraction`, they immediately know to check `services/extraction_service.py` and `routes/extraction.py`. All three share the same root name: **extraction**.

This principle is backed by production FastAPI conventions documented in:
- zhanymkanov/fastapi-best-practices (Netflix Dispatch-inspired, most-starred FastAPI guide)
- Viktor Sapozhok's FastAPI 3-Tier Design Pattern: *"Each new service requires matching files across all four packages with identical naming"*
- Camillo Visini's FastAPI Service Abstraction: routers/foo.py, services/foo.py, schemas/foo.py all share root name

Clients are the exception — they're named by external system (boeing_client.py, shopify_client.py) because they are HTTP adapters, not business pipelines.

---

## The Dependency Flow: How Layers Connect

This is the single most important architectural rule. Every layer has exactly one job, and dependencies flow in one direction only.

```
Routes (thin wrappers)
  |
  v
Services (ALL business logic lives here)
  |               |
  v               v
Clients           DB Stores
(HTTP calls)      (database CRUD)
  |               |
  v               v
External APIs     Supabase / PostgreSQL
```

**The rule is simple**: Routes call services. Services call clients (for HTTP) and stores (for DB). Never skip a layer.

### The Gold Standard: How supabase_client.py Works Today

`supabase_client.py` is 50 lines. It does exactly ONE thing: create a Supabase SDK client instance. That's it.

The DB store files import this client and use it to run database queries. Services then import DB stores and clients to orchestrate business logic. Routes call services.

```
routes/extraction.py      -- receives request, calls service, returns response
  -> services/extraction_service.py  -- orchestrates the search pipeline
    -> clients/boeing_client.py      -- makes HTTP call to Boeing API
    -> db/raw_data_store.py          -- inserts raw Boeing response into boeing_raw_data table
    -> db/staging_store.py           -- upserts normalized data into product_staging table
```

Each file has one job. Dependencies flow downward. A junior developer can trace any operation by following the arrows.

### The Anti-Pattern: What shopify_client.py Does Wrong

`shopify_client.py` is 1112 lines. It creates the Shopify HTTP client AND contains all the business logic: payload transformation, metafield building, inventory level mapping, image handling, pricing calculations, GraphQL mutation construction, and more.

This means routes and tasks call the client file directly for business operations. The service layer is either bypassed or duplicates logic. The dependency flow is inverted.

```
WRONG (today):
routes/shopify.py -> services/shopify_service.py -> clients/shopify_client.py (1112 lines, has everything)
                                                         ^
                                                    business logic lives here instead of in the service
```

```
CORRECT (after restructuring):
routes/publishing.py -> services/publishing_service.py -> clients/shopify_client.py (~280 lines, HTTP only)
                             |                                   ^
                             |                              raw HTTP calls only
                             v
                        services/publishing_mapper.py (transformation logic)
                             |
                             v
                        db/staging_store.py + db/product_store.py + db/image_store.py
```

### The Rule for Every Client File

After restructuring, every client file follows the `supabase_client.py` pattern:

- **supabase_client.py** (~50 lines) — Creates Supabase SDK client. No queries, no transformations.
- **boeing_client.py** (~150 lines) — Boeing OAuth2 + raw API calls. Returns raw JSON. No normalization.
- **shopify_client.py** (~280 lines) — Shopify REST + GraphQL raw HTTP calls. Returns raw responses. No payload building, no metafield mapping, no pricing calculations.

All transformation, mapping, and business logic moves to service files:
- `publishing_mapper.py` — Builds Shopify payloads, maps metafields, handles UOM/cert conversions
- `publishing_service.py` — Orchestrates publish: calls mapper, calls client, calls stores, handles saga

### How Each Layer Gets Used

| Layer | Who calls it | What it does | What it does NOT do |
|-------|-------------|--------------|---------------------|
| **Client** | Services only | Create SDK instance, make raw HTTP/GraphQL calls | Transform data, apply business rules, build payloads |
| **DB Store** | Services only | Run database queries, return rows | Join business logic, calculate pricing, orchestrate |
| **Service** | Routes and Tasks | ALL business logic: transform, validate, orchestrate | Handle HTTP responses, define API contracts |
| **Route** | FastAPI framework | Receive request, call service, return response | Contain ANY logic beyond request/response handling |
| **Task** | Celery framework | Receive queue message, call service, handle retry | Contain ANY logic beyond task orchestration |

---

## What's Wrong Today: The Name Mismatch Problem

A developer debugging a publishing failure has to trace through files with DIFFERENT names at every layer:

```
routes/shopify.py  ->  tasks/publishing.py  ->  services/shopify_service.py  ->  clients/shopify_client.py
     "shopify"           "publishing"              "shopify" again                 "shopify" (client)
```

There is no consistency. The route says "shopify", the task says "publishing", the service says "shopify" again. A junior developer has to memorize which name is used at which layer.

Similarly for extraction:

```
routes/boeing.py  ->  tasks/extraction.py  ->  services/boeing_service.py  ->  clients/boeing_client.py
     "boeing"          "extraction"              "boeing" again                "boeing" (client)
```

The route says "boeing", the task says "extraction". The developer sees an extraction task error and looks for `services/extraction_service.py` — but it doesn't exist. The logic is hidden inside `services/boeing_service.py`.

### After Restructuring: Names Match Across Every Layer

```
routes/extraction.py  ->  tasks/extraction.py  ->  services/extraction_service.py
       "extraction"          "extraction"              "extraction"

routes/publishing.py  ->  tasks/publishing.py  ->  services/publishing_service.py
       "publishing"          "publishing"              "publishing"

routes/sync.py  ->  tasks/sync.py  ->  services/sync_service.py
       "sync"         "sync"            "sync"
```

Developer sees an error. They know the pipeline name. They can find the file in any layer instantly.

---

## The Debugging Navigation Model

A developer gets an alert. They identify the pipeline. They go to the right file. Every time.

| "Error in..." | Route | Task | Service | Client |
|---|---|---|---|---|
| Product extraction | `extraction.py` | `extraction.py` | `extraction_service.py` | `boeing_client.py` |
| Data normalization | (part of extraction) | `normalization.py` | `extraction_service.py` | — |
| Shopify publishing | `publishing.py` | `publishing.py` | `publishing_service.py` | `shopify_client.py` |
| Auto-sync | `sync.py` | `sync.py` | `sync_service.py` | `boeing_client.py` + `shopify_client.py` |
| Batch tracking | `batches.py` | `batch.py` | `batch_service.py` | — |
| Multi-part search | `search.py` | — | `search_service.py` | `shopify_client.py` |
| Zapier webhooks | `webhooks.py` | — | `webhook_service.py` | — |
| Authentication | `auth.py` | — | `auth_service.py` | — |
| Product data view | `products.py` | — | (uses db stores directly) | — |

**Navigation rule**: The file name tells you the pipeline. The folder tells you the layer.

---

## Target Folder Structure

```
backend/
  app/
    main.py                         -- App factory, lifespan, mount routers with /api/v1 prefix
    container.py                    -- Lazy DI container for FastAPI + Celery

    core/
      config.py                     -- Pydantic settings from env vars
      constants.py                  -- NEW: every business constant, mapping table, default value
      exceptions.py                 -- All custom exceptions
      auth.py                       -- FastAPI auth dependencies (JWT validation)
      cognito.py                    -- Cognito JWKS verification
      middleware.py                 -- NEW: CORS, request logging

    clients/                        -- Named by EXTERNAL SYSTEM (HTTP adapters only)
      boeing_client.py              -- Boeing OAuth + API calls
      shopify_client.py             -- Shopify REST + GraphQL calls only
      supabase_client.py            -- Supabase connection factory

    db/                             -- Named by DATABASE TABLE (one file per table)
      base_store.py                 -- NEW: Base class with shared _insert, _upsert, _select, _update helpers
      raw_data_store.py             -- boeing_raw_data table operations
      staging_store.py              -- product_staging table operations
      product_store.py              -- product table operations
      quote_store.py                -- quotes table operations
      image_store.py                -- Image download + upload to Supabase storage
      batch_store.py                -- batches table operations (existing, minimal changes)
      sync_store.py                 -- product_sync_schedule CRUD operations
      sync_analytics.py             -- product_sync_schedule dashboard summaries + stats
      user_store.py                 -- users table operations (existing, minimal changes)

    services/                       -- Named by PIPELINE (matches routes + tasks)
      extraction_service.py         -- Boeing search + raw data storage
      publishing_service.py         -- Shopify publish, saga, idempotency, image upload
      publishing_mapper.py          -- All data transformation for Shopify payloads
      sync_service.py               -- Sync dispatch, Boeing batch fetch, Shopify update
      batch_service.py              -- Batch creation, progress calculation
      search_service.py             -- Multi-part Shopify GraphQL search
      webhook_service.py            -- Zapier quote building
      auth_service.py               -- Cognito admin operations

    schemas/                        -- Named by PIPELINE (matches routes)
      extraction.py                 -- Boeing search request/response models
      publishing.py                 -- Shopify publish request/response models
      sync.py                       -- Sync dashboard + management models
      products.py                   -- Product data models (NormalizedProduct, etc.)
      batches.py                    -- Batch status + progress models
      search.py                     -- Multi-part search models
      webhooks.py                   -- Zapier webhook models
      auth.py                       -- Auth request/response models

    routes/                         -- Named by PIPELINE (flat folder, v1 prefix applied at mount time)
      __init__.py                   -- Aggregates all sub-routers, applies /api/v1 prefix
      health.py                     -- Health check + system status
      extraction.py                 -- Boeing search + bulk extraction endpoints
      publishing.py                 -- Shopify publish + bulk publish + update + check
      sync.py                       -- Sync dashboard + management + trigger
      batches.py                    -- Batch CRUD endpoints
      products.py                   -- Product viewing (staging, published, raw-data)
      search.py                     -- Multi-part Shopify search endpoint
      webhooks.py                   -- Zapier webhook endpoint
      auth.py                       -- Authentication endpoints

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
        normalization.py            -- Thin wrapper -> calls extraction_service
        publishing.py               -- Thin wrapper -> calls publishing_service
        batch.py                    -- Thin wrapper -> calls batch_service
        sync.py                     -- Thin wrapper -> calls sync_service

  tests/
  scripts/
  requirements.txt
  requirements-dev.txt
```

---

## The Naming Convention Rules

| Folder | Named By | Example | Rationale |
|--------|----------|---------|-----------|
| `routes/` | Business pipeline | `extraction.py`, `publishing.py` | Developer traces errors by pipeline name |
| `services/` | Business pipeline + `_service` suffix | `extraction_service.py` | Matches route name, suffix avoids import conflicts |
| `schemas/` | Business pipeline | `extraction.py`, `publishing.py` | Matches route that uses them |
| `tasks/` | Business pipeline | `extraction.py`, `publishing.py` | Matches service they call |
| `clients/` | External system + `_client` suffix | `boeing_client.py`, `shopify_client.py` | They are HTTP adapters to external APIs |
| `db/` | Database table + `_store` suffix | `staging_store.py`, `product_store.py` | One file per table, easy to find |
| `utils/` | What they do | `boeing_normalize.py`, `hash_utils.py` | Pure functions, no pipeline ownership |
| `core/` | What they are | `config.py`, `constants.py`, `exceptions.py` | Framework-level concerns |

---

## What Goes Where — The Layer Rules

**Clients** create SDK instances and make raw HTTP/GraphQL calls. They return raw responses. No data transformation. No business decisions. No payload building. Every client file follows the `supabase_client.py` pattern (~50 lines). The shopify_client.py file shrinks from 1112 lines to ~280 lines by moving all transformation logic to services.

**DB stores** run database queries and return rows. One file per database table. No business logic. No orchestration. No pricing calculations. They import the Supabase client for database access.

**Services** contain ALL business logic. They are the bridge between clients (external APIs) and stores (database). Pricing, mapping, validation, orchestration, saga compensation — all in services. Services import clients for HTTP calls and stores for database operations. When something goes wrong, you debug the service.

**Routes** receive a request, call a service, return a response. Thin wrappers only. No prefix in route files — the `/api/v1` prefix is applied when routers are mounted in `routes/__init__.py`. Routes NEVER import clients or stores directly.

**Tasks** receive a queue message, call a service, handle retry. Thin wrappers only. Tasks NEVER import clients or stores directly.

**Schemas** define Pydantic models for request/response validation. They live in their own files, never inline in routes.

**Constants** define every hardcoded business value once. When a business rule changes, you change it in one file.

---

## Route Versioning Strategy

Routes live in a **flat folder** (`routes/`). There is NO `v1/` subfolder.

The version prefix `/api/v1` is applied at mount time in `routes/__init__.py`. Individual route files define endpoints with short, clean paths.

Example of how mounting works in `routes/__init__.py`:

```
extraction_router  ->  mounted at  /api/v1/extraction
publishing_router  ->  mounted at  /api/v1/publishing
sync_router        ->  mounted at  /api/v1/sync
batches_router     ->  mounted at  /api/v1/batches
products_router    ->  mounted at  /api/v1/products
search_router      ->  mounted at  /api/v1/search
webhooks_router    ->  mounted at  /api/v1/webhooks
auth_router        ->  mounted at  /api/v1/auth
health_router      ->  mounted at  /  (no version prefix)
```

Inside each route file, endpoints use short relative paths:

```
extraction.py:    /search, /bulk
publishing.py:    /publish, /bulk, /products/{id}, /check, /metafields/setup
sync.py:          /dashboard, /products, /history, /failures, etc.
```

The full URL becomes `/api/v1/extraction/search`, `/api/v1/publishing/publish`, etc.

**Why no v1 folder?** If we later need v2 for a specific route, we create `extraction_v2.py` and mount it at `/api/v2/extraction`. No folder restructuring needed.

---

## API Route Structure

All endpoints under `/api/v1/` with pipeline-based prefixes.

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
| GET | `/history` | Recent sync history |
| GET | `/failures` | Failed sync products |
| GET | `/hourly-stats` | Per-hour statistics |
| GET | `/product/{sku}` | Single product sync status |
| POST | `/product/{sku}/reactivate` | Reactivate failed product |
| POST | `/trigger/{sku}` | Trigger immediate sync |

### Batches — `/api/v1/batches`
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | List all batches |
| GET | `/{id}` | Get batch status and progress |
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

### Webhooks — `/api/v1/webhooks`
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/zap` | Zapier webhook |

### System
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check (root, no version prefix) |
| GET | `/api/v1/system/sync-status` | Sync scheduler status |

---

## Data Flow After Restructuring

### Single Product Search
```
Route (extraction.py) -> ExtractionService -> BoeingClient (HTTP) + RawDataStore + StagingStore (save)
```

### Bulk Search Pipeline
```
Route (extraction.py) -> BatchService (create batch)
  -> Task (extraction.py) -> ExtractionService -> BoeingClient (HTTP) + RawDataStore (save)
  -> Task (normalization.py) -> ExtractionService.normalize() + StagingStore (save)
```

### Publish Pipeline
```
Route (publishing.py) -> BatchService (create batch)
  -> Task (publishing.py) -> PublishingService -> PublishingMapper (transform)
    -> ShopifyClient (HTTP) + ImageStore (upload) + ProductStore + StagingStore (save)
    -> Saga: if DB fails after Shopify succeeds -> ShopifyClient.delete_product()
```

### Auto-Sync Pipeline
```
Celery Beat -> Task (sync.py) -> SyncService.dispatch_hourly()
  -> SyncStore (get products for time bucket)
  -> Task (sync.py) -> SyncService.process_boeing_batch()
    -> BoeingClient (HTTP) -> hash_utils (change detection)
    -> If changed: Task (sync.py) -> SyncService.update_shopify_product()
      -> ShopifyClient (HTTP) + ProductStore (update pricing)
```

---

## Database Layer: Table-Specific Files

The current `supabase_store.py` (794 lines) mixes operations for 4 different database tables plus image storage logic. This restructuring splits it into one file per table, plus a shared base class.

### Current supabase_store.py -> Split by Table

| Database Table | New File | Methods Moving There |
|---|---|---|
| (shared helpers) | `base_store.py` | `_insert()`, `_upsert()`, `_select()`, `_update()`, Supabase client setup |
| `boeing_raw_data` | `raw_data_store.py` | `insert_boeing_raw_data()` |
| `product_staging` | `staging_store.py` | `upsert_product_staging()`, `get_product_staging_by_part_number()`, `update_product_staging_shopify_id()`, `update_product_staging_image()` |
| `product` | `product_store.py` | `upsert_product()`, `get_product_by_part_number()`, `get_product_by_sku()`, `update_product_pricing()` |
| `quotes` | `quote_store.py` | `upsert_quote_form_data()` |
| (Supabase storage) | `image_store.py` | `upload_image_from_url()` |

### Other DB Files (Already Table-Specific)

| File | Table | Status |
|---|---|---|
| `batch_store.py` | `batches` | Keep as-is (already table-specific) |
| `user_store.py` | `users` | Keep as-is (already table-specific) |
| `sync_store.py` | `product_sync_schedule` | Keep CRUD operations |
| `sync_analytics.py` | `product_sync_schedule` | Extract dashboard/stats queries |

### base_store.py — The Shared Foundation

All store files inherit from `BaseStore`, which provides:
- Supabase client initialization (via `SupabaseClient`)
- Generic `_insert()`, `_upsert()`, `_select()`, `_update()` helper methods
- Consistent error handling and logging

Each table-specific store inherits `BaseStore` and adds only methods for its table.

---

## Constants Centralized in `core/constants.py`

Every hardcoded value that exists in 2+ files today moves to one place.

| Constant | Value | Currently Scattered Across |
|----------|-------|---------------------------|
| Default certificate | FAA 8130-3 | shopify_service, boeing_normalize, publishing task |
| Default condition | NE | shopify_service, boeing_normalize, publishing task |
| Price markup factor | 1.1 (10%) | shopify_service, boeing_normalize, publishing task, sync_dispatcher |
| Default supplier/vendor | BDI | boeing_normalize, shopify_client |
| Fallback image URL | placehold.co/... | shopify_service, publishing task |
| Product category GID | gid://shopify/TaxonomyCategory/vp-1-1 | shopify_client |
| Product tags | boeing, aerospace, Aircraft Parts | shopify_client |
| System user ID | system | boeing_service, shopify_service, all tasks |
| Default currency | USD | sync_helpers |
| UOM mapping table | EA, Inches, Pound, Pack... | shopify_client |
| Cert mapping table | FAA 8130-3, EASA Form 1... | shopify_client |
| Trace allowed domains | cdn.shopify.com, getsmartcert.com | shopify_client |
| Rate limiter capacity | 2 tokens | rate_limiter |
| JWKS cache TTL | 3600 seconds | cognito |
| Quote defaults | OH, OUTRIGHT, ON REQUEST | zap_service |
| Metafield definitions | 20 field definitions | shopify_client |

---

## Files That Get Split (Over 300-400 Lines Today)

| File Today | Lines | Becomes |
|---|---|---|
| `clients/shopify_client.py` | 1112 | `clients/shopify_client.py` (~280, HTTP only) + `services/publishing_mapper.py` (~350, transformation) |
| `db/supabase_store.py` | 794 | `db/base_store.py` (~90) + `db/raw_data_store.py` (~30) + `db/staging_store.py` (~200) + `db/product_store.py` (~150) + `db/quote_store.py` (~20) + `db/image_store.py` (~200) |
| `db/sync_store.py` | 766 | `db/sync_store.py` (~380) + `db/sync_analytics.py` (~200) |
| `celery_app/tasks/sync_dispatcher.py` | 660 | `services/sync_service.py` (~350) + thin `tasks/sync.py` (~120) |
| `routes/sync.py` | 564 | `schemas/sync.py` (~130) + `routes/sync.py` (~250) |
| `utils/sync_helpers.py` | 562 | `utils/sync_helpers.py` (~300) + `utils/hash_utils.py` (~120) |
| `routes/bulk.py` | 543 | Split across `routes/extraction.py`, `routes/publishing.py`, `routes/batches.py`, `routes/products.py` |
| `routes/multi_part_search.py` | 487 | `schemas/search.py` (~80) + `services/search_service.py` (~200) + `routes/search.py` (~80) |
| `celery_app/tasks/publishing.py` | 436 | `services/publishing_service.py` (~280) + thin `tasks/publishing.py` (~80) |
| `main.py` | 300 | `routes/health.py` (~60) + slimmed `main.py` (~200) |

---

## File Migration Map

### Current -> New (routes)
| Current | New | Reason |
|---------|-----|--------|
| `routes/boeing.py` | `routes/extraction.py` | Named by pipeline, not external system |
| `routes/shopify.py` | `routes/publishing.py` | Named by pipeline, not external system |
| `routes/bulk.py` | Split into `extraction.py`, `publishing.py`, `batches.py`, `products.py` | Mixed resources separated |
| `routes/sync.py` | `routes/sync.py` | Models extracted to schemas |
| `routes/products.py` | `routes/products.py` | Merged with staging + raw-data |
| `routes/zap.py` | `routes/webhooks.py` | Named by function, not vendor |
| `routes/multi_part_search.py` | `routes/search.py` | Simpler name |
| `routes/auth.py` | `routes/auth.py` | Same name |

### Current -> New (services)
| Current | New | Reason |
|---------|-----|--------|
| `services/boeing_service.py` | `services/extraction_service.py` | Matches extraction pipeline name |
| `services/shopify_service.py` | Merged into `services/publishing_service.py` | Matches publishing pipeline name |
| `services/zap_service.py` | `services/webhook_service.py` | Matches webhooks route name |
| `services/cognito_admin.py` | `services/auth_service.py` | Matches auth route name |
| -- | `services/publishing_mapper.py` (NEW) | Transformation logic from shopify_client.py |
| -- | `services/sync_service.py` (NEW) | Logic extracted from tasks/sync_dispatcher.py |
| -- | `services/batch_service.py` (NEW) | Logic extracted from routes/bulk.py |
| -- | `services/search_service.py` (NEW) | Logic extracted from routes/multi_part_search.py |

### Current -> New (schemas)
| Current | New | Reason |
|---------|-----|--------|
| `schemas/boeing.py` | `schemas/extraction.py` | Matches extraction route name |
| `schemas/shopify.py` | `schemas/publishing.py` | Matches publishing route name |
| `schemas/bulk.py` | `schemas/batches.py` | Matches batches route name |
| `schemas/zap.py` | `schemas/webhooks.py` | Matches webhooks route name |
| `schemas/products.py` | `schemas/products.py` | Same (core data models) |
| `schemas/auth.py` | `schemas/auth.py` | Same |
| -- | `schemas/sync.py` (NEW) | Models extracted from routes/sync.py |
| -- | `schemas/search.py` (NEW) | Models extracted from routes/multi_part_search.py |

### Current -> New (tasks)
| Current | New | Reason |
|---------|-----|--------|
| `tasks/extraction.py` | `tasks/extraction.py` | Same -- now calls extraction_service |
| `tasks/normalization.py` | `tasks/normalization.py` | Same -- now calls extraction_service.normalize() |
| `tasks/publishing.py` | `tasks/publishing.py` | Slimmed -- now calls publishing_service |
| `tasks/sync_dispatcher.py` | `tasks/sync.py` | Renamed to match sync pipeline |
| `tasks/batch.py` | `tasks/batch.py` | Same -- now calls batch_service |

### Current -> New (db)
| Current | New | Reason |
|---------|-----|--------|
| `db/supabase_store.py` | `db/base_store.py` + `db/raw_data_store.py` + `db/staging_store.py` + `db/product_store.py` + `db/quote_store.py` + `db/image_store.py` | Split by table |
| `db/sync_store.py` | `db/sync_store.py` + `db/sync_analytics.py` | Split: CRUD vs dashboards |
| `db/batch_store.py` | `db/batch_store.py` | Same (already table-specific) |
| `db/user_store.py` | `db/user_store.py` | Same (already table-specific) |

---

## Implementation Phases

### Phase 1 — Foundation (no breaking changes)
- Create `core/constants.py` with all business constants
- Create `container.py` with lazy DI container
- Create `core/middleware.py` with CORS configuration
- Create `schemas/sync.py` with models extracted from routes/sync.py
- Create `schemas/search.py` with models extracted from routes/multi_part_search.py

### Phase 2 — Move celery_app inside app/
- Move `celery_app/` directory into `app/celery_app/`
- Update all task import paths
- Update Celery subprocess commands in main.py
- Rename `tasks/sync_dispatcher.py` to `tasks/sync.py`

### Phase 3 — Split shopify_client.py
- Create `services/publishing_mapper.py` with all transformation logic
- Strip `clients/shopify_client.py` down to HTTP calls only
- Move mapping tables to `core/constants.py`

### Phase 4 — Split database files
- Create `db/base_store.py` with shared helpers from supabase_store.py
- Split `supabase_store.py` into `raw_data_store.py`, `staging_store.py`, `product_store.py`, `quote_store.py`, `image_store.py`
- Split `sync_store.py` into `sync_store.py` (core) + `sync_analytics.py`
- Update all imports across the codebase

### Phase 5 — Extract services from tasks (pipeline naming)
- Create `services/extraction_service.py` (from boeing_service.py + task logic)
- Create `services/publishing_service.py` (from shopify_service.py + task logic)
- Create `services/sync_service.py` (from tasks/sync_dispatcher.py logic)
- Create `services/batch_service.py` (from routes/bulk.py inline logic)
- Create `services/search_service.py` (from routes/multi_part_search.py logic)
- Rename `services/zap_service.py` to `services/webhook_service.py`
- Rename `services/cognito_admin.py` to `services/auth_service.py`
- Slim all Celery tasks to thin wrappers that call services
- Update `tasks/base.py` to use the Container

### Phase 6 — Restructure routes (pipeline naming + v1 prefix at mount)
- Create `routes/__init__.py` with router aggregation and `/api/v1` prefix mounting
- Create `routes/extraction.py` (from boeing.py + bulk search from bulk.py)
- Create `routes/publishing.py` (from shopify.py + bulk publish from bulk.py)
- Create `routes/batches.py` (batch CRUD from bulk.py)
- Create `routes/products.py` (merge published + staging + raw-data)
- Create `routes/sync.py` (handlers only, models in schemas)
- Create `routes/search.py` (from multi_part_search.py)
- Create `routes/webhooks.py` (from zap.py)
- Create `routes/auth.py` (from auth.py)
- Create `routes/health.py` (from main.py inline endpoints)
- Rename schemas to match: boeing.py to extraction.py, shopify.py to publishing.py, etc.
- Slim down main.py

### Phase 7 — Constants integration + utils split
- Replace every hardcoded value with imports from `core/constants.py`
- Split `sync_helpers.py` to keep slot/batch/time logic + extract hash functions to `hash_utils.py`
- Remove all singleton factory functions

### Phase 8 — Cleanup and verification
- Delete old files replaced by new structure
- Update all test imports
- Run full test suite
- Verify every file under 400 lines
- Verify no hardcoded constants outside `constants.py`
- Test complete pipeline end-to-end

---

## Verification Checklist

- Every API endpoint responds correctly on new `/api/v1/` paths
- Celery workers register all tasks from `app.celery_app.tasks`
- Celery Beat fires scheduled tasks at configured intervals
- Bulk search -> extraction -> normalization pipeline works end-to-end
- Bulk publish -> Shopify creation with saga compensation works
- Sync dispatcher -> Boeing fetch -> change detection -> Shopify update works
- No file in `backend/app/` exceeds 400 lines
- No business constant appears outside `core/constants.py`
- Every pipeline has matching names across routes, tasks, services, and schemas
- All existing tests pass with updated imports
- Health check returns 200

---

## Reference Sources

- zhanymkanov/fastapi-best-practices — Domain-based module structure, Netflix Dispatch-inspired
- Camillo Visini: FastAPI Service Abstraction — Same root name across all layers
- Viktor Sapozhok: FastAPI 3-Tier Design — Matching filenames across packages
- TestDriven.io: FastAPI + Celery — Thin tasks calling services
- FastAPI Official: Bigger Applications — Multi-file router organization
