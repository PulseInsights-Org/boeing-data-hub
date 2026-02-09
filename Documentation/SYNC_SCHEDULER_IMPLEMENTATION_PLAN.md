# Boeing Product Sync Scheduler - Implementation Plan v2

## Executive Summary

This document outlines the implementation plan for a distributed, rate-limited scheduler that syncs product pricing and availability from Boeing API to Shopify. The system processes 1000+ products daily using bucket-based time slot distribution.

**Key Design Decisions (v2 - Post Review):**
- Stable SHA-256 hashing for slot assignment
- Bucket-based dispatch (hourly + catch-up) instead of every-minute polling
- Global Redis token bucket for rate limiting
- Anchored scheduling (no drift)
- PostgreSQL `FOR UPDATE SKIP LOCKED` for row locking

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Phase 1: Database Schema](#2-phase-1-database-schema)
3. [Phase 2: Core Infrastructure](#3-phase-2-core-infrastructure)
4. [Phase 3: Dispatcher Tasks](#4-phase-3-dispatcher-tasks)
5. [Phase 4: Sync Tasks](#5-phase-4-sync-tasks)
6. [Phase 5: API Endpoints](#6-phase-5-api-endpoints)
7. [Phase 6: Integration & Testing](#7-phase-6-integration--testing)
8. [Phase 7: Monitoring & Alerting](#8-phase-7-monitoring--alerting)
9. [Configuration Reference](#9-configuration-reference)
10. [File Structure](#10-file-structure)

---

## 1. Prerequisites

### 1.1 Environment Requirements

```
EXISTING (Already in place):
- FastAPI backend (backend/app/)
- Celery with Redis broker (backend/celery_app/)
- Supabase database
- Boeing API integration (backend/app/clients/boeing_client.py)
- Shopify API integration (backend/app/clients/shopify_client.py)

NEW REQUIREMENTS:
- Celery Beat (for scheduled task triggering)
- Redis (already have, will add token bucket keys)
- Direct PostgreSQL connection (for FOR UPDATE SKIP LOCKED)
```

### 1.2 Environment Variables (New)

Add to `.env`:

```bash
# Sync Scheduler Configuration
SYNC_BOEING_RATE_LIMIT_TOKENS=2          # Max tokens in bucket
SYNC_BOEING_RATE_LIMIT_REFILL=30         # Seconds per token refill
SYNC_CATCHUP_INTERVAL_MINUTES=15         # Catch-up dispatcher frequency
SYNC_CATCHUP_OVERDUE_THRESHOLD_MINUTES=30 # Products overdue after this
SYNC_MAX_CONSECUTIVE_FAILURES=5          # Deactivate after N failures
SYNC_BATCH_SIZE=10                       # SKUs per Boeing API call

# Direct PostgreSQL connection (for row locking)
# If using Supabase, construct from SUPABASE_URL or set separately
DATABASE_URL=postgresql://user:pass@host:5432/db
```

---

## 2. Phase 1: Database Schema

### 2.1 Migration File: `database/migration_005_add_sync_scheduler.sql`

```sql
-- ============================================================
-- Migration 005: Add Sync Scheduler Tables
-- ============================================================

-- 1. Create product_sync_schedule table
CREATE TABLE IF NOT EXISTS product_sync_schedule (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    sku TEXT NOT NULL,

    -- Bucket-Based Scheduling
    hour_bucket SMALLINT NOT NULL CHECK (hour_bucket BETWEEN 0 AND 23),
    minute_offset SMALLINT NOT NULL CHECK (minute_offset BETWEEN 0 AND 59),
    -- sync_slot is derived: hour_bucket * 60 + minute_offset

    -- Scheduling State
    next_sync_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_sync_at TIMESTAMP WITH TIME ZONE,
    last_sync_started_at TIMESTAMP WITH TIME ZONE,

    -- Sync Result Tracking
    last_sync_status TEXT CHECK (last_sync_status IN (
        'pending', 'success', 'no_change', 'updated', 'failed'
    )) DEFAULT 'pending',
    last_sync_error TEXT,
    consecutive_failures INTEGER DEFAULT 0,

    -- Change Detection
    last_boeing_hash TEXT,
    last_boeing_price NUMERIC,
    last_boeing_quantity INTEGER,
    last_boeing_in_stock BOOLEAN,

    -- Control Flags
    is_active BOOLEAN DEFAULT TRUE,
    sync_priority INTEGER DEFAULT 0,
    manual_slot_override BOOLEAN DEFAULT FALSE,

    -- Audit
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    CONSTRAINT product_sync_schedule_user_sku_unique UNIQUE (user_id, sku)
);

-- 2. Create indexes for bucket-based dispatch
CREATE INDEX IF NOT EXISTS idx_sync_schedule_bucket_dispatch
    ON product_sync_schedule (hour_bucket, next_sync_at, is_active)
    WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_sync_schedule_overdue
    ON product_sync_schedule (next_sync_at, is_active)
    WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_sync_schedule_user
    ON product_sync_schedule (user_id, is_active);

CREATE INDEX IF NOT EXISTS idx_sync_schedule_failures
    ON product_sync_schedule (consecutive_failures, is_active)
    WHERE consecutive_failures > 0 AND is_active = TRUE;

-- 3. Create sync_audit_log table
CREATE TABLE IF NOT EXISTS sync_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    sku TEXT NOT NULL,

    sync_timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'sync_started', 'sync_completed', 'sync_failed',
        'price_changed', 'quantity_changed', 'stock_status_changed',
        'shopify_updated', 'shopify_update_failed',
        'product_deactivated', 'dispatch_started'
    )),

    old_value JSONB,
    new_value JSONB,
    error_message TEXT,
    error_code TEXT,
    batch_id UUID,
    task_id TEXT,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sync_audit_log_time
    ON sync_audit_log (user_id, sync_timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_sync_audit_log_sku
    ON sync_audit_log (sku, sync_timestamp DESC);

-- 4. Create trigger for updated_at
CREATE OR REPLACE FUNCTION set_sync_schedule_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sync_schedule_updated_at
    BEFORE UPDATE ON product_sync_schedule
    FOR EACH ROW EXECUTE FUNCTION set_sync_schedule_updated_at();

-- 5. Create stored procedure for bucket dispatch (FOR UPDATE SKIP LOCKED)
CREATE OR REPLACE FUNCTION dispatch_sync_bucket(
    p_hour_bucket INTEGER,
    p_limit INTEGER DEFAULT 100
)
RETURNS TABLE (
    id UUID,
    user_id TEXT,
    sku TEXT,
    hour_bucket SMALLINT,
    minute_offset SMALLINT,
    last_boeing_hash TEXT,
    last_boeing_price NUMERIC,
    last_boeing_quantity INTEGER
) AS $$
BEGIN
    RETURN QUERY
    WITH locked_rows AS (
        SELECT pss.id
        FROM product_sync_schedule pss
        WHERE pss.hour_bucket = p_hour_bucket
          AND pss.next_sync_at <= NOW()
          AND pss.is_active = TRUE
        ORDER BY pss.sync_priority DESC, pss.next_sync_at ASC
        FOR UPDATE SKIP LOCKED
        LIMIT p_limit
    )
    UPDATE product_sync_schedule pss
    SET last_sync_started_at = NOW()
    FROM locked_rows lr
    WHERE pss.id = lr.id
    RETURNING
        pss.id, pss.user_id, pss.sku,
        pss.hour_bucket, pss.minute_offset,
        pss.last_boeing_hash, pss.last_boeing_price, pss.last_boeing_quantity;
END;
$$ LANGUAGE plpgsql;

-- 6. Create stored procedure for catch-up dispatch
CREATE OR REPLACE FUNCTION dispatch_sync_catchup(
    p_overdue_minutes INTEGER DEFAULT 30,
    p_stuck_minutes INTEGER DEFAULT 10,
    p_limit INTEGER DEFAULT 50
)
RETURNS TABLE (
    id UUID,
    user_id TEXT,
    sku TEXT,
    hour_bucket SMALLINT,
    minute_offset SMALLINT,
    last_boeing_hash TEXT,
    last_boeing_price NUMERIC,
    last_boeing_quantity INTEGER
) AS $$
BEGIN
    RETURN QUERY
    WITH locked_rows AS (
        SELECT pss.id
        FROM product_sync_schedule pss
        WHERE pss.next_sync_at < NOW() - (p_overdue_minutes || ' minutes')::INTERVAL
          AND pss.is_active = TRUE
          AND (
              pss.last_sync_started_at IS NULL
              OR pss.last_sync_started_at < NOW() - (p_stuck_minutes || ' minutes')::INTERVAL
          )
        ORDER BY pss.consecutive_failures ASC, pss.next_sync_at ASC
        FOR UPDATE SKIP LOCKED
        LIMIT p_limit
    )
    UPDATE product_sync_schedule pss
    SET last_sync_started_at = NOW()
    FROM locked_rows lr
    WHERE pss.id = lr.id
    RETURNING
        pss.id, pss.user_id, pss.sku,
        pss.hour_bucket, pss.minute_offset,
        pss.last_boeing_hash, pss.last_boeing_price, pss.last_boeing_quantity;
END;
$$ LANGUAGE plpgsql;

-- 7. Create function to calculate next sync time (anchored)
CREATE OR REPLACE FUNCTION calculate_next_sync_at(
    p_hour_bucket INTEGER,
    p_minute_offset INTEGER
)
RETURNS TIMESTAMP WITH TIME ZONE AS $$
DECLARE
    v_today DATE;
    v_slot_time_today TIMESTAMP WITH TIME ZONE;
BEGIN
    v_today := CURRENT_DATE;
    v_slot_time_today := v_today +
        MAKE_INTERVAL(hours => p_hour_bucket, mins => p_minute_offset);

    IF v_slot_time_today > NOW() THEN
        RETURN v_slot_time_today;
    ELSE
        RETURN v_slot_time_today + INTERVAL '1 day';
    END IF;
END;
$$ LANGUAGE plpgsql;
```

### 2.2 Rollback Migration: `database/migration_005_rollback.sql`

```sql
-- Rollback Migration 005
DROP FUNCTION IF EXISTS calculate_next_sync_at(INTEGER, INTEGER);
DROP FUNCTION IF EXISTS dispatch_sync_catchup(INTEGER, INTEGER, INTEGER);
DROP FUNCTION IF EXISTS dispatch_sync_bucket(INTEGER, INTEGER);
DROP TRIGGER IF EXISTS trg_sync_schedule_updated_at ON product_sync_schedule;
DROP FUNCTION IF EXISTS set_sync_schedule_updated_at();
DROP TABLE IF EXISTS sync_audit_log;
DROP TABLE IF EXISTS product_sync_schedule;
```

---

## 3. Phase 2: Core Infrastructure

### 3.1 File: `backend/app/core/sync_config.py`

**Purpose:** Centralized configuration for sync scheduler.

```python
# Contents:
# - SyncSettings dataclass with all sync-related config
# - Load from environment variables
# - Validation of settings
```

**Key Functions:**
- `get_sync_settings() -> SyncSettings`

### 3.2 File: `backend/app/utils/stable_hash.py`

**Purpose:** SHA-256 based stable hashing for slot assignment.

```python
# Contents:
# - calculate_sync_slot(sku: str) -> tuple[int, int, int]
#   Returns (hour_bucket, minute_offset, sync_slot)
# - Uses SHA-256, NOT Python's hash()
```

**Key Functions:**
- `calculate_sync_slot(sku: str) -> Tuple[int, int, int]`
- `calculate_next_sync_at(hour_bucket: int, minute_offset: int) -> datetime`

### 3.3 File: `backend/app/utils/rate_limiter.py`

**Purpose:** Redis-based global token bucket rate limiter.

```python
# Contents:
# - BoeingRateLimiter class
# - Lua script for atomic token acquisition
# - Wait-and-retry logic
```

**Key Classes:**
- `BoeingRateLimiter`
  - `__init__(redis_client, max_tokens=2, refill_seconds=30)`
  - `acquire(timeout_seconds=60) -> bool`
  - `get_status() -> dict`

### 3.4 File: `backend/app/db/sync_store.py`

**Purpose:** Database operations for sync scheduler (using direct psycopg2).

```python
# Contents:
# - SyncStore class with direct PostgreSQL connection
# - Uses stored procedures for FOR UPDATE SKIP LOCKED
```

**Key Classes:**
- `SyncStore`
  - `dispatch_bucket(hour_bucket: int, limit: int) -> List[SyncProduct]`
  - `dispatch_catchup(overdue_minutes: int, limit: int) -> List[SyncProduct]`
  - `update_sync_success(sku: str, user_id: str, new_hash: str, ...)`
  - `update_sync_failure(sku: str, user_id: str, error: str)`
  - `create_sync_schedule(sku: str, user_id: str, ...)`
  - `log_audit_event(event_type: str, sku: str, ...)`

---

## 4. Phase 3: Dispatcher Tasks

### 4.1 File: `backend/celery_app/tasks/sync_dispatch.py`

**Purpose:** Celery tasks for hourly and catch-up dispatch.

**Tasks:**

#### `dispatch_hourly_bucket`
- **Trigger:** Celery Beat, every hour at HH:00
- **Queue:** `sync_dispatch`
- **Logic:**
  1. Get current hour
  2. Call `sync_store.dispatch_bucket(hour)`
  3. Group products into batches of 10
  4. Queue `sync_batch_task` for each batch
  5. Log dispatch event

#### `dispatch_catchup`
- **Trigger:** Celery Beat, every 15 minutes
- **Queue:** `sync_dispatch`
- **Logic:**
  1. Call `sync_store.dispatch_catchup()`
  2. Group into batches
  3. Queue `sync_batch_task` for each batch
  4. Log recovery event

### 4.2 Update: `backend/celery_app/celery_config.py`

**Add Celery Beat schedule:**

```python
beat_schedule = {
    'hourly-bucket-dispatch': {
        'task': 'celery_app.tasks.sync_dispatch.dispatch_hourly_bucket',
        'schedule': crontab(minute=0, hour='*'),  # Every hour at :00
    },
    'catchup-dispatch': {
        'task': 'celery_app.tasks.sync_dispatch.dispatch_catchup',
        'schedule': crontab(minute='*/15'),  # Every 15 minutes
    },
}
```

**Add new queues:**

```python
task_queues = (
    # ... existing queues ...
    Queue('sync_dispatch', routing_key='sync.dispatch'),
    Queue('sync_boeing', routing_key='sync.boeing'),
    Queue('sync_shopify', routing_key='sync.shopify'),
)
```

---

## 5. Phase 4: Sync Tasks

### 5.1 File: `backend/celery_app/tasks/sync_boeing.py`

**Purpose:** Boeing API sync with rate limiting.

**Tasks:**

#### `sync_batch_task`
- **Queue:** `sync_boeing`
- **Rate Limit:** Global (via Redis token bucket, NOT Celery rate_limit)
- **Concurrency:** Worker with `-c 1` recommended
- **Logic:**
  1. Acquire token from rate limiter (wait up to 60s)
  2. Call Boeing API with batch of SKUs
  3. For each SKU:
     - Compute SHA-256 hash of relevant fields
     - Compare with stored hash
     - If changed: queue Shopify update
  4. Update sync schedule with results
  5. Log to audit table

### 5.2 File: `backend/celery_app/tasks/sync_shopify.py`

**Purpose:** Shopify updates for changed products.

**Tasks:**

#### `sync_shopify_update`
- **Queue:** `sync_shopify`
- **Rate Limit:** `30/m` (Celery native, per-worker is OK here)
- **Logic:**
  1. Get product from DB (need shopify_product_id)
  2. Determine what changed (price, quantity, or both)
  3. Update Shopify via API
  4. Update local DB
  5. Log to audit table

---

## 6. Phase 5: API Endpoints

### 6.1 File: `backend/app/routes/sync.py`

**New router for sync management.**

#### Endpoints:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/sync/status` | Overall sync health and statistics |
| GET | `/api/sync/products` | List products with sync status (paginated) |
| GET | `/api/sync/products/{sku}` | Get sync status for specific product |
| GET | `/api/sync/history/{sku}` | Get sync history for specific product |
| POST | `/api/sync/trigger` | Manually trigger sync for specific SKUs |
| POST | `/api/sync/pause` | Pause all sync operations |
| POST | `/api/sync/resume` | Resume sync operations |
| PUT | `/api/sync/products/{sku}/slot` | Override sync slot for a product |

### 6.2 File: `backend/app/schemas/sync.py`

**Pydantic models for sync endpoints.**

```python
# Models:
# - SyncStatusResponse
# - SyncProductResponse
# - SyncHistoryResponse
# - SyncTriggerRequest
# - SyncSlotOverrideRequest
```

### 6.3 Update: `backend/app/main.py`

**Register new router:**

```python
from app.routes.sync import router as sync_router
app.include_router(sync_router, prefix="/api/sync", tags=["sync"])
```

---

## 7. Phase 6: Integration & Testing

### 7.1 Integration with Existing Publish Flow

**Update:** `backend/celery_app/tasks/publishing.py`

After successful product publish, create sync schedule entry:

```python
# In publish_product task, after saving to product table:
sync_store.create_sync_schedule(
    sku=product['sku'],
    user_id=user_id,
    initial_hash=compute_hash(product),
    initial_price=product['list_price'],
    initial_quantity=product['inventory_quantity']
)
```

### 7.2 Backfill Script

**File:** `backend/scripts/backfill_sync_schedule.py`

For existing published products that don't have sync schedule entries:

```python
# Logic:
# 1. Query all products from `product` table
# 2. For each product without sync schedule entry:
#    - Calculate slot using stable hash
#    - Create sync schedule entry
#    - Set next_sync_at based on slot
```

### 7.3 Test Files

| File | Purpose |
|------|---------|
| `backend/tests/test_stable_hash.py` | Test SHA-256 slot assignment |
| `backend/tests/test_rate_limiter.py` | Test token bucket behavior |
| `backend/tests/test_sync_dispatch.py` | Test dispatcher tasks |
| `backend/tests/test_sync_boeing.py` | Test Boeing sync with mocks |
| `backend/tests/test_sync_shopify.py` | Test Shopify sync with mocks |
| `backend/tests/test_sync_api.py` | Test sync API endpoints |

---

## 8. Phase 7: Monitoring & Alerting

### 8.1 Metrics to Track

| Metric | Query | Alert Threshold |
|--------|-------|-----------------|
| Products synced/hour | COUNT from audit_log WHERE event_type='sync_completed' AND last 1 hour | < 30 (expected ~42) |
| Boeing API errors/hour | COUNT from audit_log WHERE event_type='sync_failed' | > 5% of attempts |
| Shopify update failures | COUNT WHERE event_type='shopify_update_failed' | > 3 consecutive |
| Overdue products | COUNT WHERE next_sync_at < NOW() - 1 hour | > 100 |
| Deactivated products | COUNT WHERE is_active=FALSE | Any increase |
| Rate limit waits | Avg wait time in rate limiter | > 30 seconds |

### 8.2 Dashboard Queries

```sql
-- Products synced in last 24 hours by status
SELECT
    last_sync_status,
    COUNT(*) as count
FROM product_sync_schedule
WHERE last_sync_at > NOW() - INTERVAL '24 hours'
GROUP BY last_sync_status;

-- Hourly bucket distribution
SELECT
    hour_bucket,
    COUNT(*) as products,
    COUNT(*) FILTER (WHERE last_sync_status = 'updated') as with_changes
FROM product_sync_schedule
WHERE is_active = TRUE
GROUP BY hour_bucket
ORDER BY hour_bucket;

-- Recent changes
SELECT
    sku,
    sync_timestamp,
    event_type,
    old_value->>'price' as old_price,
    new_value->>'price' as new_price
FROM sync_audit_log
WHERE event_type IN ('price_changed', 'quantity_changed')
ORDER BY sync_timestamp DESC
LIMIT 50;
```

---

## 9. Configuration Reference

### 9.1 Environment Variables (Complete)

```bash
# =====================================================
# SYNC SCHEDULER CONFIGURATION
# =====================================================

# Rate Limiting
SYNC_BOEING_RATE_LIMIT_TOKENS=2          # Max tokens in bucket
SYNC_BOEING_RATE_LIMIT_REFILL=30         # Seconds between token refills
SYNC_BOEING_BATCH_SIZE=10                # SKUs per Boeing API call

# Dispatcher Timing
SYNC_CATCHUP_INTERVAL_MINUTES=15         # Catch-up runs every N minutes
SYNC_CATCHUP_OVERDUE_THRESHOLD=30        # Products overdue after N minutes
SYNC_STUCK_THRESHOLD_MINUTES=10          # Consider stuck after N minutes

# Failure Handling
SYNC_MAX_CONSECUTIVE_FAILURES=5          # Deactivate after N failures
SYNC_BACKOFF_BASE_HOURS=2                # Base for exponential backoff
SYNC_BACKOFF_MAX_HOURS=24                # Maximum backoff

# Worker Configuration
SYNC_DISPATCH_CONCURRENCY=1              # Dispatcher worker concurrency
SYNC_BOEING_CONCURRENCY=1                # Boeing sync worker concurrency
SYNC_SHOPIFY_CONCURRENCY=4               # Shopify sync worker concurrency

# Database (direct connection for row locking)
DATABASE_URL=postgresql://user:pass@host:5432/db
```

### 9.2 Worker Startup Commands

```bash
# Dispatcher worker (singleton)
celery -A celery_app worker -Q sync_dispatch -c 1 --hostname=dispatch@%h

# Boeing sync worker (single concurrency for rate limiting)
celery -A celery_app worker -Q sync_boeing -c 1 --hostname=boeing_sync@%h

# Shopify sync worker (higher concurrency OK)
celery -A celery_app worker -Q sync_shopify -c 4 --hostname=shopify_sync@%h

# Celery Beat (scheduler - ONLY ONE INSTANCE)
celery -A celery_app beat --loglevel=info

# Combined (for development only)
celery -A celery_app worker -Q sync_dispatch,sync_boeing,sync_shopify -c 1 -B
```

---

## 10. File Structure

```
backend/
├── app/
│   ├── core/
│   │   ├── config.py              # (existing)
│   │   └── sync_config.py         # NEW: Sync-specific config
│   │
│   ├── db/
│   │   ├── supabase_store.py      # (existing)
│   │   ├── batch_store.py         # (existing)
│   │   └── sync_store.py          # NEW: Sync schedule operations
│   │
│   ├── routes/
│   │   ├── boeing.py              # (existing)
│   │   ├── shopify.py             # (existing)
│   │   └── sync.py                # NEW: Sync management endpoints
│   │
│   ├── schemas/
│   │   └── sync.py                # NEW: Sync Pydantic models
│   │
│   ├── utils/
│   │   ├── boeing_normalize.py    # (existing)
│   │   ├── stable_hash.py         # NEW: SHA-256 slot calculation
│   │   └── rate_limiter.py        # NEW: Redis token bucket
│   │
│   └── main.py                    # UPDATE: Add sync router
│
├── celery_app/
│   ├── celery_config.py           # UPDATE: Add beat schedule, queues
│   │
│   └── tasks/
│       ├── extraction.py          # (existing)
│       ├── normalization.py       # (existing)
│       ├── publishing.py          # UPDATE: Add sync schedule creation
│       ├── sync_dispatch.py       # NEW: Dispatcher tasks
│       ├── sync_boeing.py         # NEW: Boeing sync task
│       └── sync_shopify.py        # NEW: Shopify update task
│
├── scripts/
│   └── backfill_sync_schedule.py  # NEW: Backfill existing products
│
└── tests/
    ├── test_stable_hash.py        # NEW
    ├── test_rate_limiter.py       # NEW
    ├── test_sync_dispatch.py      # NEW
    ├── test_sync_boeing.py        # NEW
    ├── test_sync_shopify.py       # NEW
    └── test_sync_api.py           # NEW

database/
├── migration_001_add_auth.sql             # (existing)
├── migration_002_add_unique_constraints.sql # (existing)
├── migration_003_add_batch_id_to_staging.sql # (existing)
├── migration_004_add_part_numbers_to_batches.sql # (existing)
├── migration_005_add_sync_scheduler.sql   # NEW
└── migration_005_rollback.sql             # NEW

Documentation/
├── CELERY_REDIS_IMPLEMENTATION_PLAN.md    # (existing)
├── SYNC_SCHEDULER_IMPLEMENTATION_PLAN.md  # NEW (this file)
└── sync_scheduler_architecture.excalidraw.json # NEW (diagram)
```

---

## Implementation Order

### Sprint 1: Foundation (Database + Core Utilities)
1. Create and apply migration_005
2. Implement `stable_hash.py`
3. Implement `rate_limiter.py`
4. Implement `sync_store.py`
5. Write unit tests for above

### Sprint 2: Celery Tasks
1. Update `celery_config.py` with queues and beat schedule
2. Implement `sync_dispatch.py`
3. Implement `sync_boeing.py`
4. Implement `sync_shopify.py`
5. Write integration tests

### Sprint 3: API & Integration
1. Implement `sync.py` router and schemas
2. Update `publishing.py` to create sync schedule
3. Create backfill script
4. Run backfill for existing products
5. End-to-end testing

### Sprint 4: Monitoring & Production
1. Set up dashboard queries
2. Configure alerting
3. Deploy to staging
4. Load testing with 1000 products
5. Deploy to production

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Supabase doesn't support FOR UPDATE | Use RPC with stored procedures (included in migration) |
| Token bucket race conditions | Lua script is atomic; tested under load |
| Products stuck in 'syncing' | Catch-up dispatcher with stuck threshold |
| Boeing API downtime | Exponential backoff + alerts |
| Shopify rate limits | Separate queue with Celery rate_limit |
| Clock skew between workers | Use UTC everywhere; anchored scheduling |
| Data inconsistency | Audit log for all changes; reconciliation queries |

---

## Success Criteria

- [ ] All 1000 products sync within 24-hour window
- [ ] Boeing API rate limit never exceeded (2 calls/min)
- [ ] No duplicate syncs (FOR UPDATE SKIP LOCKED working)
- [ ] Sync slots don't drift over 7 days
- [ ] Changes detected and propagated to Shopify within 5 minutes
- [ ] Failed products recover within 24 hours
- [ ] Dashboard shows accurate real-time status
- [ ] Alerts fire within 5 minutes of threshold breach

---

## Appendix: Quick Reference

### Slot Calculation
```
hour_bucket = SHA256(sku)[:8] % 24
minute_offset = SHA256(sku + ":offset")[:8] % 60
```

### Rate Limit Math
```
2 tokens max, 1 refill per 30 seconds
= 2 calls immediate, then 1 every 30 seconds
= 2 + (28 × 2) = 58 calls per 30 minutes max
```

### Product Distribution
```
1000 products ÷ 24 hours = ~42 products/hour
42 products ÷ 10 per batch = ~5 batches/hour
5 batches × 30 sec spacing = 2.5 minutes of work/hour
```

### DB Query Frequency
```
Hourly dispatch: 24/day
Catch-up (every 15 min): 96/day
Total: 120 queries/day (vs 1,440 with every-minute polling)
```
