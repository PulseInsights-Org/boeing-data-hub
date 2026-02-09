# Boeing Sync Scheduler — Separate Scheduled + Retry Design

## Executive Summary

This document describes an alternative architecture for the Boeing Sync Scheduler that **guarantees equal distribution** by separating scheduled syncs from retry syncs into two independent processes.

**Core Principle:** Scheduled sync and retry sync are **completely independent processes** running on different schedules. This ensures scheduled sync ALWAYS processes exactly the same number of products per hour.

**Key Benefits:**
- Equal distribution GUARANTEED (not approximate)
- Failed products don't affect scheduled load
- Same-day retry capability (up to 6 retry windows)
- Simple mental model (two separate concerns)
- Scalable to 10,000+ products

---

## Part 1: The Problem with Unified Approaches

### Why Other Approaches Break Equal Distribution

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  THE PROBLEM WITH UNIFIED next_sync_at (Part 7B)                                │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  APPROACH:                                                                      │
│  Query all products WHERE next_sync_at <= NOW()                                 │
│                                                                                 │
│  ISSUE:                                                                         │
│  • Scheduled products: next_sync_at = their hour_bucket time                    │
│  • Failed products: next_sync_at = NOW + backoff (hours later)                  │
│  • At any given hour, you pick up BOTH scheduled AND retrying products          │
│                                                                                 │
│  RESULT:                                                                        │
│  Hour 10: 208 scheduled + 15 retries = 223 products                             │
│  Hour 11: 208 scheduled + 8 retries = 216 products                              │
│  Hour 12: 208 scheduled + 22 retries = 230 products                             │
│                                                                                 │
│  Distribution is UNEQUAL! Load varies hour to hour.                             │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  THE PROBLEM WITH INLINE RETRY (Part 7C)                                        │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  APPROACH:                                                                      │
│  Process slot's products, retry failures within same hour                       │
│                                                                                 │
│  ADVANTAGES:                                                                    │
│  ✓ Equal distribution preserved                                                 │
│  ✓ No slot collision                                                            │
│                                                                                 │
│  TRADE-OFFS:                                                                    │
│  • Products that fail 3 times must wait until NEXT DAY                          │
│  • No inter-day retry windows                                                   │
│  • All retry logic within single hour window                                    │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 2: The Solution — Separate Concerns

