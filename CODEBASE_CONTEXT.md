# CODEBASE_CONTEXT.md

> Phase 1 onboarding reference for the **Boeing Data Hub** monorepo.
> Snapshot taken on branch `restructure-v3` (parent PR target: `main`).
> Treat this file as the source of truth for what currently exists in the repo —
> not for what *should* exist or for any open work.

---

## 1. Overview

**Boeing Data Hub** is a multi-tenant pipeline that:

1. **Extracts** part-level price/availability data from the Boeing Commerce
   Connect / Aviall *Part Price & Availability (PNA)* API (in bulk).
2. **Normalizes** the raw Boeing payload into a Shopify-friendly product schema
   (price, inventory, location, dimensions, metafields, image).
3. **Publishes** products into a Shopify storefront (single REST product per
   SKU, multi-location inventory, custom metafields, category tag).
4. **Synchronizes** published products back to Boeing on a hourly/weekly
   schedule, detects price/inventory changes, and pushes deltas to Shopify.
5. Generates dashboard-style **HTML cycle reports** (sync start + cycle
   complete) and emails them via Resend.

The system is structured as two deployables:

- **`backend/`** — FastAPI (HTTP API) + Celery (workers + Beat) + Redis
  (broker, locks, rate limiter, cycle tracker) + Supabase (Postgres + Storage).
  Deployed to an EC2 host via GitHub Actions.