### Core Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  SEPARATE SCHEDULED SYNC FROM RETRY SYNC                                        │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  TWO INDEPENDENT PROCESSES:                                                     │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │  1. SCHEDULED SYNC (Equal Distribution Guaranteed)                      │   │
│  │  ───────────────────────────────────────────────────                    │   │
│  │  • Runs every hour at :00                                               │   │
│  │  • 24 hourly slots (hour_bucket 0-23)                                   │   │
│  │  • ~208 products per slot (5,000 / 24)                                  │   │
│  │  • Each product syncs at its slot time ONCE per day                     │   │
│  │  • Query: WHERE hour_bucket = X AND last_successful_sync < TODAY        │   │
│  │  • Load: ALWAYS ~208 products per hour                                  │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │  2. RETRY SYNC (Separate, Capped)                                       │   │
│  │  ───────────────────────────────────────                                │   │
│  │  • Runs every 4 hours at :30 (avoids collision with scheduled)          │   │
│  │  • Processes ONLY failed products (from ANY slot)                       │   │
│  │  • Query: WHERE sync_status = 'failed' AND is_active = TRUE             │   │
│  │  • LIMIT: Max 50 products per retry run                                 │   │
│  │  • If more than 50 failed, spread across multiple retry windows         │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│  COMBINED LOAD PER HOUR:                                                        │
│  ───────────────────────                                                        │
│  • Scheduled: ~208 products (always)                                            │
│  • Retry: 0-50 products (only in retry windows)                                 │
│  • Maximum: 208 + 50 = 258 products                                             │
│  • Minimum: 208 products                                                        │
│                                                                                 │
│  EQUAL DISTRIBUTION: Scheduled always equal, retry capped and separate          │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Why This Guarantees Equal Distribution

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  MATHEMATICAL PROOF OF EQUAL DISTRIBUTION                                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  KEY INSIGHT:                                                                   │
│  Scheduled sync query uses last_successful_sync (DATE), not last_sync_at.       │
│  Failed products have last_successful_sync = YESTERDAY (unchanged).             │
│  They are NOT re-picked by scheduled sync today.                                │
│                                                                                 │
│  SCHEDULED SYNC QUERY:                                                          │
│  ─────────────────────                                                          │
│  SELECT * FROM product_sync_schedule                                            │
│  WHERE hour_bucket = X                                                          │
│  AND last_successful_sync < CURRENT_DATE                                        │
│  AND is_active = TRUE                                                           │
│  AND sync_status != 'syncing'                                                   │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │  PROOF:                                                                 │   │
│  │                                                                         │   │
│  │  Slot 10 has 208 products assigned (permanent via hour_bucket).         │   │
│  │  Today = 2024-01-15                                                     │   │
│  │                                                                         │   │
│  │  At 10:00, query returns products where:                                │   │
│  │    hour_bucket = 10 AND last_successful_sync < '2024-01-15'             │   │
│  │                                                                         │   │
│  │  CASE 1: Product succeeded yesterday                                    │   │
│  │    last_successful_sync = '2024-01-14'                                  │   │
│  │    '2024-01-14' < '2024-01-15' = TRUE → PICKED UP ✓                     │   │
│  │                                                                         │   │
│  │  CASE 2: Product failed yesterday (never succeeded today)               │   │
│  │    last_successful_sync = '2024-01-13' (or earlier)                     │   │
│  │    '2024-01-13' < '2024-01-15' = TRUE → PICKED UP ✓                     │   │
│  │                                                                         │   │
│  │  CASE 3: Product already succeeded TODAY (shouldn't happen at 10:00)    │   │
│  │    last_successful_sync = '2024-01-15'                                  │   │
│  │    '2024-01-15' < '2024-01-15' = FALSE → NOT PICKED UP                  │   │
│  │                                                                         │   │
│  │  At start of day, ALL 208 products in slot 10 have                      │   │
│  │  last_successful_sync < TODAY → ALL 208 are picked up.                  │   │
│  │                                                                         │   │
│  │  ALWAYS 208. GUARANTEED.                                                │   │
│  │                                                                         │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│  FAILED PRODUCTS:                                                               │
│  ────────────────                                                               │
│  • Fail at 10:00 → sync_status = 'failed'                                       │
│  • last_successful_sync stays at yesterday (NOT updated on failure)             │
│  • Picked up by RETRY SYNC at 10:30, 14:30, 18:30, etc.                         │
│  • NOT re-picked by scheduled sync (same day)                                   │
│  • Next day at 10:00 → picked up again (last_successful_sync < new date)        │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 3: Database Schema

### Updated product_sync_schedule Table

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  product_sync_schedule TABLE SCHEMA                                             │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  -- Primary Key                                                                 │
│  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid()               │
│                                                                                 │
│  -- Product Identity                                                            │
│  user_id               TEXT NOT NULL                                            │
│  sku                   TEXT NOT NULL                                            │
│  UNIQUE(user_id, sku)                                                           │
│                                                                                 │
│  -- Slot Assignment (PERMANENT)                                                 │
│  hour_bucket           SMALLINT NOT NULL CHECK (hour_bucket BETWEEN 0 AND 23)   │
│                        -- Assigned once at publish, never changes               │
│                        -- Determines which hour product syncs                   │
│                                                                                 │
│  -- Sync Status Tracking                                                        │
│  sync_status           TEXT NOT NULL DEFAULT 'pending'                          │
│                        -- Values: 'pending', 'syncing', 'success', 'failed'     │
│                                                                                 │
│  last_sync_at          TIMESTAMPTZ                                              │
│                        -- Last sync ATTEMPT (regardless of result)              │
│                        -- Used for stuck detection                              │
│                                                                                 │
│  last_successful_sync  DATE                                                     │
│                        -- Last SUCCESSFUL sync DATE (not timestamp)             │
│                        -- KEY FIELD: Used by scheduled sync query               │
│                        -- Only updated on SUCCESS                               │
│                                                                                 │
│  -- Failure Tracking                                                            │
│  consecutive_failures  INTEGER NOT NULL DEFAULT 0                               │
│                        -- Reset to 0 on success                                 │
│                        -- Incremented on each failure                           │
│                        -- Product deactivated at 5                              │
│                                                                                 │
│  last_error            TEXT                                                     │
│                        -- Error message from last failure                       │
│                        -- Truncated to 500 chars                                │
│                                                                                 │
│  is_active             BOOLEAN NOT NULL DEFAULT TRUE                            │
│                        -- Set FALSE after 5 consecutive failures                │
│                        -- Requires manual intervention to reactivate            │
│                                                                                 │
│  -- Boeing Data Tracking                                                        │
│  last_boeing_hash      TEXT                                                     │
│                        -- MD5 hash of (price, quantity, in_stock)               │
│                        -- Used for change detection                             │
│                                                                                 │
│  last_price            NUMERIC                                                  │
│  last_quantity         INTEGER                                                  │
│                                                                                 │
│  -- Timestamps                                                                  │
│  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()                       │
│  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()                       │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Key Indexes

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  INDEXES FOR OPTIMAL QUERY PERFORMANCE                                          │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  -- Index for SCHEDULED SYNC query                                              │
│  CREATE INDEX idx_sync_schedule_hourly ON product_sync_schedule                 │
│    (hour_bucket, last_successful_sync)                                          │
│    WHERE is_active = TRUE AND sync_status != 'syncing';                         │
│                                                                                 │
│  -- Index for RETRY SYNC query                                                  │
│  CREATE INDEX idx_sync_schedule_failed ON product_sync_schedule                 │
│    (consecutive_failures, last_sync_at)                                         │
│    WHERE is_active = TRUE AND sync_status = 'failed';                           │
│                                                                                 │
│  -- Index for stuck detection                                                   │
│  CREATE INDEX idx_sync_schedule_stuck ON product_sync_schedule                  │
│    (last_sync_at)                                                               │
│    WHERE sync_status = 'syncing';                                               │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Schema Change from Original

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  SCHEMA MIGRATION                                                               │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  NEW COLUMN:                                                                    │
│  ───────────                                                                    │
│  last_successful_sync DATE                                                      │
│                                                                                 │
│  WHY DATE INSTEAD OF TIMESTAMP?                                                 │
│  ──────────────────────────────                                                 │
│  • We only care "did it sync successfully TODAY?"                               │
│  • DATE comparison is simpler: last_successful_sync < CURRENT_DATE              │
│  • Avoids timezone issues                                                       │
│  • Cleaner semantics                                                            │
│                                                                                 │
│  MIGRATION SQL:                                                                 │
│  ──────────────                                                                 │
│  ALTER TABLE product_sync_schedule                                              │
│  ADD COLUMN last_successful_sync DATE;                                          │
│                                                                                 │
│  -- Backfill existing data                                                      │
│  UPDATE product_sync_schedule                                                   │
│  SET last_successful_sync = DATE(last_sync_at)                                  │
│  WHERE sync_status = 'success';                                                 │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 4: Complete Data Flow

### Celery Beat Configuration

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  CELERY BEAT SCHEDULE                                                           │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  CELERYBEAT_SCHEDULE = {                                                        │
│                                                                                 │
│      # SCHEDULED SYNC - Every hour at :00                                       │
│      'dispatch-scheduled-sync': {                                               │
│          'task': 'app.tasks.sync.dispatch_scheduled_sync',                      │
│          'schedule': crontab(minute=0, hour='*'),                               │
│      },                                                                         │
│                                                                                 │
│      # RETRY SYNC - Every 4 hours at :30                                        │
│      'dispatch-retry-sync': {                                                   │
│          'task': 'app.tasks.sync.dispatch_retry_sync',                          │
│          'schedule': crontab(minute=30, hour='2,6,10,14,18,22'),                │
│      },                                                                         │
│                                                                                 │
│      # STUCK RESET - Every hour at :55                                          │
│      'reset-stuck-syncing': {                                                   │
│          'task': 'app.tasks.sync.reset_stuck_syncing',                          │
│          'schedule': crontab(minute=55, hour='*'),                              │
│      },                                                                         │
│                                                                                 │
│  }                                                                              │
│                                                                                 │
│  WHY :30 FOR RETRY?                                                             │
│  ──────────────────                                                             │
│  • Avoids collision with scheduled sync at :00                                  │
│  • Gives scheduled sync 30 minutes to complete                                  │
│  • Clear separation of concerns                                                 │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Scheduled Sync Flow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  SCHEDULED SYNC FLOW (EVERY HOUR AT :00)                                        │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│    10:00 ─────────────────────────────────────────────────────────────────      │
│      │                                                                          │
│      │   STEP 1: DETERMINE CURRENT HOUR BUCKET                                  │
│      │   current_hour = datetime.utcnow().hour  → 10                            │
│      │                                                                          │
│      ▼                                                                          │
│    ┌─────────────────────────────────────────────────────────────────────┐      │
│    │  STEP 2: QUERY SCHEDULED PRODUCTS                                   │      │
│    │                                                                     │      │
│    │  SELECT * FROM product_sync_schedule                                │      │
│    │  WHERE hour_bucket = 10                                             │      │
│    │  AND is_active = TRUE                                               │      │
│    │  AND sync_status != 'syncing'                                       │      │
│    │  AND (last_successful_sync IS NULL                                  │      │
│    │       OR last_successful_sync < CURRENT_DATE)                       │      │
│    │                                                                     │      │
│    │  RETURNS: ~208 products (exactly this slot's products)              │      │
│    └─────────────────────────────────────────────────────────────────────┘      │
│      │                                                                          │
│      ▼                                                                          │
│    ┌─────────────────────────────────────────────────────────────────────┐      │
│    │  STEP 3: LOCK PRODUCTS                                              │      │
│    │                                                                     │      │
│    │  UPDATE product_sync_schedule                                       │      │
│    │  SET sync_status = 'syncing', last_sync_at = NOW()                  │      │
│    │  WHERE id IN (selected_ids)                                         │      │
│    └─────────────────────────────────────────────────────────────────────┘      │
│      │                                                                          │
│      ▼                                                                          │
│    ┌─────────────────────────────────────────────────────────────────────┐      │
│    │  STEP 4: CREATE BATCHES & QUEUE TASKS                               │      │
│    │                                                                     │      │
│    │  • Group products into batches of 10 SKUs                           │      │
│    │  • Queue each batch as Celery task                                  │      │
│    │  • Celery rate_limit='2/m' ensures 2 calls/minute                   │      │
│    │  • 21 batches × 30 sec = 10.5 minutes                               │      │
│    └─────────────────────────────────────────────────────────────────────┘      │
│      │                                                                          │
│      ▼                                                                          │
│    ┌─────────────────────────────────────────────────────────────────────┐      │
│    │  STEP 5: PROCESS EACH BATCH                                         │      │
│    │                                                                     │      │
│    │  For each product in batch:                                         │      │
│    │                                                                     │      │
│    │  ┌─────────────────────┐      ┌─────────────────────┐              │      │
│    │  │     SUCCESS         │      │     FAILURE         │              │      │
│    │  ├─────────────────────┤      ├─────────────────────┤              │      │
│    │  │ sync_status =       │      │ sync_status =       │              │      │
│    │  │   'success'         │      │   'failed'          │              │      │
│    │  │                     │      │                     │              │      │
│    │  │ last_successful_sync│      │ last_successful_sync│              │      │
│    │  │   = CURRENT_DATE    │      │   = (unchanged)     │              │      │
│    │  │                     │      │                     │              │      │
│    │  │ consecutive_failures│      │ consecutive_failures│              │      │
│    │  │   = 0               │      │   += 1              │              │      │
│    │  │                     │      │                     │              │      │
│    │  │ Update hash, price, │      │ last_error =        │              │      │
│    │  │ quantity            │      │   error_message     │              │      │
│    │  └─────────────────────┘      └─────────────────────┘              │      │
│    │                                                                     │      │
│    └─────────────────────────────────────────────────────────────────────┘      │
│      │                                                                          │
│      ▼                                                                          │
│    10:11 ─── SCHEDULED SYNC COMPLETE ────────────────────────────────────       │
│                                                                                 │
│    RESULT:                                                                      │
│    • 180 products: sync_status='success', last_successful_sync=TODAY            │
│    • 28 products: sync_status='failed', last_successful_sync=YESTERDAY          │
│                                                                                 │
│    The 28 failed products will be picked up by RETRY SYNC at 10:30              │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Retry Sync Flow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  RETRY SYNC FLOW (EVERY 4 HOURS AT :30)                                         │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│    10:30 ─────────────────────────────────────────────────────────────────      │
│      │                                                                          │
│      │   NOTE: Runs 30 minutes after scheduled sync                             │
│      │   This gives scheduled sync time to complete                             │
│      │                                                                          │
│      ▼                                                                          │
│    ┌─────────────────────────────────────────────────────────────────────┐      │
│    │  STEP 1: QUERY FAILED PRODUCTS                                      │      │
│    │                                                                     │      │
│    │  SELECT * FROM product_sync_schedule                                │      │
│    │  WHERE sync_status = 'failed'                                       │      │
│    │  AND is_active = TRUE                                               │      │
│    │  ORDER BY consecutive_failures ASC,   ◄── Prioritize fewer failures │      │
│    │           last_sync_at ASC            ◄── Then oldest first         │      │
│    │  LIMIT 50                             ◄── Cap per retry window      │      │
│    │                                                                     │      │
│    │  RETURNS: 0-50 failed products (from ANY slot, not just one)        │      │
│    └─────────────────────────────────────────────────────────────────────┘      │
│      │                                                                          │
│      │ (Example: 50 products from slots 2, 5, 7, 10, 14, 19...)                  │
│      ▼                                                                          │
│    ┌─────────────────────────────────────────────────────────────────────┐      │
│    │  STEP 2: LOCK PRODUCTS                                              │      │
│    │                                                                     │      │
│    │  UPDATE product_sync_schedule                                       │      │
│    │  SET sync_status = 'syncing', last_sync_at = NOW()                  │      │
│    │  WHERE id IN (selected_ids)                                         │      │
│    └─────────────────────────────────────────────────────────────────────┘      │
│      │                                                                          │
│      ▼                                                                          │
│    ┌─────────────────────────────────────────────────────────────────────┐      │
│    │  STEP 3: CREATE BATCHES & QUEUE TASKS                               │      │
│    │                                                                     │      │
│    │  • Group products into batches of 10 SKUs                           │      │
│    │  • 50 products = 5 batches                                          │      │
│    │  • 5 batches × 30 sec = 2.5 minutes                                 │      │
│    └─────────────────────────────────────────────────────────────────────┘      │
│      │                                                                          │
│      ▼                                                                          │
│    ┌─────────────────────────────────────────────────────────────────────┐      │
│    │  STEP 4: PROCESS EACH BATCH                                         │      │
│    │                                                                     │      │
│    │  For each product in batch:                                         │      │
│    │                                                                     │      │
│    │  ┌─────────────────────┐      ┌─────────────────────┐              │      │
│    │  │  RETRY SUCCESS      │      │  RETRY FAILURE      │              │      │
│    │  ├─────────────────────┤      ├─────────────────────┤              │      │
│    │  │ sync_status =       │      │ sync_status =       │              │      │
│    │  │   'success'         │      │   'failed'          │              │      │
│    │  │                     │      │                     │              │      │
│    │  │ last_successful_sync│      │ consecutive_failures│              │      │
│    │  │   = CURRENT_DATE    │      │   += 1              │              │      │
│    │  │                     │      │                     │              │      │
│    │  │ consecutive_failures│      │ IF failures >= 5:   │              │      │
│    │  │   = 0 (reset)       │      │   is_active = FALSE │              │      │
│    │  │                     │      │   (deactivate)      │              │      │
│    │  │ RECOVERED!          │      │                     │              │      │
│    │  └─────────────────────┘      └─────────────────────┘              │      │
│    │                                                                     │      │
│    └─────────────────────────────────────────────────────────────────────┘      │
│      │                                                                          │
│      ▼                                                                          │
│    10:33 ─── RETRY SYNC COMPLETE ────────────────────────────────────────       │
│                                                                                 │
│    RESULT:                                                                      │
│    • 40 products recovered: sync_status='success'                               │
│    • 10 products still failing: sync_status='failed'                            │
│    • Products still failing will be retried at 14:30                            │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 5: Edge Case Walkthrough

### Scenario: 100 Products Fail in Slot 10

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  DETAILED SCENARIO: MASS FAILURE IN SINGLE SLOT                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  SETUP:                                                                         │
│  ───────                                                                        │
│  • Slot 10 has 208 products                                                     │
│  • Today = 2024-01-15                                                           │
│  • All products: last_successful_sync = 2024-01-14 (yesterday)                  │
│  • Boeing API has intermittent issues                                           │
│                                                                                 │
│  ═══════════════════════════════════════════════════════════════════════════    │
│                                                                                 │
│  10:00 - SCHEDULED SYNC FOR SLOT 10                                             │
│  ────────────────────────────────────                                           │
│  Query: hour_bucket=10 AND last_successful_sync < '2024-01-15'                  │
│  Returns: 208 products ✓                                                        │
│                                                                                 │
│  Processing:                                                                    │
│  • 108 SUCCEED:                                                                 │
│    - sync_status = 'success'                                                    │
│    - last_successful_sync = '2024-01-15' (updated to today)                     │
│  • 100 FAIL:                                                                    │
│    - sync_status = 'failed'                                                     │
│    - last_successful_sync = '2024-01-14' (unchanged - still yesterday)          │
│    - consecutive_failures = 1                                                   │
│                                                                                 │
│  ═══════════════════════════════════════════════════════════════════════════    │
│                                                                                 │
│  10:30 - RETRY SYNC (1st retry window)                                          │
│  ─────────────────────────────────────                                          │
│  Query: sync_status='failed' AND is_active=TRUE LIMIT 50                        │
│  Returns: 50 of the 100 failed products                                         │
│                                                                                 │
│  Processing:                                                                    │
│  • 40 SUCCEED:                                                                  │
│    - sync_status = 'success'                                                    │
│    - last_successful_sync = '2024-01-15' (updated)                              │
│    - consecutive_failures = 0 (reset)                                           │
│  • 10 FAIL:                                                                     │
│    - sync_status = 'failed'                                                     │
│    - consecutive_failures = 2                                                   │
│                                                                                 │
│  Remaining failed: 50 (not picked) + 10 (failed again) = 60                     │
│                                                                                 │
│  ═══════════════════════════════════════════════════════════════════════════    │
│                                                                                 │
│  11:00 - SCHEDULED SYNC FOR SLOT 11 (NOT Slot 10!)                              │
│  ─────────────────────────────────────────────────────                          │
│  Query: hour_bucket=11 AND last_successful_sync < '2024-01-15'                  │
│  Returns: ~209 products (Slot 11's products)                                    │
│                                                                                 │
│  NOTE: Slot 10's failed products are NOT picked up here.                        │
│        Equal distribution preserved!                                            │
│                                                                                 │
│  ═══════════════════════════════════════════════════════════════════════════    │
│                                                                                 │
│  14:30 - RETRY SYNC (2nd retry window)                                          │
│  ─────────────────────────────────────                                          │
│  Query: sync_status='failed' LIMIT 50                                           │
│  Returns: 50 of the 60 remaining failed                                         │
│                                                                                 │
│  Processing:                                                                    │
│  • 45 SUCCEED (recovered)                                                       │
│  • 5 FAIL (consecutive_failures now 2 or 3)                                     │
│                                                                                 │
│  Remaining failed: 10 (not picked) + 5 (failed again) = 15                      │
│                                                                                 │
│  ═══════════════════════════════════════════════════════════════════════════    │
│                                                                                 │
│  18:30 - RETRY SYNC (3rd retry window)                                          │
│  ─────────────────────────────────────                                          │
│  Query: sync_status='failed' LIMIT 50                                           │
│  Returns: 15 remaining failed                                                   │
│                                                                                 │
│  Processing:                                                                    │
│  • 12 SUCCEED (recovered)                                                       │
│  • 3 FAIL (consecutive_failures now 3-4)                                        │
│                                                                                 │
│  ═══════════════════════════════════════════════════════════════════════════    │
│                                                                                 │
│  22:30 - RETRY SYNC (4th retry window)                                          │
│  ─────────────────────────────────────                                          │
│  Query: sync_status='failed' LIMIT 50                                           │
│  Returns: 3 remaining failed                                                    │
│                                                                                 │
│  Processing:                                                                    │
│  • 2 SUCCEED (recovered)                                                        │
│  • 1 FAIL (consecutive_failures = 5 → DEACTIVATED)                              │
│                                                                                 │
│  ═══════════════════════════════════════════════════════════════════════════    │
│                                                                                 │
│  END OF DAY SUMMARY:                                                            │
│  ───────────────────                                                            │
│  • Started with 100 failed                                                      │
│  • 99 recovered via retry windows                                               │
│  • 1 deactivated (needs manual review)                                          │
│  • Scheduled sync for other slots was NEVER affected                            │
│                                                                                 │
│  ═══════════════════════════════════════════════════════════════════════════    │
│                                                                                 │
│  NEXT DAY (2024-01-16) 10:00 - SCHEDULED SYNC FOR SLOT 10                       │
│  ────────────────────────────────────────────────────────────                   │
│  Query: hour_bucket=10 AND last_successful_sync < '2024-01-16'                  │
│                                                                                 │
│  Products synced yesterday: last_successful_sync = '2024-01-15' < '2024-01-16'  │
│  → PICKED UP ✓                                                                  │
│                                                                                 │
│  Returns: 207 products (208 minus 1 deactivated)                                │
│                                                                                 │
│  Distribution remains ~208 per slot!                                            │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 6: Load Distribution Analysis

### Hourly Load Breakdown

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  LOAD DISTRIBUTION WITH SEPARATE SCHEDULED + RETRY                              │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  SCHEDULED SYNC (runs every hour at :00):                                       │
│  ─────────────────────────────────────────                                      │
│  Hour 0:  ~208 products                                                         │
│  Hour 1:  ~208 products                                                         │
│  Hour 2:  ~208 products                                                         │
│  ...                                                                            │
│  Hour 23: ~208 products                                                         │
│                                                                                 │
│  ALWAYS EQUAL!                                                                  │
│                                                                                 │
│  RETRY SYNC (runs every 4 hours at :30):                                        │
│  ──────────────────────────────────────                                         │
│  02:30: 0-50 failed products                                                    │
│  06:30: 0-50 failed products                                                    │
│  10:30: 0-50 failed products                                                    │
│  14:30: 0-50 failed products                                                    │
│  18:30: 0-50 failed products                                                    │
│  22:30: 0-50 failed products                                                    │
│                                                                                 │
│  VARIABLE but CAPPED at 50!                                                     │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### API Call Budget

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  API CALL ANALYSIS                                                              │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  SCHEDULED SYNC:                                                                │
│  ───────────────                                                                │
│  208 products ÷ 10 per batch = 21 API calls per hour                            │
│  21 calls ÷ 2 per minute = 10.5 minutes                                         │
│                                                                                 │
│  RETRY SYNC:                                                                    │
│  ────────────                                                                   │
│  50 products ÷ 10 per batch = 5 API calls per retry window                      │
│  5 calls ÷ 2 per minute = 2.5 minutes                                           │
│                                                                                 │
│  COMBINED (worst case - retry window hour):                                     │
│  ──────────────────────────────────────────                                     │
│  21 + 5 = 26 API calls = 13 minutes                                             │
│                                                                                 │
│  AVAILABLE CAPACITY:                                                            │
│  ───────────────────                                                            │
│  2 calls/min × 60 min = 120 calls/hour available                                │
│  Using 26 calls = 21.7% utilization                                             │
│                                                                                 │
│  DAILY TOTALS:                                                                  │
│  ─────────────                                                                  │
│  Scheduled: 24 hours × 21 calls = 504 calls/day                                 │
│  Retry: 6 windows × 5 calls = 30 calls/day max                                  │
│  Total: 534 calls/day max                                                       │
│                                                                                 │
│  Daily limit: 2,880 calls (2/min × 60 × 24)                                     │
│  Utilization: 18.5%                                                             │
│                                                                                 │
│  PLENTY OF HEADROOM FOR SCALING!                                                │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 24-Hour Timing Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  24-HOUR TIMING DIAGRAM                                                         │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  TIME   │ SCHEDULED SYNC        │ RETRY SYNC          │ TOTAL PRODUCTS          │
│  ───────┼───────────────────────┼─────────────────────┼─────────────────────────│
│  00:00  │ Slot 0:  ~208         │                     │ 208                     │
│  01:00  │ Slot 1:  ~208         │                     │ 208                     │
│  02:00  │ Slot 2:  ~208         │                     │ 208                     │
│  02:30  │                       │ Retry: 0-50         │ 0-50                    │
│  03:00  │ Slot 3:  ~208         │                     │ 208                     │
│  04:00  │ Slot 4:  ~208         │                     │ 208                     │
│  05:00  │ Slot 5:  ~208         │                     │ 208                     │
│  06:00  │ Slot 6:  ~208         │                     │ 208                     │
│  06:30  │                       │ Retry: 0-50         │ 0-50                    │
│  07:00  │ Slot 7:  ~208         │                     │ 208                     │
│  08:00  │ Slot 8:  ~208         │                     │ 208                     │
│  09:00  │ Slot 9:  ~208         │                     │ 208                     │
│  10:00  │ Slot 10: ~208         │                     │ 208                     │
│  10:30  │                       │ Retry: 0-50         │ 0-50                    │
│  11:00  │ Slot 11: ~208         │                     │ 208                     │
│  12:00  │ Slot 12: ~208         │                     │ 208                     │
│  13:00  │ Slot 13: ~208         │                     │ 208                     │
│  14:00  │ Slot 14: ~208         │                     │ 208                     │
│  14:30  │                       │ Retry: 0-50         │ 0-50                    │
│  15:00  │ Slot 15: ~208         │                     │ 208                     │
│  16:00  │ Slot 16: ~208         │                     │ 208                     │
│  17:00  │ Slot 17: ~208         │                     │ 208                     │
│  18:00  │ Slot 18: ~208         │                     │ 208                     │
│  18:30  │                       │ Retry: 0-50         │ 0-50                    │
│  19:00  │ Slot 19: ~208         │                     │ 208                     │
│  20:00  │ Slot 20: ~208         │                     │ 208                     │
│  21:00  │ Slot 21: ~208         │                     │ 208                     │
│  22:00  │ Slot 22: ~208         │                     │ 208                     │
│  22:30  │                       │ Retry: 0-50         │ 0-50                    │
│  23:00  │ Slot 23: ~208         │                     │ 208                     │
│  ───────┴───────────────────────┴─────────────────────┴─────────────────────────│
│                                                                                 │
│  DAILY TOTALS:                                                                  │
│  Scheduled: 24 × 208 = 4,992 products/day                                       │
│  Retry: 6 × 50 = 300 products/day max                                           │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 7: State Machine

### Product Lifecycle States

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  PRODUCT STATE MACHINE                                                          │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│                         ┌─────────────────┐                                     │
│                         │                 │                                     │
│                         │    PENDING      │ ◄── Initial state after publish     │
│                         │                 │                                     │
│                         └────────┬────────┘                                     │
│                                  │                                              │
│                                  │ Scheduled sync picks up                      │
│                                  ▼                                              │
│                         ┌─────────────────┐                                     │
│                         │                 │                                     │
│                         │    SYNCING      │ ◄── Being processed                 │
│                         │                 │                                     │
│                         └────────┬────────┘                                     │
│                                  │                                              │
│                    ┌─────────────┴─────────────┐                                │
│                    │                           │                                │
│                    ▼                           ▼                                │
│           ┌─────────────────┐         ┌─────────────────┐                       │
│           │                 │         │                 │                       │
│           │    SUCCESS      │         │     FAILED      │                       │
│           │                 │         │                 │                       │
│           └────────┬────────┘         └────────┬────────┘                       │
│                    │                           │                                │
│                    │                           │ Retry sync picks up            │
│                    │                           ▼                                │
│                    │                  ┌─────────────────┐                       │
│                    │                  │                 │                       │
│                    │                  │    SYNCING      │ ◄── Retry attempt     │
│                    │                  │                 │                       │
│                    │                  └────────┬────────┘                       │
│                    │                           │                                │
│                    │             ┌─────────────┴─────────────┐                  │
│                    │             │                           │                  │
│                    │             ▼                           ▼                  │
│                    │    ┌─────────────────┐         ┌─────────────────┐         │
│                    │    │                 │         │                 │         │
│                    └───▶│    SUCCESS      │         │     FAILED      │         │
│                         │  (recovered)    │         │  (retry again)  │         │
│                         └─────────────────┘         └────────┬────────┘         │
│                                                              │                  │
│                                                              │ If failures >= 5 │
│                                                              ▼                  │
│                                                     ┌─────────────────┐         │
│                                                     │                 │         │
│                                                     │   DEACTIVATED   │         │
│                                                     │  is_active=FALSE│         │
│                                                     │                 │         │
│                                                     └─────────────────┘         │
│                                                              │                  │
│                                                              │ Manual review    │
│                                                              │ & reactivation   │
│                                                              ▼                  │
│                                                     ┌─────────────────┐         │
│                                                     │                 │         │
│                                                     │    PENDING      │         │
│                                                     │                 │         │
│                                                     └─────────────────┘         │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### State Transitions

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STATE TRANSITIONS                                                              │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌────────────────┬──────────────────┬────────────────────────────────────────┐ │
│  │  FROM          │  TO              │  TRIGGER                               │ │
│  ├────────────────┼──────────────────┼────────────────────────────────────────┤ │
│  │  pending       │  syncing         │  Scheduled/Retry sync picks up         │ │
│  ├────────────────┼──────────────────┼────────────────────────────────────────┤ │
│  │  syncing       │  success         │  Boeing API returns data               │ │
│  ├────────────────┼──────────────────┼────────────────────────────────────────┤ │
│  │  syncing       │  failed          │  Boeing API error                      │ │
│  ├────────────────┼──────────────────┼────────────────────────────────────────┤ │
│  │  success       │  syncing         │  Next day's scheduled sync             │ │
│  ├────────────────┼──────────────────┼────────────────────────────────────────┤ │
│  │  failed        │  syncing         │  Retry sync picks up                   │ │
│  ├────────────────┼──────────────────┼────────────────────────────────────────┤ │
│  │  failed        │  (deactivated)   │  consecutive_failures >= 5             │ │
│  ├────────────────┼──────────────────┼────────────────────────────────────────┤ │
│  │  syncing       │  pending         │  Stuck reset (>30 min in syncing)      │ │
│  └────────────────┴──────────────────┴────────────────────────────────────────┘ │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 8: Configuration Parameters

### Recommended Settings

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  CONFIGURATION PARAMETERS                                                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────┬─────────────┬────────────────────────────────┐ │
│  │  PARAMETER                  │  VALUE      │  RATIONALE                     │ │
│  ├─────────────────────────────┼─────────────┼────────────────────────────────┤ │
│  │  Hour buckets               │  24         │  One per hour (0-23)           │ │
│  ├─────────────────────────────┼─────────────┼────────────────────────────────┤ │
│  │  Scheduled sync frequency   │  Hourly     │  Each slot syncs once/day      │ │
│  │                             │  at :00     │                                │ │
│  ├─────────────────────────────┼─────────────┼────────────────────────────────┤ │
│  │  Retry sync frequency       │  Every 4h   │  6 retry windows per day       │ │
│  │                             │  at :30     │                                │ │
│  ├─────────────────────────────┼─────────────┼────────────────────────────────┤ │
│  │  Retry limit per window     │  50         │  Caps retry load               │ │
│  ├─────────────────────────────┼─────────────┼────────────────────────────────┤ │
│  │  Max consecutive failures   │  5          │  Before deactivation           │ │
│  ├─────────────────────────────┼─────────────┼────────────────────────────────┤ │
│  │  Stuck threshold            │  30 min     │  Reset syncing → pending       │ │
│  ├─────────────────────────────┼─────────────┼────────────────────────────────┤ │
│  │  Boeing batch size          │  10 SKUs    │  API limit per request         │ │
│  ├─────────────────────────────┼─────────────┼────────────────────────────────┤ │
│  │  Celery rate limit          │  2/m        │  Boeing API limit              │ │
│  └─────────────────────────────┴─────────────┴────────────────────────────────┘ │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Scaling Adjustments

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  SCALING PARAMETERS                                                             │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────┬──────────────────────┬──────────────────────────────────┐  │
│  │  PRODUCT COUNT  │  PRODUCTS/HOUR       │  RECOMMENDED CHANGES             │  │
│  ├─────────────────┼──────────────────────┼──────────────────────────────────┤  │
│  │  1,000          │  42                  │  Default config                  │  │
│  ├─────────────────┼──────────────────────┼──────────────────────────────────┤  │
│  │  5,000          │  208                 │  Default config                  │  │
│  ├─────────────────┼──────────────────────┼──────────────────────────────────┤  │
│  │  10,000         │  417                 │  Increase retry limit to 100     │  │
│  ├─────────────────┼──────────────────────┼──────────────────────────────────┤  │
│  │  20,000         │  833                 │  Add more retry windows (every   │  │
│  │                 │                      │  2 hours), increase limit to 150 │  │
│  └─────────────────┴──────────────────────┴──────────────────────────────────┘  │
│                                                                                 │
│  MAXIMUM CAPACITY:                                                              │
│  ─────────────────                                                              │
│  Daily API calls available: 2,880                                               │
│  Maximum products: ~28,800 (theoretical, with full utilization)                 │
│  Practical limit: ~20,000 (leaving buffer for retries and errors)               │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 9: Comparison with Other Approaches

### Design Comparison Matrix

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  COMPARISON: THREE FAILURE HANDLING APPROACHES                                  │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌────────────────────┬──────────────────┬──────────────────┬────────────────┐  │
│  │  ASPECT            │  UNIFIED         │  INLINE RETRY    │  SEPARATE      │  │
│  │                    │  next_sync_at    │  (Part 7C)       │  SCHEDULED +   │  │
│  │                    │  (Part 7B)       │                  │  RETRY (this)  │  │
│  ├────────────────────┼──────────────────┼──────────────────┼────────────────┤  │
│  │  Equal             │  NO              │  YES             │  YES           │  │
│  │  distribution      │  (varies by      │  (each slot      │  (scheduled    │  │
│  │                    │  retry backlog)  │  handles own)    │  always equal) │  │
│  ├────────────────────┼──────────────────┼──────────────────┼────────────────┤  │
│  │  Same-day          │  YES             │  LIMITED         │  YES           │  │
│  │  retry             │  (via backoff)   │  (within hour    │  (6 windows/   │  │
│  │                    │                  │  only)           │  day)          │  │
│  ├────────────────────┼──────────────────┼──────────────────┼────────────────┤  │
│  │  Retry timing      │  Hours later     │  Minutes later   │  ~4 hours      │  │
│  │                    │  (backoff)       │  (same hour)     │  (next window) │  │
│  ├────────────────────┼──────────────────┼──────────────────┼────────────────┤  │
│  │  Complexity        │  Simple          │  Moderate        │  Moderate      │  │
│  │                    │  (one query)     │  (multi-pass)    │  (two          │  │
│  │                    │                  │                  │  processes)    │  │
│  ├────────────────────┼──────────────────┼──────────────────┼────────────────┤  │
│  │  Rate limiting     │  Complex         │  Simple          │  Simple        │  │
│  │                    │  (mixed load)    │  (per slot)      │  (separate)    │  │
│  ├────────────────────┼──────────────────┼──────────────────┼────────────────┤  │
│  │  Query strategy    │  Single global   │  Per hour_bucket │  Two separate  │  │
│  │                    │  next_sync_at    │                  │  queries       │  │
│  ├────────────────────┼──────────────────┼──────────────────┼────────────────┤  │
│  │  Best for          │  Simplicity      │  Fast recovery   │  Equal load +  │  │
│  │                    │  priority        │  + equal load    │  same-day      │  │
│  │                    │                  │                  │  retry         │  │
│  └────────────────────┴──────────────────┴──────────────────┴────────────────┘  │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### When to Choose This Approach

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  CHOOSE SEPARATE SCHEDULED + RETRY WHEN:                                        │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ✓ Equal distribution is a HARD requirement                                     │
│  ✓ You need same-day retry (not just next-day)                                  │
│  ✓ Failure rates could be high (external API instability)                       │
│  ✓ You want clear separation of concerns (easier debugging)                     │
│  ✓ You're scaling beyond 5,000 products                                         │
│                                                                                 │
│  CHOOSE INLINE RETRY (Part 7C) WHEN:                                            │
│  ────────────────────────────────────                                           │
│  ✓ You want fastest possible retry (within same hour)                           │
│  ✓ Failures are typically transient (recover quickly)                           │
│  ✓ You prefer simpler architecture (single process)                             │
│                                                                                 │
│  CHOOSE UNIFIED next_sync_at (Part 7B) WHEN:                                    │
│  ───────────────────────────────────────────                                    │
│  ✓ Equal distribution is nice-to-have, not critical                             │
│  ✓ You prioritize simplicity over precision                                     │
│  ✓ Product count is low and failures are rare                                   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 10: Summary

### Architecture at a Glance

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  SEPARATE SCHEDULED + RETRY: SUMMARY                                            │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────┬────────────────────────────────────────────────┐   │
│  │  Component              │  Description                                   │   │
│  ├─────────────────────────┼────────────────────────────────────────────────┤   │
│  │  Slot Assignment        │  24 slots (hour 0-23), ~208 products each      │   │
│  ├─────────────────────────┼────────────────────────────────────────────────┤   │
│  │  Scheduled Sync         │  Every hour at :00, processes slot's products  │   │
│  ├─────────────────────────┼────────────────────────────────────────────────┤   │
│  │  Scheduled Query        │  hour_bucket=X AND last_successful_sync<TODAY  │   │
│  ├─────────────────────────┼────────────────────────────────────────────────┤   │
│  │  Retry Sync             │  Every 4 hours at :30, processes failed        │   │
│  ├─────────────────────────┼────────────────────────────────────────────────┤   │
│  │  Retry Query            │  sync_status='failed' LIMIT 50                 │   │
│  ├─────────────────────────┼────────────────────────────────────────────────┤   │
│  │  Distribution           │  ALWAYS equal (208 scheduled, 0-50 retry)      │   │
│  ├─────────────────────────┼────────────────────────────────────────────────┤   │
│  │  Same-day Retry         │  Yes, up to 6 retry windows per day            │   │
│  ├─────────────────────────┼────────────────────────────────────────────────┤   │
│  │  Max Failures           │  5 consecutive before deactivation             │   │
│  └─────────────────────────┴────────────────────────────────────────────────┘   │
│                                                                                 │
│  THIS GUARANTEES:                                                               │
│  ─────────────────                                                              │
│  • Equal distribution in scheduled sync (ALWAYS ~208)                           │
│  • Same-day retry (up to 6 times)                                               │
│  • Failed products don't affect scheduled distribution                          │
│  • Capped retry load (max 50)                                                   │
│  • Scalable (works for 10K+ with minor adjustments)                             │
│  • Low maintenance (fully automated)                                            │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Document Version

- **Version:** 1.0
- **Date:** 2024
- **Author:** Boeing Data Hub Team
- **Status:** Design Review

---

*Based on patterns from AWS Builders Library, distributed job scheduler designs, and real-world production systems.*