- **`frontend/`** — Vite + React + TypeScript SPA with shadcn/ui, Tailwind,
  TanStack Query, Supabase Realtime. Deployed via AWS Amplify (per
  `.github/workflows/deploy-backend.yml` comment "frontend is deployed on
  Amplify"; no Amplify YAML in the repo).
- **Auth** is delegated to **AWS Cognito** via SSO from a separate **Aviation
  Gateway** service (not in this repo). The frontend never talks to Cognito
  directly — it just receives a Cognito access token in a URL fragment.

Repository layout (top-level):

```
backend/                 FastAPI + Celery service
frontend/                Vite + React SPA
database/                SQL schema dump + numbered migrations
docs/                    Misc internal docs
Documentation/           Misc internal docs
.github/workflows/       deploy-backend.yml (rsync + systemctl restart)
*.md                     Plans/guides (CELERY_REDIS_IMPLEMENTATION_PLAN,
                         RESTRUCTURE_PLAN(_V1), QUALITY_PLAN(_SUMMARY),
                         DEPLOYMENT_GUIDE, EC2_DEPLOYMENT_GUIDE,
                         DEVELOPER_IMPLEMENTATION_GUIDE,
                         AUDIT_VENDOR_ALERTS)
sync_scheduler_token_bucket.excalidraw.json
```

Top-of-tree status fields (working tree at start of session, not committed):

```
modified:  .claude/settings.local.json
untracked: AUDIT_VENDOR_ALERTS.md
           backend/scripts/fix_locations.py
           backend/scripts/manual_sync.py
           backend/scripts/reports/
           backend/scripts/send_combined_report.py
```

---

## 2. Architecture

### 2.1 High-level topology

```
                ┌─────────────────────────┐
                │  Aviation Gateway (SSO) │  (external, AWS Cognito-backed)
                └────────────┬────────────┘
                             │ access_token (URL fragment)
                             ▼
┌──────────────┐    HTTPS    ┌──────────────────────┐    HTTPS     ┌──────────────┐
│  Browser     │────────────►│  FastAPI app (EC2)   │─────────────►│  Boeing PNA  │
│  (React SPA) │  Bearer JWT │  /api/v1/*           │  OAuth2 +    │  REST + GraphQL
│              │             │  /api/* (legacy)     │  x-part-     │              │
└──────┬───────┘             │  /health             │  access-token│              │
       │                     └────────┬─────────────┘              └──────────────┘
       │ Supabase Realtime            │ enqueue       ┌──────────────┐
       │ (anon key, postgres_changes) │ tasks         │  Shopify     │
       │                              ▼               │  Admin API   │
       │                     ┌──────────────────┐     │  REST +      │
       │                     │  Redis (broker,  │     │  GraphQL     │
       │                     │  locks, tokens)  │     └──────┬───────┘
       │                     └────────┬─────────┘            │
       │                              │ broker               │
       │                              ▼                      │
       │                     ┌──────────────────┐            │
       │                     │  Celery workers  │────────────┘
       │                     │  + Celery Beat   │
       │                     └────────┬─────────┘
       │                              │
       ▼                              ▼
┌─────────────────────────────────────────────────────────┐
│  Supabase (Postgres + Storage + Realtime publication)   │
│  tables: users, batches, product_staging, product,      │
│          boeing_raw_data, product_sync_schedule,        │
│          sync_reports                                   │
│  storage bucket: SUPABASE_STORAGE_BUCKET (default       │
│                  "product-images")                      │
└─────────────────────────────────────────────────────────┘
```

### 2.2 Pipeline stages (ingest)

`POST /api/v1/extraction/bulk-search` →
`process_bulk_search` (orchestrator) splits into chunks of `BOEING_BATCH_SIZE`
(default 10) →
`extract_chunk` (queue: `extraction`) calls Boeing PNA, writes raw payload to
`boeing_raw_data`, then chains →
`normalize_chunk` (queue: `normalization`) maps line items into the staging
schema, applies **location blocking** (drop products only at non-mapped
warehouses), upserts into `product_staging`. After every chunk the task
schedules `check_batch_completion`. Finally a deferred `reconcile_batch` is
queued (5 min × chunks, clamped to [5min, 30min]).

`POST /api/v1/publishing/bulk-publish` →
`publish_batch` orchestrator pre-computes Shopify-side slot assignments via
`precompute_slot_assignments` and queues per-SKU →
`publish_product` (queue: `publishing`, rate-limit `30/m`) which delegates to
`PublishingService.publish_product_for_batch` (4-tier idempotency: existing
shopify_product_id on staging → Shopify SKU search → `product` table lookup
→ CREATE).

### 2.3 Pipeline stages (sync)

Celery Beat schedule (`_build_beat_schedule`):

- `dispatch-hourly-sync` — `tasks.sync_dispatch.dispatch_hourly`, every
  configured `SYNC_DISPATCH_MINUTE`.
- `dispatch-retry-sync` — `tasks.sync_dispatch.dispatch_retry`, every
  `SYNC_RETRY_HOURS` hours at minute :15.
- `end-of-day-cleanup` — `tasks.sync_dispatch.end_of_day_cleanup` daily at
  `SYNC_CLEANUP_HOUR`:00 UTC.

If `SYNC_FREQUENCY=weekly`, `day_of_week` is restricted to `SYNC_WEEKLY_DAY`.
If `SYNC_ENABLED=false`, the schedule is empty.

`dispatch_hourly` flow:

1. **Conflict guard** — if any extraction/publish batch is `pending|processing`,
   record the bucket in Redis set `deferred_sync_buckets:{date}` and return.
2. **Dispatch idempotency lock** (Redis SET NX EX) keyed by
   `dispatch_lock:{date}:{bucket}` (TTL 10min testing / 1h prod).
3. **Cycle start detection** — `record_cycle_start()` (Redis `SET NX EX`); on
   first bucket of a new cycle, queue `send_cycle_start_notification`.
4. **Passive catch-up path** — if there are deferred buckets, acquire the
   shared catchup lock and re-dispatch them, then `clear_deferred_buckets`.
5. **Process current bucket** — `SyncDispatchService.dispatch_bucket`
   (active vs filling slot logic; aggregates filling slots; applies Layer 2
   `last_sync_at < window_start` filter and Layer 3 Redis SKU dedup).
6. `reset_stuck_products(stuck_threshold_minutes=30)`.
7. Record bucket in cycle Redis set; if all `MAX_BUCKETS` buckets dispatched →
   queue `wait_for_cycle_completion`.

`dispatch_deferred_catchup` (active path) is queued by
`check_batch_completion` when the last active batch finishes and there are
deferred buckets. Same catchup lock prevents duplication with the passive path.

`process_boeing_batch` (queue: `sync_boeing`) is the per-batch worker. It
acquires the `BoeingRateLimiter` token, calls Boeing PNA, runs change
detection (`should_update_shopify`), and for each changed SKU queues
`update_shopify_product` (queue: `sync_shopify`). For "no change" SKUs it
just updates the sync record's hash + price + qty + status + locations.

`update_shopify_product` (queue: `sync_shopify`, rate-limit `30/m`) updates
Shopify variant price + inventory + `boeing.location_summary` metafield via
`ShopifyOrchestrator`.

When all buckets of a cycle are dispatched and `get_syncing_count()` returns
0, `wait_for_cycle_completion` triggers `generate_cycle_report` which builds
a static HTML dashboard (no LLM call despite `gemini_client` being present —
the LLM client is wired up but not invoked from `report_service.py`) and
emails it via Resend.

### 2.4 Worker / queue topology

Six queues are declared in `celery_config.py`:

| Queue           | Tasks                                                                 |
|-----------------|-----------------------------------------------------------------------|
| `extraction`    | `tasks.extraction.extract_chunk`                                      |
| `normalization` | `tasks.normalization.normalize_chunk`                                 |
| `publishing`    | `tasks.publishing.publish_product`, `publish_batch`                   |
| `default`       | `tasks.batch.*`, `tasks.sync_dispatch.*`, `tasks.report_generation.*` |
| `sync_boeing`   | `tasks.sync_boeing.process_boeing_batch`                              |
| `sync_shopify`  | `tasks.sync_shopify.update_shopify_product`, `sync_single_product_immediate` |

Per-task rate limits (configurable via env):

- `extract_chunk`, `process_boeing_batch` → `BOEING_API_RATE_LIMIT` (default `20/m`).
- `publish_product`, `update_shopify_product` → `SHOPIFY_API_RATE_LIMIT` (default `30/m`).

Global Boeing rate limiter (Redis token bucket) lives in
`utils/rate_limiter.py` (capacity 2, refill 2 per 60s by default; configurable
via `BOEING_RATE_LIMIT_CAPACITY` / `BOEING_RATE_LIMIT_REFILL`). Workers must
call `wait_for_token()` before each Boeing API call.

`worker_prefetch_multiplier=1`, `task_acks_late=True`,
`task_reject_on_worker_lost=True`. On Windows, `worker_pool=solo`, otherwise
`prefork`. `result_expires=3600`, `task_default_retry_delay=30`,
`task_max_retries=3`, `broker_transport_options.visibility_timeout=3600`.

In production (per `redeploy.sh` and `deploy-backend.yml`), six systemd
services run:

- `boeing-backend`           — uvicorn FastAPI on `:8000`
- `boeing-celery-extract`    — extract + normalize queues
- `boeing-celery-publish`    — publish queue
- `boeing-celery-sync`       — sync_boeing + sync_shopify
- `boeing-celery-default`    — default queue (dispatchers, batch ops, reports)
- `boeing-celery-beat`       — Celery Beat scheduler

`main.py` *also* contains an embedded subprocess autostart of one Celery
worker + Beat when `AUTO_START_CELERY=true`. Comment in code warns that
in prod this should be `false` to avoid duplicate task consumption.

### 2.5 Service layer convention ("thin tasks, fat services")

Documented in CLAUDE.md memory: every Celery task file contains only
`@celery_app.task` decorators, retry config, error routing, and Celery
callbacks. All business logic lives in service classes under
`backend/app/services/`, accessed via factory helpers in
`backend/app/celery_app/tasks/base.py`:

- `get_batch_completion_service()` → `BatchCompletionService`
- `get_normalization_service()` → `NormalizationService` (carries
  `settings.shopify_location_map`)
- `get_publishing_service()` → `PublishingService`
- `get_boeing_fetch_service()` → `BoeingFetchService` (carries rate limiter)
- `get_shopify_update_service()` → `ShopifyUpdateService`
- `get_sync_dispatch_service()` → `SyncDispatchService`

Tasks use `run_async()` (also in `base.py`) to invoke async service methods
inside sync Celery workers (each call spins up a fresh event loop).

Services return dicts with control flags such as `trigger_catchup` and
`trigger_completion_check`. Tasks act on those flags to enqueue Celery
sub-tasks — services never import task modules.

### 2.6 Idempotency, locking, and failure routing

- **Batches table** has `idempotency_key UNIQUE`. Routes look up an existing
  batch before creating a new one when an `idempotency_key` is sent.
- **`PublishTask.on_failure`** (custom Celery base for `publish_product`)
  acts as a last-resort safety net: ensures `failed_items` and
  `update_product_staging_status('failed')` are written even if the in-task
  except handlers themselves blow up.
- **4-tier publish idempotency** (in `PublishingService.publish_product_for_batch`):
  1. existing `staging.shopify_product_id` → UPDATE
  2. Shopify SKU lookup (GraphQL `productVariants`, REST fallback) → UPDATE
  3. `product.shopify_product_id` lookup → UPDATE
  4. CREATE new product (with **saga compensation**: if DB save fails after
     CREATE, delete the orphaned Shopify product).
- **Five Redis dedup mechanisms** (utils/dispatch_lock.py):
  1. `dispatch_lock:{date}:{bucket}` — only one `dispatch_hourly` per bucket.
  2. `dispatched_skus:{date}:{bucket}` — set of SKUs already dispatched.
  3. `batch_lock:{md5(skus)}` — only one worker processes a given Boeing batch.
  4. `deferred_sync_buckets:{date}` — buckets skipped due to active extraction.
  5. `deferred_catchup_lock:{date}` — only one of active/passive paths
     processes deferred buckets.
- **DB trigger `trg_update_batch_stats`** on `product_staging` keeps
  `batches.extracted_count|normalized_count|published_count` in sync with
  actual rows. RPC `record_batch_failure` atomically increments
  `failed_count` and appends to `failed_items` jsonb (`{part_number, error,
  stage, timestamp}`).

### 2.7 Exception model

`backend/app/core/exceptions.py` defines a clean retryable / non-retryable
split that Celery `autoretry_for` / `dont_autoretry_for` honour:

- `BoeingDataHubException` (base)
  - `RetryableError`
    - `ExternalAPIError(service, message, status_code)`
    - `RateLimitError(service, retry_after)`
    - `ConnectionTimeoutError`
    - `DatabaseTransientError`
  - `NonRetryableError`
    - `ValidationError`
    - `BatchNotFoundError`
    - `ProductNotFoundError`
    - `InvalidPartNumberError`
    - `AuthenticationError`

Tasks add `httpx.ConnectError`, `httpx.ReadTimeout`, builtin `ConnectionError`,
`TimeoutError` to their retryable list.

---

## 3. Directory map

### 3.1 Repo root

```
backend/                FastAPI + Celery service (deployed)
frontend/               Vite + React SPA
database/               SQL schema dump + numbered migrations
docs/                   Architectural docs (out-of-scope for this pass)
Documentation/          Misc internal docs (out-of-scope for this pass)
.github/workflows/      deploy-backend.yml (rsync + systemctl restart on EC2)
.claude/                Local agent config (workspace-local)
*.md                    Plans + guides (see §1 list)
sync_scheduler_token_bucket.excalidraw.json
```

### 3.2 `backend/`

```
app/
├── __init__.py
├── main.py                    FastAPI lifespan, optional Celery autostart
├── container.py               Lazy DI container (lru_cache singletons)
│                              for HTTP routes
├── celery_app/
│   ├── __init__.py            Re-exports `celery_app`
│   ├── celery_config.py       Celery broker/queues/routes/beat schedule
│   └── tasks/
│       ├── __init__.py
│       ├── base.py            BaseTask, run_async, worker-local DI,
│       │                      service factories
│       ├── batch.py           check_batch_completion, reconcile_batch,
│       │                      cancel_batch, cleanup_stale_batches
│       ├── extraction.py      process_bulk_search, extract_chunk
│       ├── normalization.py   normalize_chunk
│       ├── publishing.py      PublishTask, publish_batch, publish_product
│       ├── report_generation.py  send_cycle_start_notification,
│       │                      wait_for_cycle_completion,
│       │                      generate_cycle_report
│       ├── sync_boeing.py     process_boeing_batch
│       ├── sync_dispatch.py   dispatch_hourly, dispatch_deferred_catchup,
│       │                      dispatch_retry, end_of_day_cleanup
│       └── sync_shopify.py    update_shopify_product,
│                              sync_single_product_immediate
├── clients/                   Thin HTTP wrappers
│   ├── boeing_client.py       OAuth2 + x-part-access-token; PNA REST
│   ├── shopify_client.py      Shopify Admin REST + GraphQL transport
│   ├── supabase_client.py     supabase-py wrapper
│   ├── gemini_client.py       google-generativeai wrapper (instantiated
│   │                          but currently NOT invoked — see §6)
│   └── resend_client.py       Resend transactional email
├── core/
│   ├── auth.py                FastAPI dependencies (get_current_user,
│   │                          require_groups, require_admin)
│   ├── cognito.py             JWKS fetch + JWT verify
│   ├── config.py              pydantic Settings (env loader)
│   ├── exceptions.py          RetryableError / NonRetryableError tree
│   ├── middleware.py          CORS (open `*`)
│   └── constants/
│       ├── __init__.py        Re-exports
│       ├── extraction.py      DEFAULT_SUPPLIER, BOEING_BATCH_SIZE, ...
│       ├── pricing.py         MARKUP_FACTOR=1.1, FALLBACK_IMAGE_URL, ...
│       ├── publishing.py      METAFIELD_DEFINITIONS, UOM_MAPPING,
│       │                      CERT_MAPPING, PRODUCT_CATEGORY_GID
│       └── sync.py            MIN_PRODUCTS_FOR_ACTIVE_SLOT=10,
│                              MAX_SKUS_PER_API_CALL=10,
│                              STUCK_THRESHOLD_MINUTES=30
├── db/                        Supabase access layer (CRUD only)
│   ├── base_store.py          Shared insert/upsert/select/update helpers
│   ├── batch_store.py         batches table CRUD + counters + failures
│   ├── image_store.py         Supabase Storage upload (Boeing → bucket)
│   ├── product_store.py       product table CRUD + price/qty updates
│   ├── raw_data_store.py      boeing_raw_data inserts
│   ├── report_store.py        sync_reports CRUD
│   ├── staging_store.py       product_staging CRUD + status updates
│   ├── sync_analytics.py      Dashboard aggregates (slot distribution,
│   │                          status summary)
│   └── sync_store.py          product_sync_schedule CRUD; singleton via
│                              get_sync_store()
├── routes/                    FastAPI routers (mounted at /api/v1/*)
│   ├── __init__.py            v1_router aggregator + legacy_router export
│   ├── auth.py                /auth/me, /auth/logout
│   ├── batches.py             /batches[ /{id} ] GET/DELETE
│   ├── extraction.py          /extraction/search, /extraction/bulk-search
│   ├── health.py              /health
│   ├── legacy.py              /api/* alias of /api/v1/* for old clients
│   ├── products.py            /products/published, /products/staging,
│   │                          /products/raw-data/{pn}
│   ├── publishing.py          /publishing/publish, /publishing/bulk-publish,
│   │                          /publishing/products/{id} PUT,
│   │                          /publishing/check, /publishing/metafields/setup
│   ├── reports.py             /reports/generate, /reports/latest,
│   │                          /reports/cycle-progress
│   ├── search.py              /search/multi-part
│   └── sync.py                /sync/dashboard, /products, /history,
│                              /failures, /hourly-stats,
│                              /product/{sku}, /product/{sku}/reactivate,
│                              /trigger/{sku}
├── schemas/                   Pydantic request/response models
│   ├── auth.py                User, LogoutResponse
│   ├── batches.py             BulkSearch/Publish requests, BatchStatusResponse
│   ├── extraction.py
│   ├── products.py            NormalizedProduct, ShopifyProductModel,
│   │                          LocationAvailability, LocationQuantity
│   ├── publishing.py          PublishRequest/Response, UpdateRequest,
│   │                          CheckResponse
│   ├── reports.py
│   ├── search.py              MultiPartSearchRequest/Response
│   └── sync.py                Dashboard, products, history, failures, hourly
├── services/                  Business logic layer
│   ├── auth_service.py        Cognito GlobalSignOut via boto3
│   ├── batch_completion_service.py  check/reconcile/cancel/cleanup
│   ├── batch_service.py       Bulk-search/publish orchestration
│   ├── boeing_fetch_service.py  Sync-time fetch + change detection
│   ├── extraction_service.py  Single-search variant of bulk extraction
│   ├── normalization_service.py  Boeing→staging mapping + location blocking
│   ├── products_service.py    Read-only published product list/get
│   ├── publishing_service.py  4-tier publish + saga compensation
│   ├── report_service.py      HTML cycle reports (start + complete)
│   ├── search_service.py      Multi-SKU GraphQL search against Shopify
│   ├── shopify_inventory_service.py  Locations + inventory levels + cost
│   ├── shopify_orchestrator.py  Product CRUD glue
│   ├── shopify_update_service.py  Sync-time price/inventory update
│   └── sync_dispatch_service.py  hourly/retry/cleanup business logic
├── utils/
│   ├── batch_grouping.py      calculate_batch_groups, aggregate_filling_slots
│   ├── boeing_data_extract.py extract_boeing_product_data,
│   │                          create_out_of_stock_data
│   ├── boeing_normalize.py    normalize_boeing_payload (raw → staging dict)
│   ├── change_detection.py    should_update_shopify (hash + diff)
│   ├── cycle_tracker.py       Redis cycle: start, bucket-dispatched,
│   │                          changes, progress, reset
│   ├── dispatch_lock.py       5 Redis dedup mechanisms (see §2.6)
│   ├── hash_utils.py          compute_boeing_hash, compute_sync_hash
│   ├── rate_limiter.py        BoeingRateLimiter token bucket (Lua script)
│   ├── schedule_helpers.py    bucket helpers, sync window, retry backoff
│   ├── shopify_payload_builder.py  build_product_payload, build_metafields,
│   │                          map_unit_of_measure, map_cert,
│   │                          validate_trace_url, map_inventory_location
│   ├── slot_manager.py        get_optimal_slot,
│   │                          precompute_slot_assignments,
│   │                          get_slot_distribution
│   └── type_converters.py     to_float, to_int (None on invalid)
├── tests/                     conftest + unit/integration/e2e dirs
│                              (out of scope — read but not summarized)
├── scripts/                   Operational scripts
│   ├── redeploy.sh            stop/reload/start six systemd services + curl /health
│   ├── start_workers.bat
│   ├── start_workers.ps1
│   ├── stop_workers.bat
│   ├── backfill_sync_schedules.py
│   ├── create_products.py
│   ├── script.py
│   ├── fix_locations.py       (untracked)
│   ├── manual_sync.py         (untracked)
│   ├── send_combined_report.py (untracked)
│   └── reports/               (untracked output dir)
├── celerybeat-schedule*       SQLite-backed Beat schedule snapshot
├── requirements.txt           Production deps
├── requirements-dev.txt       + pytest, ruff, black, mypy, fakeredis, ...
├── .env / .env.example        Env vars (see §9)
└── readme.md
```

### 3.3 `frontend/`

```
src/
├── main.tsx                Entry, mounts <App/>
├── App.tsx                 QueryClientProvider → AuthProvider → Router
│                           Single route "/" → ProtectedRoute → Index
├── index.css               Tailwind base
├── App.css
├── components/
│   ├── NavLink.tsx
│   ├── ProtectedRoute.tsx  Redirects to Aviation Gateway login if unauth
│   ├── dashboard/
│   │   ├── AutoSyncPanel.tsx
│   │   ├── BulkOperationsPanel.tsx
│   │   ├── EditProductModal.tsx
│   │   ├── ErrorAlert.tsx
│   │   ├── FailedProductsList.tsx
│   │   ├── Header.tsx
│   │   ├── HourlyDistributionChart.tsx
│   │   ├── ProductTable.tsx
│   │   ├── PublishedProductsPanel.tsx
│   │   ├── SearchPanel.tsx
│   │   ├── StatusBadge.tsx
│   │   ├── SyncHistoryTable.tsx
│   │   ├── SyncStatusCards.tsx
│   │   └── Toolbar.tsx
│   └── ui/                 shadcn/ui (Radix-based) primitives — full set
├── contexts/
│   └── AuthContext.tsx     SSO token from URL fragment, sessionStorage,
│                           SLO via hidden iframe + postMessage
├── hooks/
│   ├── use-mobile.tsx
│   ├── use-toast.ts
│   ├── useBulkOperations.ts
│   ├── useProducts.ts
│   ├── usePublishedProducts.ts
│   └── useSyncDashboard.ts
├── lib/
│   └── utils.ts            cn() helper (clsx + tailwind-merge)
├── pages/
│   ├── Index.tsx           3-tab dashboard (Fetch & Process / Published /
│   │                       Auto-Sync)
│   └── NotFound.tsx
├── services/
│   ├── authService.ts      sessionStorage token + redirectToLogin
│   ├── boeingService.ts    /extraction/search wrapper
│   ├── bulkService.ts      /extraction/bulk-search, /publishing/bulk-publish,
│   │                       /batches, /products/staging,
│   │                       /products/raw-data/{pn}
│   ├── productsService.ts
│   ├── realtimeService.ts  Supabase Realtime + RPCs (get_batch_stats,
│   │                       get_batch_part_numbers_with_status) +
│   │                       fetchPublishedProducts (anon-key path)
│   ├── shopifyService.ts
│   ├── supabaseService.ts  Direct anon-key writes to product_staging
│   │                       (saveNormalizedProduct, fetchNormalizedProducts,
│   │                       updateProductStatus)
│   └── syncService.ts      All /sync/* endpoints
├── types/
│   └── product.ts          BoeingProduct, NormalizedProduct,
│                           BatchStatusResponse, BatchListResponse, ...
└── vite-env.d.ts
```

Tooling: Vite 5 (`vite.config.ts` — alias `@→src`, dev port 8080, plugin
`@vitejs/plugin-react-swc`, dev-only `lovable-tagger`), Tailwind 3
(`tailwind.config.ts` + `postcss.config.js`), ESLint 9 with
`typescript-eslint`. Build outputs to `dist/`. Bun lockfile committed
(`bun.lockb`) alongside `package-lock.json`.

### 3.4 `database/`

```
complete_db_schema.sql            Idempotent CREATEs for full target schema
                                  (users, product_staging, product, batches,
                                  boeing_raw_data, product_sync_schedule
                                  with set_updated_at() trigger fn and all
                                  batch RPCs)
production-db-schema.sql          Snapshot of production at point-in-time
production_migration.sql          Idempotent migration from prod snapshot
                                  to target schema (begins with BEGIN;)
migration_001_add_auth.sql        users + user_id columns on
                                  product_staging/product/batches/
                                  boeing_raw_data
migration_002_add_unique_constraints.sql  (user_id, sku) UNIQUE on staging
                                  and product
migration_003_add_batch_id_to_staging.sql
migration_004_add_part_numbers_to_batches.sql
migration_005_sync_scheduler.sql  product_sync_schedule v1 (with
                                  next_sync_at, hash(sku) % 24 bucketing)
migration_006_sync_scheduler_v2.sql
migration_006_sync_tracking_columns.sql  last_inventory_status,
                                  last_location_summary,
                                  failed_part_numbers (legacy)
migration_007_fix_progress_calculation.sql
migration_008_sync_scheduler_production.sql  Full schedule v2 + 11 RPCs +
                                  trg_update_batch_stats trigger
migration_009_pipeline_error_tracking.sql  Drops failed_part_numbers,
                                  backfills stage+timestamp into failed_items
migration_010_sync_reports.sql    sync_reports table
migration_011_enhanced_sync_reports.sql  cycle_started_at, cycle_ended_at,
                                  report_type
triggers-and-RPC.json             Audit dump of all functions in DB
```

---

## 4. Data model

### 4.1 Tables (Postgres / Supabase, schema `public`)

> All timestamp columns are `TIMESTAMPTZ`. All tables have an `updated_at`
> column maintained by `set_updated_at()` trigger function (consolidated in
> `production_migration.sql`; older migrations show three legacy variants
> `set_product_updated_at`, `set_product_staging_updated_at`,
> `set_updated_at`).

**`users`** — legacy local auth, *unused at runtime* (auth is via Cognito).
- `id TEXT PK`, `username TEXT UNIQUE`, `password TEXT`, `created_at`, `last_login`.
- Seed row inserted: `('user_001', 'sk-user1', 'pulse123')`.

**`product_staging`** — normalized buffer between extraction and publish.
- `id TEXT PK` (uuid string), `sku TEXT`, `title TEXT`,
  `body_html`, `vendor`, `price NUMERIC`, `currency`, `inventory_quantity INT`,
  `inventory_status`, `weight`, `weight_unit`, `country_of_origin`,
  `dim_length/width/height NUMERIC`, `dim_uom`,
  `status TEXT DEFAULT 'fetched'` (values used in code: `fetched`, `normalized`,
  `published`, `blocked`, `failed`),
  `image_url`, `image_path`, `boeing_image_url`, `boeing_thumbnail_url`,
  `base_uom`, `hazmat_code`, `faa_approval_code`, `eccn`, `schedule_b_code`,
  `supplier_name`, `boeing_name`, `boeing_description`,
  `list_price`, `net_price`, `cost_per_item`, `location_summary`,
  `condition`, `pma BOOLEAN`, `estimated_lead_time_days INT`,
  `trace`, `expiration_date DATE`, `notes`,
  `user_id TEXT NOT NULL DEFAULT 'system'`,
  `shopify_product_id TEXT`,
  `batch_id TEXT`,
  `created_at`, `updated_at`.
- `UNIQUE (user_id, sku)`.
- Indexes: `user_id`; partial `shopify_product_id IS NOT NULL`; `batch_id`.
- Triggers: `trg_product_staging_updated_at`, `trg_update_batch_stats`
  (forwards INSERT/UPDATE/DELETE to `update_batch_stats_on_product_change()`).
- In `supabase_realtime` publication.

**`product`** — published catalogue (source of truth post-publish).
- Same shape as `product_staging` minus `batch_id` and `status`, plus
  `shopify_product_id`, `shopify_variant_id`, `shopify_handle`.
- `UNIQUE (user_id, sku)`. Trigger `trg_product_updated_at`. In realtime pub.

**`batches`** — bulk operation tracker.
- `id VARCHAR(36) PK` (uuid string), `batch_type VARCHAR(20)` constrained to
  one of `extract|normalize|publish` (production has `search|normalized|
  publishing|publish` — code emits the new names; migration 008 still has the
  old names referenced in RPC `CASE` arms),
  `status VARCHAR(20)` ∈ `pending|processing|completed|failed|cancelled`,
  `total_items INT`, `extracted_count INT`, `normalized_count INT`,
  `published_count INT`, `failed_count INT`,
  `error_message TEXT`,
  `failed_items JSONB DEFAULT '[]'` — each entry
  `{part_number, error, stage, timestamp}`,
  `celery_task_id VARCHAR(100)`,
  `idempotency_key VARCHAR(100) UNIQUE`,
  `user_id VARCHAR(50) DEFAULT 'system'`,
  `part_numbers JSONB DEFAULT '[]'` (production schema uses `TEXT[]`),
  `publish_part_numbers TEXT[]`,
  `skipped_count INT DEFAULT 0`, `skipped_part_numbers TEXT[] DEFAULT '{}'`,
  `created_at`, `updated_at`, `completed_at`.
- Indexes: `(status, created_at DESC)`, `user_id`,
  partial `idempotency_key IS NOT NULL`, partial `status IN ('pending','processing')`.
- In `supabase_realtime` publication.

**`boeing_raw_data`** — audit trail of every Boeing PNA call.
- `id UUID PK DEFAULT gen_random_uuid()`, `created_at`,
  `search_query TEXT NOT NULL` (comma-joined SKUs), `raw_payload JSONB`,
  `user_id TEXT DEFAULT 'system'`. Index on `user_id`.

**`product_sync_schedule`** — per-product hourly sync slot tracker.
- `id UUID PK`, `user_id TEXT`, `sku TEXT`, `UNIQUE (user_id, sku)`.
- `hour_bucket SMALLINT` ∈ [0,23], `sync_status TEXT` ∈ `pending|syncing|
  success|failed`, `last_sync_at`, `consecutive_failures INT DEFAULT 0`,
  `last_error TEXT`,
  `last_boeing_hash TEXT` (first 16 chars of SHA-256 of price/qty/status/locs),
  `last_price NUMERIC`, `last_quantity INT`,
  `last_inventory_status TEXT`, `last_locations JSONB` (or
  `last_location_summary TEXT` per migration 006 — migration 008 keeps the
  jsonb form; backend reads/writes `last_locations`).
- `is_active BOOLEAN DEFAULT TRUE` (set false after `SYNC_MAX_FAILURES`
  consecutive failures).
- Indexes: `idx_sync_hourly_dispatch (hour_bucket, sync_status, last_sync_at)
  WHERE is_active=TRUE`, `idx_sync_slot_distribution`,
  `idx_sync_failed_products`, `idx_sync_stuck`, `idx_sync_user`.
- Trigger `trg_sync_schedule_updated_at`. In realtime publication (added in
  migration 008).

**`sync_reports`** — generated cycle reports.
- `id UUID PK`, `cycle_id TEXT NOT NULL`, `report_text TEXT` (nullable since
  migration 011 to allow lightweight `cycle_start` rows), `summary_stats JSONB`,
  `file_path TEXT`, `email_sent BOOLEAN`, `email_recipients TEXT[]`,
  `cycle_started_at`, `cycle_ended_at`,
  `report_type TEXT NOT NULL DEFAULT 'cycle_complete'` (∈
  `cycle_start|cycle_complete`), `created_at`.
- Indexes: `(created_at DESC)`, `(report_type, created_at DESC)`.

### 4.2 RPC functions (Postgres)

Defined in `complete_db_schema.sql` and `migration_008_sync_scheduler_production.sql`:

- `set_updated_at()` — generic `BEFORE UPDATE` trigger fn.
- `update_batch_stats_on_product_change()` — `AFTER INSERT|UPDATE|DELETE` on
  `product_staging`, recomputes `extracted_count|normalized_count|
  published_count` for the row's `batch_id`.
- `increment_batch_extracted/normalized/published(p_batch_id, p_count)` —
  atomic counter bumps (called from Python only as a fallback; primary path
  is the trigger).
- `record_batch_failure(p_batch_id, p_part_number, p_error, p_stage)` — atomic
  `failed_count++` and append to `failed_items` jsonb.
- `recalculate_batch_stats(p_batch_id)` — full re-count from `product_staging`
  rows.
- `get_batch_stats(p_batch_id)` — real-time stats incl. `progress_percent`
  computed per-batch-type.
- `get_batch_product_status_counts(p_batch_id)` — group-by on `status`.
- `check_batch_completion(p_batch_id)` — server-side completion check;
  transitions to `completed`/`failed` and sets `completed_at`.
- `get_batch_part_numbers_with_status(p_batch_id)` — left join `part_numbers`
  array against `product_staging` to return per-PN status flags
  (`has_inventory`, `has_price`).

Frontend `realtimeService.ts` calls `get_batch_stats` and
`get_batch_part_numbers_with_status` directly via the supabase-js anon key.

### 4.3 Realtime publication

`supabase_realtime` publication includes `batches`, `product_staging`,
`product`, and (per migration 008) `product_sync_schedule`.
Frontend subscribes via supabase-js to:

- `batches-changes` — INSERT/UPDATE/DELETE on `batches`.
- `staging-{batchId}` — UPDATE on `product_staging` filtered by `batch_id`.
- `all-staging-updates` — INSERT/UPDATE on `product_staging` (no filter).
- `products-changes` — INSERT/UPDATE/DELETE on `product`.
- `sync-schedule-changes` — UPDATE on `product_sync_schedule`.

### 4.4 Redis keyspace

| Key                                      | TTL                       | Used by                     |
|------------------------------------------|---------------------------|-----------------------------|
| `boeing:rate_limiter:tokens`             | persistent (Lua-managed)  | `BoeingRateLimiter`         |
| `boeing:rate_limiter:last_refill`        | persistent                | `BoeingRateLimiter`         |
| `dispatch_lock:{date}:{bucket}`          | 600s test / 3600s prod    | `dispatch_hourly`           |
| `dispatched_skus:{date}:{bucket}`        | 900s test / 7200s prod    | `dispatch_bucket` Layer 3   |
| `batch_lock:{md5(skus)}`                 | 300s                      | `process_boeing_batch`      |
| `deferred_sync_buckets:{date}`           | 86400s                    | conflict guard / catchup    |
| `deferred_catchup_lock:{date}`           | 300s                      | active vs passive catchup   |
| `sync_cycle_counter:{date}`              | 86400s                    | `cycle_tracker`             |
| `sync_cycle:{date}:{N}`                  | 86400s                    | dispatched-bucket set       |
| `sync_cycle:{date}:{N}:started_at`       | 86400s                    | cycle start time            |
| `sync_cycle:{date}:{N}:changes`          | 86400s                    | hash sku → reason           |
| Celery broker keys + result backend      | 3600s (`result_expires`)  | Celery internals            |

Visibility timeout: 3600s (`broker_transport_options`).

---

## 5. API surface

All routes are mounted twice for backward compatibility:
- `/api/v1/<path>` (preferred) via `v1_router` in `app/routes/__init__.py`.
- `/api/<path>` legacy aliases in `app/routes/legacy.py` (each calls the same
  handler function; tagged "legacy (deprecated)").

`/health` is mounted at the root with no prefix.

All routes (except `/health` and `/auth/logout`'s header-only path) require a
Cognito Bearer JWT via `Depends(get_current_user)`.

### 5.1 Auth (`/api/v1/auth`, tag `auth`)

| Method | Path           | Handler              | Notes                                       |
|--------|----------------|----------------------|---------------------------------------------|
| GET    | `/auth/me`     | `get_me`             | Returns `User` from token claims            |
| POST   | `/auth/logout` | `logout`             | Calls Cognito `GlobalSignOut` via boto3     |

### 5.2 Health (`/health`, tag `health`)

| Method | Path     | Handler        | Notes                |
|--------|----------|----------------|----------------------|
| GET    | `/health`| `health_check` | Returns `{"status":"healthy"}` |

### 5.3 Extraction (`/api/v1/extraction`, tag `extraction`)

| Method | Path                       | Handler             | Body / Query                                          | Notes |
|--------|----------------------------|---------------------|-------------------------------------------------------|-------|
| GET    | `/extraction/search`       | `extraction_search` | `?query=`                                             | One-off Boeing search → normalize → upsert staging |
| POST   | `/extraction/bulk-search`  | `bulk_search`       | `BulkSearchRequest` (`part_numbers` or `part_numbers_text`, `idempotency_key`) | Creates batch, queues `process_bulk_search.delay(...)`, returns `BulkOperationResponse` |

Limits: `MAX_BULK_SEARCH_SIZE` (default 50000), per-PN length ≤ 50.

### 5.4 Publishing (`/api/v1/publishing`, tag `publishing`)

| Method | Path                                | Handler              | Notes |
|--------|-------------------------------------|----------------------|-------|
| POST   | `/publishing/publish`               | `publish_product`    | If `batch_id` given, queues `pub_task.delay`; otherwise creates a 1-item batch and queues `publish_batch.delay` |
| POST   | `/publishing/bulk-publish`          | `bulk_publish`       | Re-uses an existing extract/normalize batch when `batch_id` given (mutates `batch_type → publish`, resets publish counters, preserves skipped from normalize stage) |
| PUT    | `/publishing/products/{shopify_id}` | `update_product`     | Synchronous Shopify product update via `PublishingService.update_product` |
| GET    | `/publishing/check`                 | `check_sku`          | `?sku=` → `{shopifyProductId|null}` via GraphQL `productVariants` |
| POST   | `/publishing/metafields/setup`      | `setup_metafields`   | One-shot create of all `METAFIELD_DEFINITIONS` |

### 5.5 Batches (`/api/v1/batches`, tag `batches`)

| Method | Path                | Handler                  | Notes |
|--------|---------------------|--------------------------|-------|
| GET    | `/batches`          | `list_batches`           | Paginated, optional `status` filter, scoped to `user_id` |
| GET    | `/batches/{id}`     | `get_batch_status`       | Sets HTTP 500 if status=failed, 207 if completed-with-failures |
| DELETE | `/batches/{id}`     | `cancel_batch_endpoint`  | Queues `cancel_batch_task.delay(id)` |

### 5.6 Products (`/api/v1/products`, tag `products`)

| Method | Path                                   | Handler                  | Notes |
|--------|----------------------------------------|--------------------------|-------|
| GET    | `/products/published`                  | `get_published_products` | `?limit=&offset=&search=` (search filters loaded `range(0,999)` then filters in-memory by SKU substring); has Cloudflare-worker-error retry loop |
| GET    | `/products/published/{product_id}`     | `get_published_product`  | Strict `user_id` ownership check |
| GET    | `/products/staging`                    | `get_staging_products`   | `?status=&batch_id=` |
| GET    | `/products/raw-data/{part_number}`     | `get_raw_boeing_data`    | Walks last 50 `boeing_raw_data` rows, finds the line item matching `part_number` (with/without `=K3` style suffix) |

### 5.7 Sync (`/api/v1/sync`, tag `sync`)

| Method | Path                                 | Handler                    |
|--------|--------------------------------------|----------------------------|
| GET    | `/sync/dashboard`                    | `get_sync_dashboard`       |
| GET    | `/sync/products`                     | `get_sync_products`        |
| GET    | `/sync/history`                      | `get_sync_history`         |
| GET    | `/sync/failures`                     | `get_failed_products`      |
| GET    | `/sync/hourly-stats`                 | `get_hourly_stats`         |
| GET    | `/sync/product/{sku}`                | `get_product_sync_status`  |
| POST   | `/sync/product/{sku}/reactivate`     | `reactivate_product`       |
| POST   | `/sync/trigger/{sku}`                | `trigger_immediate_sync`   |

`/sync/trigger/{sku}` queues `sync_single_product_immediate.delay(sku, user_id)`,
which in turn delegates `process_boeing_batch` with `source_hour=-2`.

### 5.8 Search (`/api/v1/search`, tag `search`)

| Method | Path                  | Handler              |
|--------|-----------------------|----------------------|
| POST   | `/search/multi-part`  | `multi_part_search`  |

Body `MultiPartSearchRequest{ part_numbers: 1..50 }`. Response includes
found products + `not_found_skus` + summary. Uses Shopify Admin GraphQL
`productVariants(first, query: "sku:\"X\" OR sku:\"Y\"...")` in batches of 25
with 100ms delay between batches.

### 5.9 Reports (`/api/v1/reports`, tag `reports`)

| Method | Path                       | Handler                      |
|--------|----------------------------|------------------------------|
| POST   | `/reports/generate`        | `generate_report`            |
| GET    | `/reports/latest`          | `get_latest_report`          |
| GET    | `/reports/cycle-progress`  | `get_cycle_progress_endpoint`|

### 5.10 CORS

`apply_cors(app)` in `core/middleware.py` sets:
`allow_origins=["*"]`, `allow_credentials=True`, `allow_methods=["*"]`,
`allow_headers=["*"]`.

---

## 6. Integrations

Every wired-up integration, what it does, where, and which env vars it needs.

### 6.1 AWS Cognito (Aviation Gateway SSO + GlobalSignOut)

- **What**: JWT verification for all API routes; `GlobalSignOut` on logout.
- **Where**:
  - `core/cognito.py` — `get_jwks` (JWKS cache, 1h TTL), `verify_cognito_token`
    (RS256, asserts `iss`, `token_use=access`, optional `client_id` match).
  - `core/auth.py` — `get_current_user`, `get_optional_user`, `require_groups`,
    `require_admin`.
  - `services/auth_service.py` — `boto3.client("cognito-idp")`, calls
    `global_sign_out(AccessToken=...)`.
- **Env**: `COGNITO_REGION`, `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID`.
  Implicitly: `AWS_*` for boto3 credentials (host IAM role on EC2, presumably).

### 6.2 Boeing PNA API

- **What**: Two-step OAuth2 client_credentials → username/password gives back
  `x-part-access-token`; price/availability POST returns `lineItems[]`.
- **Where**:
  - `clients/boeing_client.py` — `_get_oauth_access_token`,
    `_get_part_access_token`, `fetch_price_availability`,
    `fetch_price_availability_batch` (batch up to 10 SKUs per call by
    convention).
  - `services/extraction_service.py` (one-off search),
    `services/boeing_fetch_service.py` (sync-time batch).
  - `tasks/extraction.py::extract_chunk`, `tasks/sync_boeing.py::process_boeing_batch`.
- **Env**: `BOEING_OAUTH_TOKEN_URL`, `BOEING_CLIENT_ID`, `BOEING_CLIENT_SECRET`,
  `BOEING_SCOPE`, `BOEING_PNA_OAUTH_URL`, `BOEING_PNA_PRICE_URL`,
  `BOEING_USERNAME`, `BOEING_PASSWORD`, `BOEING_BATCH_SIZE`,
  `BOEING_API_RATE_LIMIT`, `BOEING_RATE_LIMIT_CAPACITY`,
  `BOEING_RATE_LIMIT_REFILL`.
- **Rate limiting**: Global Redis token bucket (capacity 2, 2/min).
  Workers must `wait_for_token(timeout=120)` before each call.

### 6.3 Shopify Admin API (REST + GraphQL)

- **What**: Product create/update, location lookup, inventory levels per
  location, cost-per-item, metafield definitions, SKU search (GraphQL
  `productVariants`).
- **Where**:
  - `clients/shopify_client.py` — REST + GraphQL transport, domain normalisation.
  - `services/shopify_inventory_service.py` — locations, inventory set,
    inventory disconnect, cost, metafield definition setup.
  - `services/shopify_orchestrator.py` — product create/update glue,
    `update_product_pricing`, `update_inventory_by_location`, `delete_product`,
    `find_product_by_sku` (GraphQL primary, REST fallback limited to 50).
  - `services/search_service.py` — multi-SKU GraphQL search (independent of
    `ShopifyClient`; reads creds directly from settings).
  - `utils/shopify_payload_builder.py` — pure REST payload + metafields builder.
- **Env**: `SHOPIFY_STORE_DOMAIN`, `SHOPIFY_ADMIN_API_TOKEN`,
  `SHOPIFY_API_VERSION` (default `2024-10`), `SHOPIFY_LOCATION_MAP` (JSON
  `{boeingLocName: shopifyLocName}`),
  `SHOPIFY_INVENTORY_LOCATION_CODES` (JSON `{boeingLocName: 3-char-code}`),
  `SHOPIFY_DEFAULT_LOCATION_NAME`, `SHOPIFY_API_RATE_LIMIT`.

### 6.4 Supabase (Postgres + Storage + Realtime)

- **What**: Primary datastore. Storage bucket holds product images uploaded
  from Boeing image URLs.
- **Where**:
  - Backend: `clients/supabase_client.py` (service-role key) + every store in
    `db/`. `db/image_store.py` writes to bucket. Storage public URL pattern:
    `{SUPABASE_URL}/storage/v1/object/public/{bucket}/products/{pn}/{pn}.jpg`.
  - Frontend: `services/supabaseService.ts` and `services/realtimeService.ts`
    use `@supabase/supabase-js` with `VITE_SUPABASE_URL` +
    `VITE_SUPABASE_ANON_KEY` (anon key — **client-side direct DB writes to
    `product_staging`** are present in `saveNormalizedProduct`).
- **Env**: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (also aliased to
  `SUPABASE_KEY` in code), `SUPABASE_STORAGE_BUCKET` (default `product-images`),
  `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`.

### 6.5 Redis

- **What**: Celery broker + result backend, locks, dedup sets, token bucket,
  cycle tracker.
- **Where**: `celery_app/celery_config.py` (`broker=backend=REDIS_URL`),
  `utils/rate_limiter.py`, `utils/dispatch_lock.py`, `utils/cycle_tracker.py`.
- **Env**: `REDIS_URL` (default `redis://localhost:6379/0`).

### 6.6 Google Gemini

- **What**: Wired up but **not currently invoked**. `GeminiClient` is
  constructed in `container.py::get_gemini_client` and `get_report_service`
  signature does NOT pass it; `ReportService` does not import or use Gemini —
  reports are pure Python HTML builders. The dependency exists in
  `requirements.txt` (`google-generativeai>=0.8.0`) and env vars are read.
- **Env**: `GEMINI_API_KEY`, `GEMINI_MODEL` (default `gemini-2.0-flash`).
- See **Open questions** §10.

### 6.7 Resend

- **What**: Sends `cycle_start` and `cycle_complete` HTML emails.
- **Where**: `clients/resend_client.py`,
  `services/report_service.py` (skipped if no `RESEND_API_KEY` or no
  `REPORT_RECIPIENTS`).
- **Env**: `RESEND_API_KEY`, `RESEND_FROM_ADDRESS`
  (default `reports@skynetparts.com`),
  `REPORT_RECIPIENTS` (JSON list of emails, default `[]`).

### 6.8 Aviation Gateway (external SSO)

- **What**: Issues Cognito access tokens via redirect flow; provides a
  hidden iframe `/logout-listener` that broadcasts a `postMessage` of type
  `AVIATION_GATEWAY_LOGOUT` for federated SLO.
- **Where**: `frontend/src/contexts/AuthContext.tsx`. The backend never calls
  Aviation Gateway directly — it just verifies the JWT.
- **Env**: `VITE_AVIATION_GATEWAY_URL` (default `http://localhost:8080`),
  `AVIATION_GATEWAY_URL` (backend env, currently unread by Python code but
  present in `backend/.env.example`).

### 6.9 Frontend HTTP / data deps

- TanStack Query 5, React Router 6, react-hook-form, Zod, recharts,
  date-fns, Radix UI primitives, shadcn/ui, Tailwind, lucide-react.
- The frontend talks to **two backends**: the FastAPI server (REST under
  `/api/v1`) and Supabase directly (Realtime + select RPCs +
  `product_staging` writes).

---

## 7. Auth & security posture

### 7.1 Authentication flow

1. Browser opens the SPA. `AuthContext.initAuth` checks for an
   `access_token` URL fragment. If present, store in `sessionStorage`
   (`boeing_data_hub_sso_token`) and clear fragment.
2. If no token, redirect to
   `${VITE_AVIATION_GATEWAY_URL}/login?redirect=${current_url}`.
3. Aviation Gateway authenticates against Cognito and redirects back with
   `#access_token=...` fragment.
4. Token expiry: SPA decodes JWT (no signature check) and treats it as
   expired 30s before `exp`.
5. SPA mounts a hidden iframe `${gateway}/logout-listener` and listens for
   `postMessage{ type: "AVIATION_GATEWAY_LOGOUT" }` (origin-checked).

### 7.2 Authorization on the backend

- `get_current_user` (`core/auth.py`) validates the bearer token via
  `verify_cognito_token` and returns `{user_id, username, email, groups, scope}`.
  `user_id` is `sub` from the JWT.
- All routes except `/health` add `Depends(get_current_user)`. There are
  group-gated dependency factories `require_groups([...])` and `require_admin`,
  but no current route uses them.
- **Multi-tenancy** is enforced application-side by filtering every Supabase
  query on `user_id == current_user["user_id"]`. There is **no Postgres RLS**
  in any of the SQL files in `database/`.
- Logout (`POST /auth/logout`) calls Cognito `GlobalSignOut`, which revokes
  all refresh tokens for the user. The current access token remains valid
  until natural expiry (typically 1h).

### 7.3 Secrets & configuration

- All secrets (`SUPABASE_SERVICE_ROLE_KEY`, `SHOPIFY_ADMIN_API_TOKEN`,
  `BOEING_*`, `RESEND_API_KEY`, `GEMINI_API_KEY`) are loaded from
  `backend/.env` via `python-dotenv` and `pydantic.BaseModel` (`Settings`).
- `backend/.env` is `.gitignore`'d. `.env.example` documents required vars.
- Frontend exposes `VITE_*` vars at build time; only `VITE_API_BASE_URL` /
  `VITE_API_URL`, `VITE_AVIATION_GATEWAY_URL`, `VITE_SUPABASE_URL`,
  `VITE_SUPABASE_ANON_KEY` are referenced in code.

### 7.4 CORS

`allow_origins=["*"]` with `allow_credentials=True`, `allow_methods=["*"]`,
`allow_headers=["*"]`. Set in `core/middleware.py::apply_cors`.

### 7.5 Rate limiting

- Boeing API: global Redis token bucket (capacity 2, 2/min). Used by
  `extract_chunk`, `process_boeing_batch`, `update_shopify_product`
  *(only the Boeing-side tasks call `wait_for_token`; Shopify-side tasks
  rely on Celery `rate_limit="30/m"`)*.
- Celery `task_annotations` add per-task rate limits keyed on task name.

### 7.6 Content / payload sanitization

- Image upload (`db/image_store.py`) re-fetches the Boeing image URL
  server-side, follows redirects, sniffs `Content-Type`, falls back to
  placeholder if response is non-image or HTML.
- `validate_trace_url` in `utils/shopify_payload_builder.py` allow-lists
  trace URLs to `https://cdn.shopify.com/` and `https://www.getsmartcert.com/`.

---

## 8. Background jobs / webhooks / scheduled work

### 8.1 Celery Beat (scheduled)

(Schedule built dynamically from env in
`celery_app/celery_config.py::_build_beat_schedule`.)

| Beat entry              | Task                                       | Schedule                                                                 |
|-------------------------|--------------------------------------------|--------------------------------------------------------------------------|
| `dispatch-hourly-sync`  | `tasks.sync_dispatch.dispatch_hourly`      | `crontab(minute=SYNC_DISPATCH_MINUTE [, day_of_week=...])`               |
| `dispatch-retry-sync`   | `tasks.sync_dispatch.dispatch_retry`       | `crontab(minute=15, hour=*/SYNC_RETRY_HOURS [, day_of_week=...])`        |
| `end-of-day-cleanup`    | `tasks.sync_dispatch.end_of_day_cleanup`   | `crontab(minute=0, hour=SYNC_CLEANUP_HOUR [, day_of_week=...])`          |

Defaults: `SYNC_DISPATCH_MINUTE=*/10` in testing mode, `45` in production;
`SYNC_RETRY_HOURS=4`; `SYNC_CLEANUP_HOUR=0`. If `SYNC_FREQUENCY=weekly`,
all three are restricted to `SYNC_WEEKLY_DAY` (default Sunday).
If `SYNC_ENABLED=false`, the schedule is empty.

In testing mode, `dispatch_hourly` uses 10-minute buckets (`now.minute//10`)
and skips the `:45` window check; `MAX_BUCKETS = SYNC_TEST_BUCKET_COUNT`
(default 6). In production, `MAX_BUCKETS = 24`.

### 8.2 On-demand task chains

**Extraction → publish chain:**

```
process_bulk_search.delay(batch_id, pns, user_id)       # default queue
  → extract_chunk.delay(...) per chunk                  # extraction
  → reconcile_batch.apply_async(countdown=N)            # default
  → normalize_chunk.delay(...)                          # normalization
      → check_batch_completion.delay(batch_id)          # default
          (on terminal state, may queue dispatch_deferred_catchup)
```

**Publish chain:**

```
publish_batch.delay(batch_id, pns, user_id)             # publishing
  → publish_product.delay(... assigned_slot)            # publishing
  → reconcile_batch.apply_async(countdown=N)            # default
  → check_batch_completion.delay(batch_id)              # default
```

**Sync chain (per bucket):**

```
dispatch_hourly  (Beat)                                 # default
  → process_boeing_batch.delay(skus, user_id, hour)     # sync_boeing
      → update_shopify_product.delay(sku, ...)          # sync_shopify
          (no further chain)
```

**Cycle reporting:**

```
dispatch_hourly  (first bucket of cycle)
  → send_cycle_start_notification.delay()               # default
                                  (… buckets dispatch …)
dispatch_hourly  (last bucket; or dispatch_deferred_catchup)
  → wait_for_cycle_completion.delay(cycle_id)           # default
      polls product_sync_schedule.sync_status='syncing' every 30s × 60
      → generate_cycle_report.delay(cycle_id [, still_syncing])
          → ReportService.generate_cycle_report(...)  → email
```

### 8.3 Webhooks

**None.** No webhook receivers from Boeing, Shopify, Cognito, or Resend are
defined in this repo.

### 8.4 One-off operational scripts (`backend/scripts/`)

- `redeploy.sh` — stop/reload/start the six systemd units; curl-poll
  `localhost:8000/health` 5×.
- `start_workers.bat`, `start_workers.ps1`, `stop_workers.bat` — Windows dev
  helpers that launch separate worker processes per queue.
- `backfill_sync_schedules.py`, `create_products.py`, `script.py` — committed
  data-loading / migration helpers.
- `fix_locations.py`, `manual_sync.py`, `send_combined_report.py`,
  `reports/` — currently untracked in working tree.

---

## 9. Deployment & environment

### 9.1 Backend (EC2 + GitHub Actions)

`.github/workflows/deploy-backend.yml` triggers on push to `main` touching
`backend/**` (or manual `workflow_dispatch`):

1. SSH key from `secrets.EC2_SSH_KEY` written to `~/.ssh/deploy_key`.
2. `rsync -avz --delete` of `./backend/` → `ubuntu@98.84.57.169:/home/ubuntu/boeing-data-hub/backend/`
   (excludes `__pycache__`, `*.pyc`, `.env`, `venv`, `.git`,
   `celerybeat-schedule*`, `tests`, `logs.txt`, `nul`).
3. SSH into host, `git fetch origin main && git reset --hard origin/main`,
   `pip install -r requirements.txt --quiet`, `sudo bash scripts/redeploy.sh`.
4. Verify each of the six systemd services is `is-active`.

**Two EC2 hosts are referenced in docs**: workflow uses `98.84.57.169`,
`EC2_DEPLOYMENT_GUIDE.md` references `54.234.36.109` (api.boeing-data-hub.skynetparts.com)
with the older 2-celery-service layout (`boeing-celery` instead of the four
queue-specific services). See **Open questions** §10.

`scripts/redeploy.sh` stops/reloads/starts in the order:
backend → extract → publish → sync → default → beat (with `sleep 3` and
`sleep 5` between groups). Tail health-check curl on `:8000/health` (5
attempts × 3s).

Frontend is deployed to **AWS Amplify** (per workflow comment); no Amplify
config is in this repo.

### 9.2 Required environment variables (backend)

From `backend/.env.example` and consumed in `core/config.py`:

| Var                                    | Default                                                          | Used by |
|----------------------------------------|------------------------------------------------------------------|---------|
| `COGNITO_REGION`                       | `us-east-1`                                                      | Cognito JWKS / boto3 |
| `COGNITO_USER_POOL_ID`                 | —                                                                | JWT issuer |
| `COGNITO_APP_CLIENT_ID`                | —                                                                | optional client_id check |
| `AVIATION_GATEWAY_URL`                 | (in .env.example, unread by Python)                              | (frontend only) |
| `SUPABASE_URL`                         | —                                                                | All DB stores, image storage |
| `SUPABASE_SERVICE_ROLE_KEY`            | — (also aliased to `supabase_key`)                               | Service role auth |
| `SUPABASE_STORAGE_BUCKET`              | `product-images`                                                 | `image_store` |
| `SHOPIFY_STORE_DOMAIN`                 | —                                                                | `ShopifyClient`, `SearchService` |
| `SHOPIFY_ADMIN_API_TOKEN`              | —                                                                | All Shopify calls |
| `SHOPIFY_API_VERSION`                  | `2024-10`                                                        | Shopify URL |
| `SHOPIFY_LOCATION_MAP`                 | `{}`                                                             | normalization location-blocking + payload builder |
| `SHOPIFY_INVENTORY_LOCATION_CODES`     | `{}`                                                             | metafield 3-char location code |
| `SHOPIFY_DEFAULT_LOCATION_NAME`        | —                                                                | inventory disconnect on publish |
| `BOEING_OAUTH_TOKEN_URL`               | `https://api.developer.boeingservices.com/oauth2/v2.0/token`     | step 1 OAuth |
| `BOEING_CLIENT_ID`                     | —                                                                | OAuth |
| `BOEING_CLIENT_SECRET`                 | —                                                                | OAuth |
| `BOEING_SCOPE`                         | `api://helixapis.com/.default`                                   | OAuth |
| `BOEING_PNA_OAUTH_URL`                 | `.../boeing-part-price-availability/token/v1/oauth`              | step 2 part token |
| `BOEING_PNA_PRICE_URL`                 | `.../boeing-part-price-availability/price-availability/v1/wtoken`| price call |
| `BOEING_USERNAME` / `BOEING_PASSWORD`  | —                                                                | step 2 |
| `REDIS_URL`                            | `redis://localhost:6379/0`                                       | broker, locks, etc. |
| `BOEING_BATCH_SIZE`                    | `10`                                                             | extract chunking |
| `MAX_BULK_SEARCH_SIZE`                 | `50000`                                                          | request validation |
| `MAX_BULK_PUBLISH_SIZE`                | `10000`                                                          | request validation |
| `BOEING_API_RATE_LIMIT`                | `20/m`                                                           | Celery `rate_limit` for extract/sync_boeing |
| `SHOPIFY_API_RATE_LIMIT`               | `30/m`                                                           | Celery `rate_limit` for publish/sync_shopify |
| `BOEING_RATE_LIMIT_CAPACITY`           | `2`                                                              | global token bucket capacity |
| `BOEING_RATE_LIMIT_REFILL`             | `2` (per `60s`)                                                  | refill rate |
| `AUTO_START_CELERY`                    | `true`                                                           | spawn worker+beat from FastAPI lifespan |
| `SYNC_MODE`                            | `testing`                                                        | `production` or `testing` |
| `SYNC_TEST_BUCKET_COUNT`               | `6`                                                              | `MAX_BUCKETS` in testing |
| `SYNC_BATCH_SIZE`                      | `10`                                                             | `MAX_SKUS_PER_SLOT` (analytics + slot manager) |
| `SYNC_MAX_FAILURES`                    | `5`                                                              | deactivation threshold |
| `SYNC_DISPATCH_MINUTE`                 | `*/10` if testing else `45`                                      | beat |
| `SYNC_RETRY_HOURS`                     | `4`                                                              | beat |
| `SYNC_CLEANUP_HOUR`                    | `0`                                                              | beat |
| `SYNC_ENABLED`                         | `true`                                                           | master kill switch |
| `SYNC_FREQUENCY`                       | `daily`                                                          | `daily|weekly` |
| `SYNC_WEEKLY_DAY`                      | `Sunday`                                                         | `_resolve_weekly_day` |
| `GEMINI_API_KEY`                       | —                                                                | `GeminiClient` (constructed, currently unused) |
| `GEMINI_MODEL`                         | `gemini-2.0-flash`                                               | `GeminiClient` |
| `RESEND_API_KEY`                       | —                                                                | `ResendClient` |
| `RESEND_FROM_ADDRESS`                  | `reports@skynetparts.com`                                        | sender |
| `REPORT_RECIPIENTS`                    | `[]`                                                             | recipients (JSON list) |

### 9.3 Required environment variables (frontend)

From `frontend/.env.example` and `.env.local`:

- `VITE_API_URL` — base URL of FastAPI (e.g. `http://localhost:8000/api/v1`).
- `VITE_AVIATION_GATEWAY_URL` — SSO redirect target (default `http://localhost:8080`).
- `VITE_SUPABASE_URL` — used by `supabaseService.ts` and `realtimeService.ts`.
  Will throw at module import if missing.
- `VITE_SUPABASE_ANON_KEY` — same.
- `VITE_API_BASE_URL` — alternative base URL used by some service modules
  (`bulkService.ts`, `boeingService.ts`, `syncService.ts`). Falls back to
  `window.location.origin`.

### 9.4 Python runtime deps (backend/requirements.txt)

```
fastapi==0.115.0
uvicorn[standard]==0.30.0
httpx>=0.26.0
python-dotenv==1.0.1
python-jose[cryptography]>=3.3.0
supabase>=2.9.0
websockets>=13,<16
boto3>=1.28.0
celery[redis]==5.3.6
redis==5.0.1
google-generativeai>=0.8.0
resend>=2.0.0
```

Dev: pytest, pytest-asyncio, pytest-cov, pytest-mock, respx, fakeredis,
mypy, types-redis, ruff, black.

### 9.5 Database migration order

1. `migration_001_add_auth.sql`
2. `migration_002_add_unique_constraints.sql`
3. `migration_003_add_batch_id_to_staging.sql`
4. `migration_004_add_part_numbers_to_batches.sql`
5. `migration_005_sync_scheduler.sql` (v1; later superseded by 008)
6. `migration_006_sync_scheduler_v2.sql` *and/or* `migration_006_sync_tracking_columns.sql`
7. `migration_007_fix_progress_calculation.sql`
8. `migration_008_sync_scheduler_production.sql` (full v2 + RPCs + triggers)
9. `migration_009_pipeline_error_tracking.sql`
10. `migration_010_sync_reports.sql`
11. `migration_011_enhanced_sync_reports.sql`

For brand-new envs the consolidated `complete_db_schema.sql` plus
`migration_010_sync_reports.sql` + `migration_011_enhanced_sync_reports.sql`
covers everything; for already-deployed envs `production_migration.sql`
brings prod up to the consolidated schema.

---

## 10. Open questions

Items that the code alone does not resolve.

1. **Two EC2 hosts.** GitHub Actions deploys to `98.84.57.169` with six
   systemd services (boeing-backend + four queue-specific celery + beat).
   `EC2_DEPLOYMENT_GUIDE.md` documents `54.234.36.109` (api.boeing-data-hub.skynetparts.com)
   with two services (boeing-backend + boeing-celery). The systemd unit files
   themselves are not in this repo, so the live unit configuration on each
   host cannot be confirmed from source.

2. **`batch_type` enum drift.** `complete_db_schema.sql` constrains
   `batch_type` to `extract|normalize|publish`, while
   `production-db-schema.sql` (the prod snapshot) and migration 008 use
   `search|normalized|publishing|publish`. Code emits the new names. Whether
   prod has been migrated is not visible from the repo.

3. **`product_sync_schedule.last_locations` vs `last_location_summary`.**
   Migration 006 (`_sync_tracking_columns.sql`) adds `last_location_summary TEXT`
   and migration 008 declares `last_locations JSONB`. Production snapshot
   has `last_locations JSONB`. The route/schemas expose
   `last_location_summary`, while `sync_store.update_sync_success` writes
   `last_locations`. The on-disk shape in production is not derivable from
   code alone.

4. **Gemini integration.** `GeminiClient` is constructed by
   `container.get_gemini_client` but `ReportService.__init__` does not accept
   a Gemini argument and `report_service.py` does not import or call Gemini —
   reports are pure HTML built from sync data. Whether the LLM was
   intentionally removed or is meant to be re-attached is not stated in code
   or in-tree docs. (Cycle reports also reference a separate
   `send_combined_report.py` script in `backend/scripts/`, which is currently
   *untracked* in the working tree.)

5. **`AVIATION_GATEWAY_URL` (backend env).** Listed in `.env.example` but not
   read anywhere in the Python source — only the frontend uses
   `VITE_AVIATION_GATEWAY_URL`. Its purpose on the backend (if any) is unclear.

6. **Celery autostart in production.** `main.py::lifespan` will spawn a
   worker + beat as subprocesses if `AUTO_START_CELERY=true`, even though the
   production deployment uses dedicated systemd services. Code comments warn
   "set AUTO_START_CELERY=false" in production but the actual production env
   file value is not in the repo.

7. **`product_staging.id` vs uuid generation.** `BaseStore`-derived stores
   pass `str(uuid.uuid4())` for `id` even though `complete_db_schema.sql`
   leaves no default. Production schema is the same. The application is the
   sole source of `id` values; no DB default.

8. **No RLS.** None of the SQL files in `database/` enable row-level security
   on any table. Multi-tenancy is enforced exclusively in Python via
   `user_id = current_user["user_id"]` filters on every query. Whether RLS is
   enabled out-of-band on the live Supabase project is not visible here.

9. **Search filter scope (`/products/published`).** When `search` is given,
   the route fetches `range(0, 999)` (up to 1000 rows) and filters in-memory
   for SKU substring. Behaviour for users with >1000 products is implicit
   in the code but not documented.

10. **Frontend direct DB writes.** `frontend/src/services/supabaseService.ts`
    upserts into `product_staging` directly via the anon key. Whether this is
    intended or vestigial (the rest of the SPA goes through FastAPI) is not
    clear from in-repo docs.

11. **`failed_part_numbers` column.** Migration 006 adds it; migration 009
    drops it. The production snapshot still shows it. Whether prod has been
    migrated past 009 cannot be determined from the repo.

12. **Tests directory not summarized.** `backend/tests/` (`test_logout*.py`,
    `test_phase9_cleanup.py`, `test_sync_*.py`, plus `unit/`, `integration/`,
    `e2e/` subdirs) has been read for existence but the assertions inside
    were not catalogued in this pass.
