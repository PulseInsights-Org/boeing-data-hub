# Boeing Sync Scheduler — System Design Document

## Executive Summary

This document describes a **fully automated, low-maintenance daily sync system** that keeps 5,000 published Shopify products synchronized with Boeing's pricing and availability data.

**Core Problem:** You have products on Shopify. Boeing's prices and stock levels change. Your Shopify store must reflect these changes daily—automatically, without manual intervention.

**Constraints:**
- Boeing API: Maximum 10 part numbers per request
- Boeing API: 2 requests per minute rate limit
- Single user system (no multi-tenancy)
- Products added incrementally (100-500/week, not all at once)
- Target: 5,000 products with path to 10,000

---

## Part 1: Why This Design?

### The Ticketmaster Parallel

You mentioned Ticketmaster. Here's why that comparison is apt:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  TICKETMASTER                          YOUR SYSTEM                              │
├─────────────────────────────────────────────────────────────────────────────────┤
│  Thousands of users want same seat     Thousands of products need same API      │
│  Must prevent double-booking           Must prevent double-processing           │
│  External vendor has rate limits       Boeing has rate limits                   │
│  Must distribute load over time        Must distribute syncs over 24 hours      │
│  Eventual consistency is acceptable    Eventual consistency is acceptable       │
│  Must handle failures gracefully       Must handle failures gracefully          │
└─────────────────────────────────────────────────────────────────────────────────┘
```

Both systems solve the same fundamental problem: **coordinating many operations against a rate-limited external resource while preventing duplicate work**.

### Why Not Just Use a Simple Cron Job?

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  NAIVE APPROACH: "Just run everything at midnight"                              │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  5,000 products ÷ 10 per batch = 500 API calls                                  │
│  500 calls ÷ 2 per minute = 250 minutes = 4+ hours                              │
│                                                                                 │
│  PROBLEMS:                                                                      │
│  ❌ 4-hour processing window creates maintenance nightmares                     │
│  ❌ If cron job fails at 2am, you discover it at 9am                            │
│  ❌ No visibility into what's happening during those 4 hours                    │
│  ❌ Single point of failure                                                     │
│  ❌ No retry mechanism for individual failures                                  │
│  ❌ Difficult to add new products without restarting                            │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Why Distributed Hourly Processing?

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  OUR APPROACH: Distribute across 24 hours                                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  5,000 products ÷ 24 hours = ~208 products per hour                             │
│  208 products ÷ 10 per batch = 21 API calls per hour                            │
│  21 calls ÷ 2 per minute = ~11 minutes of work per hour                         │
│                                                                                 │
│  BENEFITS:                                                                      │
│  ✅ Only 11 minutes of processing, then 49 minutes of idle time                 │
│  ✅ Plenty of headroom for retries (could do 60 calls/hour, only need 21)       │
│  ✅ Failures are isolated to small batches                                      │
│  ✅ New products automatically slot into the schedule                           │
│  ✅ System self-heals between hours                                             │
│  ✅ Easy to monitor (check any hour for issues)                                 │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 2: System Architecture

### High-Level Components

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              SYSTEM ARCHITECTURE                                │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐   │
│  │   CELERY    │     │    REDIS    │     │   CELERY    │     │  EXTERNAL   │   │
│  │    BEAT     │────▶│   QUEUES    │────▶│   WORKER    │────▶│    APIs     │   │
│  │ (scheduler) │     │  (broker)   │     │ (executor)  │     │             │   │
│  └─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘   │
│        │                                       │                    │          │
│        │                                       ▼                    │          │
│        │                               ┌─────────────┐              │          │
│        └──────────────────────────────▶│  POSTGRES   │◀─────────────┘          │
│                                        │  (Supabase) │                         │
│                                        └─────────────┘                         │
│                                                                                 │
│  COMPONENT RESPONSIBILITIES:                                                    │
│  ─────────────────────────────────────────────────────────────────────────────  │
│  Celery Beat:   Clock that triggers hourly dispatches                           │
│  Redis:         Message broker holding queued tasks                             │
│  Celery Worker: Executes tasks (API calls, DB updates)                          │
│  Postgres:      State storage (schedules, locks, history)                       │
│  External APIs: Boeing (source of truth), Shopify (destination)                 │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Why Each Component?

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  WHY CELERY BEAT (instead of system cron)?                                      │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  System cron is external to your application:                                   │
│  - No visibility into app state                                                 │
│  - Separate deployment/configuration                                            │
│  - Different logging system                                                     │
│  - Hard to test                                                                 │
│                                                                                 │
│  Celery Beat is part of your application:                                       │
│  - Same deployment, same config, same logs                                      │
│  - Can dynamically adjust schedules                                             │
│  - Integrated with task system (retries, monitoring)                            │
│  - Testable as part of the application                                          │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│  WHY REDIS (instead of direct execution)?                                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Direct execution: "Run this function now"                                      │
│  - If process crashes, work is lost                                             │
│  - No backpressure (can overwhelm resources)                                    │
│  - No persistence                                                               │
│                                                                                 │
│  Redis queue: "Put this work in a queue"                                        │
│  - If worker crashes, task returns to queue (visibility timeout)                │
│  - Backpressure built-in (queue fills up, workers take at own pace)             │
│  - Persistence (tasks survive restarts)                                         │
│  - Rate limiting (workers only pull when ready)                                 │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│  WHY POSTGRES (instead of Redis-only)?                                          │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Redis is great for queues but:                                                 │
│  - Not designed for complex queries                                             │
│  - Memory-based (expensive at scale)                                            │
│  - No relational integrity                                                      │
│                                                                                 │
│  Postgres stores:                                                               │
│  - Product schedules (who syncs when)                                           │
│  - Lock states (who's processing what)                                          │
│  - Historical data (last prices, failure counts)                                │
│  - Queryable indexes for efficient hour-bucket lookups                          │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 2B: Detailed Architecture with Database Writes

### Complete System Architecture with Data Flows

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                        COMPLETE ARCHITECTURE WITH DATABASE WRITES                                 │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                  │
│                                    ┌─────────────────────┐                                       │
│                                    │     CELERY BEAT     │                                       │
│                                    │   (scheduler clock) │                                       │
│                                    └──────────┬──────────┘                                       │
│                                               │                                                  │
│                                               │ triggers at HH:00                                │
│                                               ▼                                                  │
│  ┌────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │                                   REDIS QUEUES                                             │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                   │  │
│  │  │   default    │  │ sync_boeing  │  │ sync_shopify │  │  publishing  │                   │  │
│  │  │    queue     │  │    queue     │  │    queue     │  │    queue     │                   │  │
│  │  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                   │  │
│  └─────────┼─────────────────┼─────────────────┼─────────────────┼───────────────────────────┘  │
│            │                 │                 │                 │                              │
│            ▼                 ▼                 ▼                 ▼                              │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────┐    │
│  │                              CELERY WORKERS                                             │    │
│  │                                                                                         │    │
│  │  ┌────────────────────┐  ┌────────────────────┐  ┌────────────────────┐                │    │
│  │  │ dispatch_hourly_   │  │  sync_boeing_      │  │  sync_shopify_     │                │    │
│  │  │ sync               │  │  batch             │  │  product           │                │    │
│  │  │ (concurrency=4)    │  │  (concurrency=1)   │  │  (concurrency=4)   │                │    │
│  │  └─────────┬──────────┘  └─────────┬──────────┘  └─────────┬──────────┘                │    │
│  │            │                       │                       │                           │    │
│  └────────────┼───────────────────────┼───────────────────────┼───────────────────────────┘    │
│               │                       │                       │                                │
│               │                       │                       │                                │
│  ┌────────────┼───────────────────────┼───────────────────────┼────────────────────────────┐   │
│  │            ▼                       ▼                       ▼                            │   │
│  │  ┌─────────────────────────────────────────────────────────────────────────────────┐   │   │
│  │  │                         SUPABASE (PostgreSQL)                                   │   │   │
│  │  │                                                                                 │   │   │
│  │  │  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐     │   │   │
│  │  │  │ product_sync_       │  │  product_staging    │  │     product         │     │   │   │
│  │  │  │ schedule            │  │                     │  │                     │     │   │   │
│  │  │  │ ─────────────────── │  │ ─────────────────── │  │ ─────────────────── │     │   │   │
│  │  │  │ • hour_bucket       │  │ • sku               │  │ • sku               │     │   │   │
│  │  │  │ • sync_status       │  │ • shopify_product_id│  │ • shopify_product_id│     │   │   │
│  │  │  │ • last_sync_at      │  │ • price             │  │ • price             │     │   │   │
│  │  │  │ • next_sync_at      │  │ • inventory_qty     │  │ • inventory_qty     │     │   │   │
│  │  │  │ • last_boeing_hash  │  │ • list_price        │  │                     │     │   │   │
│  │  │  │ • consecutive_      │  │ • net_price         │  │                     │     │   │   │
│  │  │  │   failures          │  │                     │  │                     │     │   │   │
│  │  │  │ • is_active         │  │                     │  │                     │     │   │   │
│  │  │  └─────────────────────┘  └─────────────────────┘  └─────────────────────┘     │   │   │
│  │  │                                                                                 │   │   │
│  │  └─────────────────────────────────────────────────────────────────────────────────┘   │   │
│  │                                        DATABASE                                        │   │
│  └────────────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                               │
│  ┌────────────────────────────────────────────────────────────────────────────────────────┐   │
│  │                              EXTERNAL APIs                                             │   │
│  │                                                                                        │   │
│  │  ┌─────────────────────────────────┐    ┌─────────────────────────────────┐           │   │
│  │  │         BOEING API              │    │         SHOPIFY API             │           │   │
│  │  │  ───────────────────────────    │    │  ───────────────────────────    │           │   │
│  │  │  • Rate limit: 2 calls/min      │    │  • Rate limit: ~40 calls/min    │           │   │
│  │  │  • Max 10 SKUs per call         │    │  • Update variant price         │           │   │
│  │  │  • Returns: price, qty, stock   │    │  • Update inventory level       │           │   │
│  │  └─────────────────────────────────┘    └─────────────────────────────────┘           │   │
│  │                                                                                        │   │
│  └────────────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                               │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Database Tables Overview

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  SUPABASE TABLES AND THEIR ROLES                                                │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  TABLE                    PURPOSE                      WRITTEN BY               │
│  ─────────────────────────────────────────────────────────────────────────────  │
│                                                                                 │
│  product_sync_schedule    Scheduler state machine      • dispatch_hourly_sync   │
│                           (locks, timing, history)     • sync_boeing_batch      │
│                                                        • publish_product        │
│                                                                                 │
│  product_staging          Intermediate product data    • publish_product        │
│                           (before/during publish)      • sync_shopify_product   │
│                                                                                 │
│  product                  Final published products     • publish_product        │
│                           (with Shopify IDs)           • sync_shopify_product   │
│                                                                                 │
│  boeing_raw_data          API response audit trail     • extraction tasks       │
│                           (not used by sync scheduler) (not sync-related)       │
│                                                                                 │
│  batches                  Batch job tracking           • batch orchestration    │
│                           (not used by sync scheduler) (not sync-related)       │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 2C: When Database Writes Happen

### Write Flow 1: Product Publishing (Entry Point)

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  WHEN: User calls POST /api/bulk-publish                                        │
│  TASK: publish_product                                                          │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  STEP 1: Read product_staging                                             │  │
│  │  ─────────────────────────────────────────────────────────────────────────│  │
│  │  Operation: SELECT                                                        │  │
│  │  Table: product_staging                                                   │  │
│  │  Query: WHERE sku = 'PN-001' AND user_id = 'user_001'                     │  │
│  │  Purpose: Get product data for Shopify publish                            │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                              │                                                  │
│                              ▼                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  STEP 2: Call Shopify API (CREATE product)                                │  │
│  │  ─────────────────────────────────────────────────────────────────────────│  │
│  │  API: POST /admin/api/products.json                                       │  │
│  │  Returns: shopify_product_id = 123456789                                  │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                              │                                                  │
│                              ▼                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  STEP 3: Write to product table                                           │  │
│  │  ─────────────────────────────────────────────────────────────────────────│  │
│  │  Operation: UPSERT                                                        │  │
│  │  Table: product                                                           │  │
│  │  Data: {                                                                  │  │
│  │    sku: 'PN-001',                                                         │  │
│  │    shopify_product_id: '123456789',                                       │  │
│  │    price: 165.00,                                                         │  │
│  │    inventory_quantity: 50,                                                │  │
│  │    ...all product fields                                                  │  │
│  │  }                                                                        │  │
│  │  Purpose: Store published product with Shopify ID                         │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                              │                                                  │
│                              ▼                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  STEP 4: Update product_staging with Shopify ID                           │  │
│  │  ─────────────────────────────────────────────────────────────────────────│  │
│  │  Operation: UPDATE                                                        │  │
│  │  Table: product_staging                                                   │  │
│  │  Data: { shopify_product_id: '123456789' }                                │  │
│  │  Where: sku = 'PN-001' AND user_id = 'user_001'                           │  │
│  │  Purpose: Mark staging record as published                                │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                              │                                                  │
│                              ▼                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  STEP 5: Create sync schedule (NEW - for scheduler)                       │  │
│  │  ─────────────────────────────────────────────────────────────────────────│  │
│  │  Operation: INSERT (UPSERT on conflict)                                   │  │
│  │  Table: product_sync_schedule                                             │  │
│  │  Data: {                                                                  │  │
│  │    sku: 'PN-001',                                                         │  │
│  │    user_id: 'user_001',                                                   │  │
│  │    hour_bucket: 9,              ◀── hash('PN-001') % 24                   │  │
│  │    next_sync_at: '2024-01-16 09:00:00',                                   │  │
│  │    last_sync_at: '2024-01-15 14:30:00',  ◀── Now (just published)         │  │
│  │    sync_status: 'success',                                                │  │
│  │    last_boeing_hash: 'a1b2c3d4...',                                       │  │
│  │    last_price: 150.00,                                                    │  │
│  │    last_quantity: 50,                                                     │  │
│  │    consecutive_failures: 0,                                               │  │
│  │    is_active: true                                                        │  │
│  │  }                                                                        │  │
│  │  Purpose: Register product for daily Boeing sync                          │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
│  TOTAL WRITES: 3 (product, product_staging, product_sync_schedule)              │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Write Flow 2: Hourly Dispatch

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  WHEN: Every hour at HH:00 (Celery Beat triggers)                               │
│  TASK: dispatch_hourly_sync                                                     │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  STEP 1: Reset stuck jobs (WRITE #1)                                      │  │
│  │  ─────────────────────────────────────────────────────────────────────────│  │
│  │  Operation: UPDATE                                                        │  │
│  │  Table: product_sync_schedule                                             │  │
│  │  Query:                                                                   │  │
│  │    UPDATE product_sync_schedule                                           │  │
│  │    SET sync_status = 'pending'                                            │  │
│  │    WHERE sync_status = 'syncing'                                          │  │
│  │      AND last_sync_at < NOW() - INTERVAL '30 minutes'                     │  │
│  │                                                                           │  │
│  │  Purpose: Recover from crashed workers                                    │  │
│  │  Affected rows: Usually 0-5 (rare stuck jobs)                             │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                              │                                                  │
│                              ▼                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  STEP 2: Query products for this hour (READ)                              │  │
│  │  ─────────────────────────────────────────────────────────────────────────│  │
│  │  Operation: SELECT                                                        │  │
│  │  Table: product_sync_schedule                                             │  │
│  │  Query:                                                                   │  │
│  │    SELECT * FROM product_sync_schedule                                    │  │
│  │    WHERE hour_bucket = 9         ◀── Current hour                         │  │
│  │      AND is_active = TRUE                                                 │  │
│  │      AND sync_status != 'syncing'                                         │  │
│  │    LIMIT 1000                                                             │  │
│  │                                                                           │  │
│  │  Purpose: Find all products scheduled for this hour                       │  │
│  │  Returns: ~208 products (at 5K scale)                                     │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                              │                                                  │
│                              ▼                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  STEP 3: Mark all as syncing (WRITE #2) - ACQUIRE LOCK                    │  │
│  │  ─────────────────────────────────────────────────────────────────────────│  │
│  │  Operation: UPDATE                                                        │  │
│  │  Table: product_sync_schedule                                             │  │
│  │  Query:                                                                   │  │
│  │    UPDATE product_sync_schedule                                           │  │
│  │    SET sync_status = 'syncing'                                            │  │
│  │    WHERE id IN (id1, id2, id3, ... id208)                                 │  │
│  │                                                                           │  │
│  │  Purpose: Lock products to prevent double-processing                      │  │
│  │  Affected rows: ~208 products                                             │  │
│  │                                                                           │  │
│  │  WHY THIS IS CRITICAL:                                                    │  │
│  │  ┌─────────────────────────────────────────────────────────────────────┐  │  │
│  │  │  Before: sync_status = 'success' (or 'pending' or 'failed')        │  │  │
│  │  │  After:  sync_status = 'syncing' (LOCKED)                          │  │  │
│  │  │                                                                     │  │  │
│  │  │  Any other dispatcher query will NOT return these rows             │  │  │
│  │  │  because of: WHERE sync_status != 'syncing'                        │  │  │
│  │  └─────────────────────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                              │                                                  │
│                              ▼                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  STEP 4: Queue batch tasks (NO DB WRITE)                                  │  │
│  │  ─────────────────────────────────────────────────────────────────────────│  │
│  │  Operation: Redis LPUSH                                                   │  │
│  │  Target: sync_boeing queue                                                │  │
│  │  Creates: 21 batch tasks (208 products ÷ 10 per batch)                    │  │
│  │                                                                           │  │
│  │  No database write - tasks go to Redis                                    │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
│  TOTAL WRITES: 2 (both to product_sync_schedule)                                │
│  - Reset stuck: ~0-5 rows                                                       │
│  - Acquire lock: ~208 rows                                                      │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Write Flow 3: Boeing Sync (Per Batch)

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  WHEN: Boeing worker pulls batch from queue                                     │
│  TASK: sync_boeing_batch (processes 10 SKUs)                                    │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  STEP 1: Call Boeing API (NO DB WRITE)                                    │  │
│  │  ─────────────────────────────────────────────────────────────────────────│  │
│  │  API: POST /price-availability                                            │  │
│  │  Body: { productCodes: ['PN-001', 'PN-047', ... 10 SKUs] }                │  │
│  │  Returns: { lineItems: [{ price, quantity, inStock }, ...] }              │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                              │                                                  │
│                              ▼                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  STEP 2: For each SKU - Read current hash (READ)                          │  │
│  │  ─────────────────────────────────────────────────────────────────────────│  │
│  │  Operation: SELECT                                                        │  │
│  │  Table: product_sync_schedule                                             │  │
│  │  Query: SELECT last_boeing_hash FROM product_sync_schedule                │  │
│  │         WHERE sku = 'PN-001' AND user_id = 'user_001'                     │  │
│  │  Purpose: Get stored hash for comparison                                  │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                              │                                                  │
│                              ▼                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  STEP 3: Compare hashes                                                   │  │
│  │  ─────────────────────────────────────────────────────────────────────────│  │
│  │                                                                           │  │
│  │  old_hash = "a1b2c3d4..."  (from DB)                                      │  │
│  │  new_hash = hash(price:150, qty:50, stock:true) = "a1b2c3d4..."           │  │
│  │                                                                           │  │
│  │  IF old_hash == new_hash:                                                 │  │
│  │     → No change, skip Shopify update                                      │  │
│  │  ELSE:                                                                    │  │
│  │     → Change detected! Queue Shopify update                               │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                              │                                                  │
│                      ┌───────┴────────┐                                         │
│                      ▼                ▼                                         │
│  ┌─────────────────────────┐  ┌──────────────────────────────────────────────┐  │
│  │  NO CHANGE PATH         │  │  CHANGE DETECTED PATH                        │  │
│  │  ───────────────────    │  │  ────────────────────────────────────────    │  │
│  │                         │  │                                              │  │
│  │  Skip Shopify update    │  │  Queue Shopify update task:                  │  │
│  │  (no DB write here)     │  │  sync_shopify_product.delay(                 │  │
│  │                         │  │    sku='PN-047',                             │  │
│  │                         │  │    new_price=200.00,                         │  │
│  │                         │  │    new_quantity=30                           │  │
│  │                         │  │  )                                           │  │
│  │                         │  │                                              │  │
│  │                         │  │  (Redis write, not DB)                       │  │
│  └─────────────────────────┘  └──────────────────────────────────────────────┘  │
│                      │                │                                         │
│                      └───────┬────────┘                                         │
│                              ▼                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  STEP 4: Update sync schedule - SUCCESS (WRITE)                           │  │
│  │  ─────────────────────────────────────────────────────────────────────────│  │
│  │  Operation: UPDATE                                                        │  │
│  │  Table: product_sync_schedule                                             │  │
│  │  Query:                                                                   │  │
│  │    UPDATE product_sync_schedule                                           │  │
│  │    SET                                                                    │  │
│  │      last_sync_at = NOW(),                                                │  │
│  │      next_sync_at = NOW() + INTERVAL '24 hours',                          │  │
│  │      sync_status = 'success',                                             │  │
│  │      last_boeing_hash = 'new_hash_value',                                 │  │
│  │      last_price = 150.00,                                                 │  │
│  │      last_quantity = 50,                                                  │  │
│  │      consecutive_failures = 0,                                            │  │
│  │      last_error = NULL                                                    │  │
│  │    WHERE sku = 'PN-001' AND user_id = 'user_001'                          │  │
│  │                                                                           │  │
│  │  Purpose: Record successful sync, schedule next sync, release lock        │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
│  ─── OR, IF BOEING API FAILED ───                                               │
│                                                                                 │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  STEP 4 (ALT): Update sync schedule - FAILURE (WRITE)                     │  │
│  │  ─────────────────────────────────────────────────────────────────────────│  │
│  │  Operation: UPDATE                                                        │  │
│  │  Table: product_sync_schedule                                             │  │
│  │  Query:                                                                   │  │
│  │    UPDATE product_sync_schedule                                           │  │
│  │    SET                                                                    │  │
│  │      last_sync_at = NOW(),                                                │  │
│  │      next_sync_at = NOW() + INTERVAL '2 hours',  ◀── Exponential backoff  │  │
│  │      sync_status = 'failed',                                              │  │
│  │      consecutive_failures = consecutive_failures + 1,                     │  │
│  │      last_error = 'Boeing API timeout',                                   │  │
│  │      is_active = CASE WHEN consecutive_failures >= 5                      │  │
│  │                       THEN FALSE ELSE TRUE END                            │  │
│  │    WHERE sku = 'PN-001' AND user_id = 'user_001'                          │  │
│  │                                                                           │  │
│  │  Purpose: Record failure, schedule retry with backoff, maybe deactivate   │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
│  TOTAL WRITES PER BATCH: 10 (one per SKU in batch)                              │
│  All writes to: product_sync_schedule                                           │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Write Flow 4: Shopify Update (Only When Changed)

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  WHEN: Change detected by sync_boeing_batch                                     │
│  TASK: sync_shopify_product                                                     │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  STEP 1: Get Shopify product ID (READ)                                    │  │
│  │  ─────────────────────────────────────────────────────────────────────────│  │
│  │  Operation: SELECT                                                        │  │
│  │  Table: product_staging                                                   │  │
│  │  Query:                                                                   │  │
│  │    SELECT shopify_product_id                                              │  │
│  │    FROM product_staging                                                   │  │
│  │    WHERE sku = 'PN-047' AND user_id = 'user_001'                          │  │
│  │                                                                           │  │
│  │  Returns: shopify_product_id = '123456789'                                │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                              │                                                  │
│                              ▼                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  STEP 2: Calculate new Shopify price                                      │  │
│  │  ─────────────────────────────────────────────────────────────────────────│  │
│  │  Boeing price: $200.00                                                    │  │
│  │  Markup: 10%                                                              │  │
│  │  Shopify price: $200.00 × 1.1 = $220.00                                   │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                              │                                                  │
│                              ▼                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  STEP 3: Update Shopify product (EXTERNAL API)                            │  │
│  │  ─────────────────────────────────────────────────────────────────────────│  │
│  │  API: PUT /admin/api/variants/{variant_id}.json                           │  │
│  │  Body: { variant: { price: "220.00" } }                                   │  │
│  │                                                                           │  │
│  │  API: POST /admin/api/inventory_levels/set.json                           │  │
│  │  Body: { inventory_item_id: X, available: 30 }                            │  │
│  │                                                                           │  │
│  │  Purpose: Update price and inventory in Shopify                           │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                              │                                                  │
│                              ▼                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  STEP 4: Update local database (WRITE #1)                                 │  │
│  │  ─────────────────────────────────────────────────────────────────────────│  │
│  │  Operation: UPDATE                                                        │  │
│  │  Table: product_staging                                                   │  │
│  │  Query:                                                                   │  │
│  │    UPDATE product_staging                                                 │  │
│  │    SET                                                                    │  │
│  │      list_price = 200.00,       ◀── Boeing's price                        │  │
│  │      price = 220.00,            ◀── Shopify's price (with markup)         │  │
│  │      inventory_quantity = 30                                              │  │
│  │    WHERE sku = 'PN-047' AND user_id = 'user_001'                          │  │
│  │                                                                           │  │
│  │  Purpose: Keep local data in sync with Shopify                            │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                              │                                                  │
│                              ▼                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  STEP 5: Update product table (WRITE #2) - Optional                       │  │
│  │  ─────────────────────────────────────────────────────────────────────────│  │
│  │  Operation: UPDATE                                                        │  │
│  │  Table: product                                                           │  │
│  │  Query:                                                                   │  │
│  │    UPDATE product                                                         │  │
│  │    SET                                                                    │  │
│  │      price = 220.00,                                                      │  │
│  │      inventory_quantity = 30                                              │  │
│  │    WHERE sku = 'PN-047' AND user_id = 'user_001'                          │  │
│  │                                                                           │  │
│  │  Purpose: Keep published product record current                           │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
│  TOTAL WRITES: 2 (product_staging, product)                                     │
│  NOTE: This only happens when data CHANGED (~5% of syncs)                       │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 2D: Complete Write Summary

### All Database Writes by Task

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  COMPLETE DATABASE WRITE MAP                                                    │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  TASK                        TABLE                    OPERATION   FREQUENCY     │
│  ─────────────────────────────────────────────────────────────────────────────  │
│                                                                                 │
│  publish_product             product                  UPSERT      On publish    │
│                              product_staging          UPDATE      On publish    │
│                              product_sync_schedule    INSERT      On publish    │
│                                                                                 │
│  dispatch_hourly_sync        product_sync_schedule    UPDATE      Every hour    │
│                              (reset stuck)            (~0-5 rows)               │
│                              product_sync_schedule    UPDATE      Every hour    │
│                              (acquire lock)           (~208 rows)               │
│                                                                                 │
│  sync_boeing_batch           product_sync_schedule    UPDATE      Per SKU       │
│                              (success or failure)     (10 per batch)            │
│                                                                                 │
│  sync_shopify_product        product_staging          UPDATE      On change     │
│                              product                  UPDATE      On change     │
│                                                       (~5% of syncs)            │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Daily Write Volume Estimate (at 5,000 products)

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  DAILY DATABASE WRITE VOLUME                                                    │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  SYNC SCHEDULER WRITES (per day):                                       │    │
│  │                                                                         │    │
│  │  dispatch_hourly_sync (24 runs × day):                                  │    │
│  │    • Reset stuck jobs:    24 × ~2 rows = ~48 writes                     │    │
│  │    • Acquire locks:       24 × 208 rows = 4,992 writes                  │    │
│  │                                                                         │    │
│  │  sync_boeing_batch (500 batches × day):                                 │    │
│  │    • Update sync status:  500 × 10 SKUs = 5,000 writes                  │    │
│  │                                                                         │    │
│  │  sync_shopify_product (~5% change rate):                                │    │
│  │    • product_staging:     250 × 1 = 250 writes                          │    │
│  │    • product:             250 × 1 = 250 writes                          │    │
│  │                                                                         │    │
│  │  ─────────────────────────────────────────────────────────────────────  │    │
│  │  DAILY TOTAL: ~10,540 writes                                            │    │
│  │                                                                         │    │
│  │  BREAKDOWN BY TABLE:                                                    │    │
│  │    • product_sync_schedule:  ~10,040 writes (95%)                       │    │
│  │    • product_staging:           ~250 writes (2.5%)                      │    │
│  │    • product:                   ~250 writes (2.5%)                      │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  WHY THIS IS EFFICIENT:                                                         │
│  • ~10K writes/day = ~7 writes/minute = negligible load                         │
│  • product_sync_schedule is optimized with indexes                              │
│  • Writes are distributed across 24 hours (not batched)                         │
│  • Supabase (Postgres) easily handles 1000+ writes/second                       │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Data Flow Sequence Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  COMPLETE DATA FLOW: ONE PRODUCT THROUGH ENTIRE SYNC CYCLE                      │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  TIME    COMPONENT              ACTION                TABLE / API               │
│  ─────────────────────────────────────────────────────────────────────────────  │
│                                                                                 │
│  ══════════════════════════════════════════════════════════════════════════════ │
│  PHASE 1: INITIAL PUBLISH (happens once per product)                            │
│  ══════════════════════════════════════════════════════════════════════════════ │
│                                                                                 │
│  14:30   User                   POST /api/bulk-publish                          │
│  14:30   publish_product        READ product_staging   ──▶ SELECT               │
│  14:30   publish_product        CALL Shopify API       ──▶ POST /products       │
│  14:30   publish_product        WRITE product          ──▶ UPSERT               │
│  14:30   publish_product        WRITE product_staging  ──▶ UPDATE               │
│  14:30   publish_product        WRITE sync_schedule    ──▶ INSERT   ◀── NEW!    │
│                                                                                 │
│  ══════════════════════════════════════════════════════════════════════════════ │
│  PHASE 2: DAILY SYNC (happens every 24 hours)                                   │
│  ══════════════════════════════════════════════════════════════════════════════ │
│                                                                                 │
│  ─── NEXT DAY, 09:00 UTC ───                                                    │
│                                                                                 │
│  09:00   Celery Beat            Trigger dispatch                                │
│  09:00   dispatch_hourly_sync   WRITE sync_schedule    ──▶ UPDATE (reset stuck) │
│  09:00   dispatch_hourly_sync   READ sync_schedule     ──▶ SELECT (hour=9)      │
│  09:00   dispatch_hourly_sync   WRITE sync_schedule    ──▶ UPDATE (lock)        │
│  09:00   dispatch_hourly_sync   Queue batch tasks      ──▶ Redis LPUSH          │
│                                                                                 │
│  09:00   sync_boeing_batch      READ sync_schedule     ──▶ SELECT (get hash)    │
│  09:01   sync_boeing_batch      CALL Boeing API        ──▶ POST /pricing        │
│  09:01   sync_boeing_batch      Compare hashes         (in memory)              │
│                                                                                 │
│  ─── IF NO CHANGE ───                                                           │
│  09:01   sync_boeing_batch      WRITE sync_schedule    ──▶ UPDATE (success)     │
│                                 (Done for this product)                         │
│                                                                                 │
│  ─── IF CHANGE DETECTED ───                                                     │
│  09:01   sync_boeing_batch      WRITE sync_schedule    ──▶ UPDATE (success)     │
│  09:01   sync_boeing_batch      Queue Shopify update   ──▶ Redis LPUSH          │
│  09:02   sync_shopify_product   READ product_staging   ──▶ SELECT               │
│  09:02   sync_shopify_product   CALL Shopify API       ──▶ PUT /variants        │
│  09:02   sync_shopify_product   CALL Shopify API       ──▶ POST /inventory      │
│  09:02   sync_shopify_product   WRITE product_staging  ──▶ UPDATE               │
│  09:02   sync_shopify_product   WRITE product          ──▶ UPDATE               │
│                                                                                 │
│  ─── NEXT DAY, 09:00 UTC ───                                                    │
│  (Cycle repeats)                                                                │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 3: The Scheduling Model

### How Products Are Assigned to Hours

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  THE HOUR BUCKET CONCEPT                                                        │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Each product is assigned to exactly ONE hour of the day (0-23).                │
│  The assignment is deterministic: same SKU always maps to same hour.            │
│                                                                                 │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  SKU "PN-001"  ───hash───▶  large integer  ───mod 24───▶  hour 9          │  │
│  │  SKU "PN-002"  ───hash───▶  large integer  ───mod 24───▶  hour 14         │  │
│  │  SKU "PN-003"  ───hash───▶  large integer  ───mod 24───▶  hour 9          │  │
│  │  SKU "PN-004"  ───hash───▶  large integer  ───mod 24───▶  hour 22         │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
│  WHY HASH-BASED ASSIGNMENT?                                                     │
│  ─────────────────────────────────────────────────────────────────────────────  │
│  1. Deterministic: No storage needed for assignment (computed on demand)        │
│  2. Even distribution: Hash functions spread values uniformly                   │
│  3. Stable: SKU always maps to same hour (no reassignment needed)               │
│  4. Automatic: New products self-assign without any logic                       │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Distribution Math

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  DISTRIBUTION AT 5,000 PRODUCTS                                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  5,000 products ÷ 24 hours = 208.33 products per hour (average)                 │
│                                                                                 │
│  Due to hash distribution, actual values vary:                                  │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  Hour 00: 195 products  ████████████████████                            │    │
│  │  Hour 01: 212 products  █████████████████████▌                          │    │
│  │  Hour 02: 203 products  ████████████████████▎                           │    │
│  │  Hour 03: 218 products  █████████████████████▊                          │    │
│  │  ...                                                                    │    │
│  │  Hour 23: 201 products  ████████████████████                            │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  Variance is typically ±10-15% from average. At 208 average:                    │
│  - Minimum hour might have ~180 products                                        │
│  - Maximum hour might have ~240 products                                        │
│                                                                                 │
│  This variance is acceptable because we have massive headroom.                  │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Capacity Analysis

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  CAPACITY MATH: HOW MUCH CAN WE PROCESS PER HOUR?                               │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Given:                                                                         │
│  - 2 Boeing API calls per minute (rate limit)                                   │
│  - 10 products per API call (batch size)                                        │
│  - 60 minutes per hour                                                          │
│                                                                                 │
│  Maximum capacity = 2 calls/min × 10 products/call × 60 min = 1,200 products    │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │                                                                         │    │
│  │  HOURLY CAPACITY:  1,200 products                                       │    │
│  │  ACTUAL LOAD:        208 products (at 5K total)                         │    │
│  │  HEADROOM:           992 products (5× buffer!)                          │    │
│  │  UTILIZATION:         17% of capacity                                   │    │
│  │                                                                         │    │
│  │  ▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │    │
│  │  |←── 17% used ──→|←────────── 83% available for retries ──────────→|  │    │
│  │                                                                         │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  WHY THIS HEADROOM MATTERS:                                                     │
│  - Retries don't impact schedule (plenty of room)                               │
│  - System recovers naturally from brief outages                                 │
│  - Can scale to 10K products (35% utilization) without changes                  │
│  - Future-proofed for growth                                                    │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 4: Job Locking and Preventing Double-Processing

### The Double-Processing Problem

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  WITHOUT LOCKING: Race Conditions                                               │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Scenario: Two workers both try to process the same product                     │
│                                                                                 │
│  TIME     WORKER A                    WORKER B                                  │
│  ─────────────────────────────────────────────────────────────────────────────  │
│  09:00    Query: "Get products        Query: "Get products                      │
│           for hour 9"                 for hour 9"                               │
│                                                                                 │
│  09:00    Result: [PN-001, PN-002]    Result: [PN-001, PN-002]   ◀── SAME!     │
│                                                                                 │
│  09:01    Call Boeing for PN-001      Call Boeing for PN-001     ◀── DUPLICATE │
│                                                                                 │
│  09:02    Update Shopify              Update Shopify             ◀── DUPLICATE │
│                                                                                 │
│  RESULT: Wasted API calls, potential race conditions on updates                 │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Our Locking Strategy: Status-Based Locks

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STATUS-BASED LOCKING                                                           │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Each product has a sync_status field:                                          │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  pending   →  Waiting to be picked up                                     │  │
│  │  syncing   →  Currently being processed (LOCKED)                          │  │
│  │  success   →  Last sync completed successfully                            │  │
│  │  failed    →  Last sync failed                                            │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
│  The status acts as a lock:                                                     │
│                                                                                 │
│  TIME     DISPATCHER                  DATABASE                                  │
│  ─────────────────────────────────────────────────────────────────────────────  │
│  09:00    Query: "WHERE hour_bucket=9                                           │
│           AND sync_status != 'syncing'"                                         │
│                                       Returns: [PN-001, PN-002, PN-003]         │
│                                                                                 │
│  09:00    Update: "SET sync_status =                                            │
│           'syncing' WHERE id IN (...)"                                          │
│                                       PN-001.status = 'syncing'                 │
│                                       PN-002.status = 'syncing'                 │
│                                       PN-003.status = 'syncing'                 │
│                                                                                 │
│  09:00    Queue batch tasks                                                     │
│           (products are now locked)                                             │
│                                                                                 │
│  ─────────────────────────────────────────────────────────────────────────────  │
│  If another dispatcher runs at 09:00 (edge case):                               │
│                                                                                 │
│  09:00    Query: "WHERE hour_bucket=9                                           │
│           AND sync_status != 'syncing'"                                         │
│                                       Returns: []  ◀── Empty! All locked        │
│                                                                                 │
│  No duplicate processing!                                                       │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Why Not Database Row Locks?

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  ALTERNATIVE: SELECT ... FOR UPDATE SKIP LOCKED                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  This PostgreSQL feature provides true database-level locking.                  │
│                                                                                 │
│  WHY WE DON'T USE IT:                                                           │
│                                                                                 │
│  1. COMPLEXITY:                                                                 │
│     - Requires holding transactions open during processing                      │
│     - Distributed workers can't share transactions                              │
│     - Supabase (our DB) has connection limits                                   │
│                                                                                 │
│  2. OUR SCALE DOESN'T NEED IT:                                                  │
│     - Single dispatcher per hour (no concurrent dispatchers)                    │
│     - Sequential batching (no parallel batch creation)                          │
│     - Status-based locks are sufficient                                         │
│                                                                                 │
│  3. STATUS LOCKS PROVIDE VISIBILITY:                                            │
│     - Can query "what's currently syncing?"                                     │
│     - Can see stuck jobs in dashboard                                           │
│     - Easy to debug production issues                                           │
│                                                                                 │
│  WHEN TO USE FOR UPDATE SKIP LOCKED:                                            │
│  - Multiple competing dispatchers                                               │
│  - High contention scenarios                                                    │
│  - Need guaranteed atomic acquisition                                           │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Handling Stuck Jobs

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  PROBLEM: Worker Crashes While Processing                                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Scenario: Worker picks up job, crashes before completing                       │
│                                                                                 │
│  09:00    Worker starts processing PN-001 (status = 'syncing')                  │
│  09:01    Worker crashes! 💥                                                    │
│  09:02    PN-001 still has status = 'syncing' (orphaned lock)                   │
│                                                                                 │
│  If we do nothing, PN-001 never syncs again.                                    │
│                                                                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│  SOLUTION: Stuck Job Recovery                                                   │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  At the start of each hourly dispatch, we check for stuck jobs:                 │
│                                                                                 │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  "Find all products where:                                                │  │
│  │   - sync_status = 'syncing'                                               │  │
│  │   - last_sync_at was more than 30 minutes ago                             │  │
│  │                                                                           │  │
│  │   These are stuck. Reset them to 'pending' so they can be retried."       │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
│  WHY 30 MINUTES?                                                                │
│  - Normal processing takes ~10 minutes per hour                                 │
│  - 30 minutes is 3× the expected time                                           │
│  - Provides buffer for retries and slowdowns                                    │
│  - If still stuck after 30 min, something is wrong                              │
│                                                                                 │
│  SELF-HEALING: No manual intervention needed!                                   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 5: Rate Limiting Deep Dive

### Understanding the Constraint

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  BOEING API RATE LIMIT: 2 REQUESTS PER MINUTE                                   │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  This is a HARD LIMIT. Exceed it and:                                           │
│  - Requests return HTTP 429 (Too Many Requests)                                 │
│  - Your account may be temporarily blocked                                      │
│  - Data freshness degrades                                                      │
│                                                                                 │
│  We must guarantee we never exceed 2 requests per minute.                       │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### How Celery Rate Limiting Works

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  CELERY RATE LIMITING: TOKEN BUCKET ALGORITHM                                   │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Conceptually, imagine a bucket that holds tokens:                              │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  rate_limit = '2/m' means:                                              │    │
│  │  - Bucket starts with 2 tokens                                          │    │
│  │  - Each task consumes 1 token                                           │    │
│  │  - Bucket refills at rate of 2 tokens per minute                        │    │
│  │  - If bucket empty, task waits for token                                │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  TIME        BUCKET     EVENT                                                   │
│  ───────────────────────────────────────────────────────────────────────────    │
│  00:00.000   [●●]       Task 1 arrives, takes token                             │
│  00:00.001   [● ]       Task 1 executes                                         │
│  00:00.500   [● ]       Task 2 arrives, takes token                             │
│  00:00.501   [  ]       Task 2 executes                                         │
│  00:01.000   [  ]       Task 3 arrives, NO TOKENS → WAIT                        │
│  00:30.000   [● ]       30 sec passed, 1 token added                            │
│  00:30.001   [  ]       Task 3 takes token, executes                            │
│  00:31.000   [  ]       Task 4 arrives, NO TOKENS → WAIT                        │
│  01:00.000   [● ]       60 sec from start, 1 token added                        │
│  01:00.001   [  ]       Task 4 takes token, executes                            │
│                                                                                 │
│  Result: Maximum 2 tasks in first second (burst), then 1 per 30 seconds         │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Why Single Worker for Boeing Queue?

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  CRITICAL: RATE LIMIT IS PER-WORKER, NOT GLOBAL                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ❌ WRONG: Multiple workers with same rate limit                                │
│                                                                                 │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  Worker 1: rate_limit='2/m'  →  2 calls/min                               │  │
│  │  Worker 2: rate_limit='2/m'  →  2 calls/min                               │  │
│  │  Worker 3: rate_limit='2/m'  →  2 calls/min                               │  │
│  │  ─────────────────────────────────────────────                            │  │
│  │  TOTAL: 6 calls/min  ← EXCEEDS BOEING'S LIMIT!                            │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
│  ✅ CORRECT: Single worker with concurrency=1                                   │
│                                                                                 │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  Worker 1: rate_limit='2/m', concurrency=1  →  2 calls/min                │  │
│  │  ─────────────────────────────────────────────                            │  │
│  │  TOTAL: 2 calls/min  ← MATCHES BOEING'S LIMIT ✓                           │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
│  This is why we run:                                                            │
│  celery worker -Q sync_boeing --concurrency=1                                   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Queue Separation Strategy

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  WHY SEPARATE QUEUES?                                                           │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  Queue: sync_boeing     │  Worker: concurrency=1, rate_limit=2/m        │    │
│  │  Queue: sync_shopify    │  Worker: concurrency=4, rate_limit=30/m       │    │
│  │  Queue: default         │  Worker: concurrency=4, no rate limit         │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  WHY THIS SEPARATION?                                                           │
│                                                                                 │
│  1. DIFFERENT RATE LIMITS:                                                      │
│     Boeing (2/min) needs tight control                                          │
│     Shopify (30/min) can be more aggressive                                     │
│     Default queue has no API limits                                             │
│                                                                                 │
│  2. ISOLATION:                                                                  │
│     Boeing slowdown doesn't block Shopify updates                               │
│     Shopify issues don't affect Boeing syncs                                    │
│     System remains responsive even if one queue backs up                        │
│                                                                                 │
│  3. SCALING:                                                                    │
│     Can add Shopify workers independently                                       │
│     Boeing queue stays at 1 worker (rate limit bound)                           │
│     Default queue scales with server capacity                                   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 6: Complete Data Flow

### Flow Diagram: Hourly Sync Cycle

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  COMPLETE HOURLY SYNC FLOW                                                      │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │  PHASE 1: DISPATCH (runs at HH:00)                                       │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│    Celery Beat                                                                  │
│        │                                                                        │
│        │ "It's 09:00 UTC"                                                       │
│        ▼                                                                        │
│    ┌───────────────────────┐                                                    │
│    │  dispatch_hourly_sync │                                                    │
│    └───────────┬───────────┘                                                    │
│                │                                                                │
│                │ 1. Reset stuck jobs (status='syncing' for >30min)              │
│                │ 2. Query products WHERE hour_bucket = 9                        │
│                │ 3. Mark all as 'syncing' (acquire lock)                        │
│                │ 4. Create batches of 10                                        │
│                │ 5. Queue batch tasks to sync_boeing queue                      │
│                ▼                                                                │
│    ┌──────────────────────────────────────────────────────────────────────┐     │
│    │  Redis Queue: sync_boeing                                            │     │
│    │  [batch_1] [batch_2] [batch_3] ... [batch_21]                        │     │
│    └──────────────────────────────────────────────────────────────────────┘     │
│                                                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │  PHASE 2: BOEING SYNC (rate limited to 2/min)                            │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│    Boeing Worker (concurrency=1)                                                │
│        │                                                                        │
│        │ Pull batch_1 from queue                                                │
│        ▼                                                                        │
│    ┌────────────────────────────────────────────────────────────────────────┐   │
│    │  sync_boeing_batch([SKU1, SKU2, ... SKU10])                            │   │
│    │                                                                        │   │
│    │  1. Call Boeing API with 10 SKUs                                       │   │
│    │  2. For each SKU:                                                      │   │
│    │     a. Compute hash of response (price, qty, stock)                    │   │
│    │     b. Compare with stored hash                                        │   │
│    │     c. If changed → queue Shopify update                               │   │
│    │  3. Update sync_schedule:                                              │   │
│    │     - status = 'success'                                               │   │
│    │     - last_sync_at = now                                               │   │
│    │     - next_sync_at = now + 24h                                         │   │
│    │     - last_boeing_hash = new_hash                                      │   │
│    │                                                                        │   │
│    └────────────────────────────────────────────────────────────────────────┘   │
│        │                                                                        │
│        │ Wait ~30 seconds (rate limit)                                          │
│        │ Pull batch_2...                                                        │
│        │ (repeat until all batches processed)                                   │
│        │                                                                        │
│        │ If change detected:                                                    │
│        ▼                                                                        │
│    ┌──────────────────────────────────────────────────────────────────────┐     │
│    │  Redis Queue: sync_shopify                                           │     │
│    │  [update_SKU47] [update_SKU128] ...                                  │     │
│    └──────────────────────────────────────────────────────────────────────┘     │
│                                                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │  PHASE 3: SHOPIFY UPDATE (only if data changed)                          │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│    Shopify Worker                                                               │
│        │                                                                        │
│        │ Pull update task from queue                                            │
│        ▼                                                                        │
│    ┌────────────────────────────────────────────────────────────────────────┐   │
│    │  sync_shopify_product(SKU47, new_price, new_qty)                       │   │
│    │                                                                        │   │
│    │  1. Look up shopify_product_id from DB                                 │   │
│    │  2. Calculate Shopify price (Boeing price × 1.1)                       │   │
│    │  3. Update Shopify variant price                                       │   │
│    │  4. Update Shopify inventory level                                     │   │
│    │  5. Update local DB to match                                           │   │
│    │                                                                        │   │
│    └────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Timeline: One Hour of Processing

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  TIMELINE: PROCESSING 208 PRODUCTS IN HOUR 9                                    │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  TIME        EVENT                                            DURATION          │
│  ───────────────────────────────────────────────────────────────────────────    │
│  09:00:00    Celery Beat triggers dispatch                    instant           │
│  09:00:00    Dispatcher queries DB for hour_bucket=9          ~50ms             │
│  09:00:00    Dispatcher marks 208 products as 'syncing'       ~100ms            │
│  09:00:00    Dispatcher creates 21 batches, queues them       ~50ms             │
│                                                                                 │
│  09:00:00    Boeing worker pulls batch 1                                        │
│  09:00:01    Boeing API call completes                        ~1 sec            │
│  09:00:01    Process results, maybe queue Shopify updates     ~100ms            │
│                                                                                 │
│  09:00:01    Boeing worker pulls batch 2 (immediate)                            │
│  09:00:02    Boeing API call completes                        ~1 sec            │
│              (2 calls done - rate limit kicks in)                               │
│                                                                                 │
│  09:00:30    Rate limit allows batch 3                        30 sec wait       │
│  09:00:31    Boeing API call completes                        ~1 sec            │
│                                                                                 │
│  09:01:00    Rate limit allows batch 4                        30 sec wait       │
│  09:01:01    Boeing API call completes                        ~1 sec            │
│                                                                                 │
│  ... (pattern continues: 2 calls per minute) ...                                │
│                                                                                 │
│  09:10:00    Batch 21 (last batch) completes                                    │
│  09:10:00    All 208 products synced                          TOTAL: ~10 min    │
│                                                                                 │
│  09:10:00    │─────────────────────────────────────────────│   50 min idle     │
│  to 10:00:00 │           IDLE / AVAILABLE FOR RETRIES       │                   │
│              │─────────────────────────────────────────────│                    │
│                                                                                 │
│  10:00:00    Next hour dispatch begins (hour_bucket=10)                         │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 7: Failure Handling and Self-Healing

### Types of Failures

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  FAILURE TAXONOMY                                                               │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  TRANSIENT FAILURES (self-healing)                                      │    │
│  │  ─────────────────────────────────────────────────────────────────────  │    │
│  │  • Network timeout                                                      │    │
│  │  • Boeing API returns 500                                               │    │
│  │  • Temporary database unavailable                                       │    │
│  │                                                                         │    │
│  │  HANDLING: Automatic retry with exponential backoff                     │    │
│  │  • 1st failure: retry in 2 hours                                        │    │
│  │  • 2nd failure: retry in 4 hours                                        │    │
│  │  • 3rd failure: retry in 8 hours                                        │    │
│  │  • 4th failure: retry in 16 hours                                       │    │
│  │  • 5th failure: retry in 24 hours (capped)                              │    │
│  │                                                                         │    │
│  │  WHY EXPONENTIAL BACKOFF?                                               │    │
│  │  • Gives external systems time to recover                               │    │
│  │  • Prevents hammering a failing service                                 │    │
│  │  • Most transient issues resolve within hours                           │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  PERSISTENT FAILURES (requires attention)                               │    │
│  │  ─────────────────────────────────────────────────────────────────────  │    │
│  │  • SKU no longer exists in Boeing system                                │    │
│  │  • Invalid product data                                                 │    │
│  │  • Shopify product deleted externally                                   │    │
│  │                                                                         │    │
│  │  HANDLING: Deactivate after 5 consecutive failures                      │    │
│  │  • is_active = FALSE                                                    │    │
│  │  • Product stops syncing                                                │    │
│  │  • Shows up in monitoring dashboard                                     │    │
│  │  • Requires manual review                                               │    │
│  │                                                                         │    │
│  │  WHY 5 FAILURES?                                                        │    │
│  │  • Allows for extended outages (5 days of retries)                      │    │
│  │  • Filters out truly broken products                                    │    │
│  │  • Prevents wasting API calls on bad data                               │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Self-Healing Mechanisms

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  SELF-HEALING: ZERO MANUAL INTERVENTION NEEDED                                  │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  MECHANISM 1: STUCK JOB RECOVERY                                                │
│  ─────────────────────────────────────────────────────────────────────────────  │
│  Problem:  Worker crashes mid-processing, job stuck in 'syncing'                │
│  Solution: Hourly dispatcher resets jobs stuck for >30 minutes                  │
│  Result:   Job automatically retries next hour                                  │
│                                                                                 │
│  MECHANISM 2: EXPONENTIAL BACKOFF                                               │
│  ─────────────────────────────────────────────────────────────────────────────  │
│  Problem:  Boeing API is down                                                   │
│  Solution: Failed jobs retry with increasing delays                             │
│  Result:   System waits for Boeing to recover, then catches up                  │
│                                                                                 │
│  MECHANISM 3: AUTOMATIC DEACTIVATION                                            │
│  ─────────────────────────────────────────────────────────────────────────────  │
│  Problem:  SKU is permanently invalid                                           │
│  Solution: After 5 failures, product marked inactive                            │
│  Result:   Bad data stops wasting resources                                     │
│                                                                                 │
│  MECHANISM 4: IDEMPOTENT OPERATIONS                                             │
│  ─────────────────────────────────────────────────────────────────────────────  │
│  Problem:  Same task runs twice (rare edge case)                                │
│  Solution: All operations are idempotent (same result if run twice)             │
│  Result:   No data corruption from duplicate processing                         │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  WHAT "IDEMPOTENT" MEANS:                                               │    │
│  │                                                                         │    │
│  │  Running "set price to $150" twice is safe (same result)                │    │
│  │  Running "add $10 to price" twice is NOT safe (different result)        │    │
│  │                                                                         │    │
│  │  All our operations are SET operations, not ADD operations.             │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 7B: Production-Ready Failure Handling (Revised)

### The Time Drift Problem

The original design had a critical flaw. Here's the problem:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  THE TIME DRIFT PROBLEM                                                         │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ORIGINAL DESIGN (FLAWED):                                                      │
│  ─────────────────────────                                                      │
│  Dispatcher query: SELECT * WHERE hour_bucket = 14                              │
│                                                                                 │
│  SCENARIO:                                                                      │
│  • Product A: hour_bucket = 10, next_sync_at = today 10:00                      │
│  • 10:00: Dispatcher runs, Product A syncs, Boeing API FAILS                    │
│  • Backoff: next_sync_at = today 12:00 (but hour_bucket still = 10!)            │
│  • 12:00: Dispatcher runs for hour_bucket = 12                                  │
│  • Product A has hour_bucket = 10 → NOT PICKED UP                               │
│  • Product A is LOST until tomorrow 10:00                                       │
│                                                                                 │
│  ROOT CAUSE:                                                                    │
│  ─────────────                                                                  │
│  Dispatcher filters by hour_bucket, but backoff changes next_sync_at.           │
│  hour_bucket never changes → failed products are missed.                        │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Production Pattern: Unified next_sync_at Query

Based on patterns from AWS, Google Cloud Scheduler, Sidekiq, and database job queue architectures:

**Sources:**
- AWS Prescriptive Guidance: https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/retry-backoff.html
- AWS Builders Library: https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/
- System Design: https://blog.algomaster.io/p/design-a-distributed-job-scheduler

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  PRODUCTION PATTERN: UNIFIED next_sync_at SCHEDULING                            │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  KEY INSIGHT:                                                                   │
│  ─────────────                                                                  │
│  hour_bucket is ONLY for calculating next_sync_at on SUCCESS.                   │
│  Dispatcher NEVER filters by hour_bucket - ONLY by next_sync_at.                │
│                                                                                 │
│  SINGLE DISPATCHER QUERY:                                                       │
│  ─────────────────────────                                                      │
│  SELECT * FROM product_sync_schedule                                            │
│  WHERE next_sync_at <= NOW()                                                    │
│  AND is_active = TRUE                                                           │
│  AND sync_status != 'syncing'                                                   │
│  ORDER BY next_sync_at ASC                                                      │
│  LIMIT 250                                                                      │
│                                                                                 │
│  That's it. No dual query. No hour_bucket filter.                               │
│                                                                                 │
│  WHY THIS WORKS:                                                                │
│  ───────────────                                                                │
│  • Scheduled products: next_sync_at set to their hour_bucket time               │
│  • Failed products: next_sync_at set to NOW + backoff                           │
│  • ALL products with next_sync_at <= NOW are picked up                          │
│  • No products are ever missed                                                  │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### How hour_bucket Is Actually Used

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  hour_bucket = PERMANENT (for distribution calculation)                         │
│  next_sync_at = DYNAMIC (changes on every sync attempt)                         │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ON PRODUCT PUBLISH:                                                            │
│  ───────────────────                                                            │
│  hour_bucket = least_loaded_bucket()     ← Permanent, never changes             │
│  next_sync_at = next occurrence of hour_bucket (e.g., tomorrow 10:00)           │
│                                                                                 │
│  ON SYNC SUCCESS:                                                               │
│  ────────────────                                                               │
│  next_sync_at = next occurrence of hour_bucket                                  │
│  (Product RETURNS to its assigned hour, distribution preserved)                 │
│                                                                                 │
│  ON SYNC FAILURE:                                                               │
│  ────────────────                                                               │
│  next_sync_at = NOW() + exponential_backoff + jitter                            │
│  (Product temporarily leaves its hour, retries sooner)                          │
│                                                                                 │
│  AFTER RECOVERY:                                                                │
│  ───────────────                                                                │
│  next_sync_at = next occurrence of hour_bucket                                  │
│  (Product returns to its original schedule)                                     │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Exponential Backoff With Jitter

From AWS Builders Library - jitter prevents "thundering herd" when multiple failures retry simultaneously:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  EXPONENTIAL BACKOFF WITH JITTER                                                │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  FORMULA:                                                                       │
│  ─────────                                                                      │
│  base_backoff = min(cap, base × 2^attempt)                                      │
│  jitter = random(0, base_backoff × 0.3)      ← 30% randomness                   │
│  next_sync_at = NOW() + base_backoff + jitter                                   │
│                                                                                 │
│  EXAMPLE (base=2 hours, cap=24 hours):                                          │
│  ─────────────────────────────────────                                          │
│  Attempt 1: 2h  + jitter(0-36min) → retry between 2h 0min and 2h 36min          │
│  Attempt 2: 4h  + jitter(0-72min) → retry between 4h 0min and 5h 12min          │
│  Attempt 3: 8h  + jitter(0-2.4h)  → retry between 8h 0min and 10h 24min         │
│  Attempt 4: 16h + jitter(0-4.8h)  → retry between 16h 0min and 20h 48min        │
│  Attempt 5: 24h (capped) + jitter → deactivate after this                       │
│                                                                                 │
│  WHY JITTER MATTERS:                                                            │
│  ───────────────────                                                            │
│  Without jitter: 10 products fail at 10:00 → ALL retry at exactly 12:00         │
│  With jitter:    10 products fail at 10:00 → retries spread 12:00-12:36         │
│                                                                                 │
│  According to AWS research, jitter reduces retry spike collisions by 40-60%.    │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Rate Limiting With LIMIT Clause

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  RATE LIMITING: SINGLE QUERY WITH LIMIT                                         │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  DISPATCHER QUERY:                                                              │
│  ─────────────────                                                              │
│  SELECT * FROM product_sync_schedule                                            │
│  WHERE next_sync_at <= NOW()                                                    │
│  AND is_active = TRUE                                                           │
│  AND sync_status != 'syncing'                                                   │
│  ORDER BY next_sync_at ASC      ← Oldest due first (fairness)                   │
│  LIMIT 250                      ← Cap per hour (rate limit protection)          │
│                                                                                 │
│  WHY 250?                                                                       │
│  ─────────                                                                      │
│  • 250 products = 25 batches of 10 SKUs                                         │
│  • At 2 req/min = 12.5 minutes to complete                                      │
│  • Leaves buffer time within the hour                                           │
│  • Overflow products stay due (next_sync_at still <= NOW)                       │
│  • Picked up next hour automatically                                            │
│                                                                                 │
│  OVERFLOW HANDLING:                                                             │
│  ──────────────────                                                             │
│  If 280 products are due at 10:00:                                              │
│  • 10:00 dispatcher: processes 250, leaves 30                                   │
│  • 11:00 dispatcher: picks up 30 (still due) + 208 scheduled = 238              │
│  • System self-balances, no products lost                                       │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Why Distribution Stays Balanced

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  LOAD DISTRIBUTION REMAINS BALANCED                                             │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  SCENARIO: 5000 products, ~208 per hour bucket                                  │
│                                                                                 │
│  NORMAL STATE (no failures):                                                    │
│  ───────────────────────────                                                    │
│  Hour 10: ~208 products have next_sync_at = today 10:00                         │
│  Hour 11: ~208 products have next_sync_at = today 11:00                         │
│  ...                                                                            │
│  Distribution is preserved because SUCCESS resets next_sync_at to hour_bucket   │
│                                                                                 │
│  WITH FAILURES (e.g., 5% failure rate = 10 products/hour fail):                 │
│  ──────────────────────────────────────────────────────────────                 │
│  Hour 10: 208 scheduled + ~5-10 retries from earlier = ~218 products            │
│  Hour 11: 208 scheduled + ~5-10 retries from earlier = ~218 products            │
│                                                                                 │
│  Retries are NATURALLY SPREAD because backoff + jitter times vary:              │
│  • Product A failed at 10:00 → retry at 12:18 (+2h + 18min jitter)              │
│  • Product B failed at 10:05 → retry at 12:29 (+2h + 24min jitter)              │
│  • Product C failed at 09:30 (2nd fail) → retry at 13:45 (+4h + 15min jitter)   │
│                                                                                 │
│  No "thundering herd" - retries naturally distribute across time.               │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Complete Revised Failure Flow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    PRODUCTION-READY FAILURE HANDLING FLOW                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│                         CELERY BEAT (Every Hour)                                │
│                                    │                                            │
│                                    ▼                                            │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │  STEP 1: Reset Stuck Jobs (>30 min in 'syncing' status)                 │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                    │                                            │
│                                    ▼                                            │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │  STEP 2: SINGLE QUERY (no hour_bucket filter!)                          │   │
│  │  SELECT * FROM product_sync_schedule                                    │   │
│  │  WHERE next_sync_at <= NOW() AND is_active = TRUE                       │   │
│  │  AND sync_status != 'syncing'                                           │   │
│  │  ORDER BY next_sync_at ASC LIMIT 250                                    │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                    │                                            │
│                                    ▼                                            │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │  STEP 3: Lock (sync_status='syncing') + Create batches of 10            │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                    │                                            │
│                                    ▼                                            │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │  STEP 4: Process batches (Celery rate_limit='2/m')                      │   │
│  │                                                                         │   │
│  │       ┌─────────────────┐              ┌─────────────────┐              │   │
│  │       │     SUCCESS     │              │     FAILURE     │              │   │
│  │       ├─────────────────┤              ├─────────────────┤              │   │
│  │       │ next_sync_at =  │              │ backoff =       │              │   │
│  │       │   next_occurrence│              │   2^failures    │              │   │
│  │       │   of hour_bucket │              │ jitter =        │              │   │
│  │       │                 │              │   rand(0, 30%)  │              │   │
│  │       │ failures = 0   │              │ next_sync_at =  │              │   │
│  │       │                 │              │   NOW + backoff │              │   │
│  │       │ Returns to      │              │   + jitter      │              │   │
│  │       │ original hour   │              │                 │              │   │
│  │       └─────────────────┘              │ failures++      │              │   │
│  │                                        │                 │              │   │
│  │                                        │ If failures>=5: │              │   │
│  │                                        │   is_active=F   │              │   │
│  │                                        └─────────────────┘              │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Key Differences: Original vs Production Design

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  COMPARISON: ORIGINAL vs PRODUCTION-READY DESIGN                                │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌───────────────────┬────────────────────────┬────────────────────────────┐   │
│  │     ASPECT        │   ORIGINAL (Complex)   │  PRODUCTION (Simple)       │   │
│  ├───────────────────┼────────────────────────┼────────────────────────────┤   │
│  │ Query             │ Dual query (hour_bucket│ Single query (next_sync_at │   │
│  │                   │ + next_sync_at)        │ only)                      │   │
│  ├───────────────────┼────────────────────────┼────────────────────────────┤   │
│  │ hour_bucket use   │ Filter in dispatcher   │ Only for calculating       │   │
│  │                   │                        │ next_sync_at on success    │   │
│  ├───────────────────┼────────────────────────┼────────────────────────────┤   │
│  │ Rate limiting     │ Complex (two sources)  │ Simple LIMIT clause        │   │
│  ├───────────────────┼────────────────────────┼────────────────────────────┤   │
│  │ Retry handling    │ Separate logic/query   │ Same table, same query     │   │
│  ├───────────────────┼────────────────────────┼────────────────────────────┤   │
│  │ Jitter            │ None                   │ 30% randomness on backoff  │   │
│  ├───────────────────┼────────────────────────┼────────────────────────────┤   │
│  │ Overflow          │ Not handled            │ Auto-handled by next hour  │   │
│  ├───────────────────┼────────────────────────┼────────────────────────────┤   │
│  │ Time drift        │ Products get lost      │ Impossible - all due       │   │
│  │                   │                        │ products always picked up  │   │
│  └───────────────────┴────────────────────────┴────────────────────────────┘   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 7C: Inline Retry Within Same Slot (Recommended)

### The Distribution Preservation Problem

Part 7B's "Unified next_sync_at" approach solves the time drift problem but introduces a new issue:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  THE UNEQUAL LOAD PROBLEM (Part 7B Issue)                                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  SCENARIO WITH UNIFIED next_sync_at:                                            │
│  ───────────────────────────────────                                            │
│  Hour 10: 208 scheduled products + 15 retries (from earlier failures)           │
│  Hour 11: 208 scheduled products + 8 retries (from earlier failures)            │
│  Hour 12: 208 scheduled products + 22 retries (from earlier failures)           │
│                                                                                 │
│  PROBLEM:                                                                       │
│  ─────────                                                                      │
│  Load per hour varies: 223, 216, 230, etc.                                      │
│  Equal distribution is NOT preserved.                                           │
│  Under high failure rates, some hours may process significantly more.           │
│                                                                                 │
│  WHY THIS HAPPENS:                                                              │
│  ─────────────────                                                              │
│  Backoff + jitter pushes retries into DIFFERENT hours than their original slot. │
│  Slot 10 product failing at 10:00 might retry at 12:24 → competes with Slot 12. │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Proposed Solution: Inline Retry Within Same Slot

Instead of pushing failed products to future hours (where they compete with other slots), retry them **within the same hour** after the initial batch completes.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  INLINE RETRY: EACH SLOT HANDLES ITS OWN RETRIES                                │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  PRINCIPLE:                                                                     │
│  ──────────                                                                     │
│  "Slot 2 at 02:00 is responsible for ALL Slot 2 products, including retries."   │
│                                                                                 │
│  FLOW:                                                                          │
│  ──────                                                                         │
│  02:00 - Dispatcher fires for hour_bucket = 2                                   │
│        - PASS 1: Process all 208 products (10.5 minutes)                        │
│        - Track failures in-memory: 10 products failed                           │
│  02:11 - PASS 2: Retry 10 failed products (30 seconds)                          │
│        - 3 products still failing                                               │
│  02:12 - PASS 3: Retry 3 remaining (30 seconds)                                 │
│        - 1 product still failing → mark for next day                            │
│  02:13 - COMPLETE - 47 minutes of buffer remaining                              │
│                                                                                 │
│  RESULT:                                                                        │
│  ───────                                                                        │
│  ✅ Slot 2 processes ONLY Slot 2 products (no collision with other slots)       │
│  ✅ Equal distribution preserved (each hour = ~208 products)                    │
│  ✅ Retries happen immediately (better for transient errors)                    │
│  ✅ Single rate limiter (no competing queues)                                   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Time Budget Analysis

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  TIME BUDGET PER HOUR SLOT                                                      │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  CONSTANTS:                                                                     │
│  ───────────                                                                    │
│  Products per slot: 5,000 ÷ 24 = 208 products                                   │
│  Requests for initial pass: 208 ÷ 10 = 21 requests                              │
│  Rate limit: 2 requests/minute                                                  │
│  Time for initial pass: 21 ÷ 2 = 10.5 minutes                                   │
│                                                                                 │
│  TIME ALLOCATION:                                                               │
│  ────────────────                                                               │
│  ┌────────────────────────────────────────────────────────────────────────┐     │
│  │  00:00-10:30  │  PASS 1: Initial sync (21 requests)                    │     │
│  ├────────────────────────────────────────────────────────────────────────┤     │
│  │  10:30-11:00  │  PASS 2: Retry failures (typically 1-3 requests)       │     │
│  ├────────────────────────────────────────────────────────────────────────┤     │
│  │  11:00-12:00  │  PASS 3: Final retry (typically 0-1 requests)          │     │
│  ├────────────────────────────────────────────────────────────────────────┤     │
│  │  12:00-55:00  │  BUFFER (safety margin before next hour)               │     │
│  ├────────────────────────────────────────────────────────────────────────┤     │
│  │  55:00-60:00  │  HARD STOP (defer any remaining to next day)           │     │
│  └────────────────────────────────────────────────────────────────────────┘     │
│                                                                                 │
│  CAPACITY:                                                                      │
│  ─────────                                                                      │
│  Total requests possible per hour: 60 min × 2 req/min = 120 requests            │
│  Requests used by initial pass: 21 requests                                     │
│  Remaining for retries: 99 requests = 990 SKUs worth of retries                 │
│                                                                                 │
│  WORST CASE (100% failure rate - entire slot fails):                            │
│  ──────────────────────────────────────────────────                             │
│  Pass 1: 208 products fail (21 requests, 10.5 min)                              │
│  Pass 2: 208 products retry (21 requests, 10.5 min)                             │
│  Pass 3: 208 products retry (21 requests, 10.5 min)                             │
│  Total: 63 requests, 31.5 min → Still within 55 min limit ✅                    │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Complete Inline Retry Flow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    INLINE RETRY FLOW (SLOT 2 EXAMPLE)                           │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│    02:00 ─────────────────────────────────────────────────────────────────      │
│      │                                                                          │
│      │   DISPATCHER QUERY:                                                      │
│      │   SELECT * FROM product_sync_schedule                                    │
│      │   WHERE hour_bucket = 2 AND is_active = TRUE                             │
│      │   AND sync_status != 'syncing'                                           │
│      │                                                                          │
│      ▼                                                                          │
│    ┌─────────────────────────────────────────────────────────────────────┐      │
│    │  PASS 1: INITIAL SYNC                                               │      │
│    │  ─────────────────────────                                          │      │
│    │  • Mark all 208 products as sync_status = 'syncing'                 │      │
│    │  • Process in batches of 10 (21 batches @ 2/min = 10.5 min)         │      │
│    │  • For each product:                                                │      │
│    │    - Boeing API call                                                │      │
│    │    - If SUCCESS: sync_status = 'success', reset failures           │      │
│    │    - If FAILURE: sync_status = 'failed_pass_1', failures++         │      │
│    │  • Track failed SKUs in-memory: ['SKU-A', 'SKU-B', ...]            │      │
│    └─────────────────────────────────────────────────────────────────────┘      │
│      │                                                                          │
│      │ (10 products failed)                                                     │
│      ▼                                                                          │
│    ┌─────────────────────────────────────────────────────────────────────┐      │
│    │  WAIT 2 MINUTES (brief cooldown for transient errors)               │      │
│    └─────────────────────────────────────────────────────────────────────┘      │
│      │                                                                          │
│      ▼                                                                          │
│    ┌─────────────────────────────────────────────────────────────────────┐      │
│    │  PASS 2: FIRST RETRY                                                │      │
│    │  ───────────────────────                                            │      │
│    │  • Query: WHERE sync_status = 'failed_pass_1' AND hour_bucket = 2   │      │
│    │  • Process 10 failed products (1 batch @ 30 sec)                    │      │
│    │  • For each product:                                                │      │
│    │    - If SUCCESS: sync_status = 'success', reset failures           │      │
│    │    - If FAILURE: sync_status = 'failed_pass_2', failures++         │      │
│    └─────────────────────────────────────────────────────────────────────┘      │
│      │                                                                          │
│      │ (3 products still failing)                                               │
│      ▼                                                                          │
│    ┌─────────────────────────────────────────────────────────────────────┐      │
│    │  WAIT 2 MINUTES                                                     │      │
│    └─────────────────────────────────────────────────────────────────────┘      │
│      │                                                                          │
│      ▼                                                                          │
│    ┌─────────────────────────────────────────────────────────────────────┐      │
│    │  PASS 3: FINAL RETRY                                                │      │
│    │  ───────────────────────                                            │      │
│    │  • Query: WHERE sync_status = 'failed_pass_2' AND hour_bucket = 2   │      │
│    │  • Process 3 remaining products                                     │      │
│    │  • For each product:                                                │      │
│    │    - If SUCCESS: sync_status = 'success'                           │      │
│    │    - If FAILURE:                                                    │      │
│    │      * sync_status = 'failed'                                       │      │
│    │      * next_sync_at = tomorrow at hour_bucket (02:00)               │      │
│    │      * consecutive_failures++                                       │      │
│    │      * If consecutive_failures >= 5: is_active = False (deactivate)│      │
│    └─────────────────────────────────────────────────────────────────────┘      │
│      │                                                                          │
│      │ (1 product deferred to tomorrow)                                         │
│      ▼                                                                          │
│    02:15 ─── SLOT COMPLETE ── 45 min buffer before next hour ──────────────     │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### State Machine for Inline Retry

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  INLINE RETRY STATE MACHINE                                                     │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│                        WITHIN SAME HOUR WINDOW                                  │
│    ┌───────────────────────────────────────────────────────────────────────┐    │
│    │                                                                       │    │
│    │   pending ──► syncing ──► success ✓                                  │    │
│    │                  │                                                    │    │
│    │                  │ (Boeing API error)                                 │    │
│    │                  ▼                                                    │    │
│    │           failed_pass_1                                               │    │
│    │                  │                                                    │    │
│    │                  │ (Pass 2 retry)                                     │    │
│    │                  ▼                                                    │    │
│    │              syncing ──► success ✓                                   │    │
│    │                  │                                                    │    │
│    │                  │ (Still failing)                                    │    │
│    │                  ▼                                                    │    │
│    │           failed_pass_2                                               │    │
│    │                  │                                                    │    │
│    │                  │ (Pass 3 retry)                                     │    │
│    │                  ▼                                                    │    │
│    │              syncing ──► success ✓                                   │    │
│    │                  │                                                    │    │
│    │                  │ (Still failing)                                    │    │
│    │                  ▼                                                    │    │
│    │           failed_pass_3                                               │    │
│    │                                                                       │    │
│    └───────────────────────────────────────────────────────────────────────┘    │
│                       │                                                         │
│                       │ (Hour window complete)                                  │
│                       ▼                                                         │
│               DEFERRED TO NEXT DAY                                              │
│    ┌───────────────────────────────────────────────────────────────────────┐    │
│    │  sync_status = 'pending'                                              │    │
│    │  next_sync_at = tomorrow at hour_bucket time                          │    │
│    │  consecutive_failures++ (across days)                                 │    │
│    │                                                                       │    │
│    │  If consecutive_failures >= 5:                                        │    │
│    │    is_active = False (requires manual review)                         │    │
│    └───────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Edge Cases and Mitigations

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  EDGE CASE ANALYSIS                                                             │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────┬────────────────────────┬─────────────────────────┐     │
│  │  EDGE CASE          │  IMPACT                │  MITIGATION             │     │
│  ├─────────────────────┼────────────────────────┼─────────────────────────┤     │
│  │  All 208 products   │  Need 31.5 min for     │  Still within 55 min    │     │
│  │  fail all 3 passes  │  3 full passes         │  hard stop. Defer to    │     │
│  │                     │                        │  next day.              │     │
│  ├─────────────────────┼────────────────────────┼─────────────────────────┤     │
│  │  Boeing API down    │  All products fail     │  Mark for next day,     │     │
│  │  entire hour        │  all passes            │  alert admin via        │     │
│  │                     │                        │  monitoring.            │     │
│  ├─────────────────────┼────────────────────────┼─────────────────────────┤     │
│  │  Worker crashes     │  Products stuck in     │  reset_stuck_syncing()  │     │
│  │  mid-processing     │  'syncing' status      │  runs before each       │     │
│  │                     │                        │  dispatcher cycle.      │     │
│  ├─────────────────────┼────────────────────────┼─────────────────────────┤     │
│  │  Processing exceeds │  May overlap with      │  Hard stop at minute    │     │
│  │  55 minutes         │  next hour's slot      │  55. Defer remaining    │     │
│  │                     │                        │  to next day.           │     │
│  ├─────────────────────┼────────────────────────┼─────────────────────────┤     │
│  │  Product fails 5    │  Needs manual review   │  is_active = False,     │     │
│  │  consecutive days   │                        │  admin alert, shows     │     │
│  │                     │                        │  in dashboard.          │     │
│  └─────────────────────┴────────────────────────┴─────────────────────────┘     │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Comparison: Inline Retry vs Unified next_sync_at

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  COMPARISON: PART 7B vs PART 7C                                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌────────────────────┬─────────────────────────┬─────────────────────────┐     │
│  │  ASPECT            │  UNIFIED next_sync_at   │  INLINE RETRY (7C)      │     │
│  │                    │  (Part 7B)              │                         │     │
│  ├────────────────────┼─────────────────────────┼─────────────────────────┤     │
│  │  Equal distribution│  ❌ No - retries mix    │  ✅ Yes - each slot     │     │
│  │                    │  with other slots       │  handles its own        │     │
│  ├────────────────────┼─────────────────────────┼─────────────────────────┤     │
│  │  Retry timing      │  Hours later (backoff)  │  Minutes later (same    │     │
│  │                    │                         │  hour)                  │     │
│  ├────────────────────┼─────────────────────────┼─────────────────────────┤     │
│  │  Transient error   │  Slow - wait hours      │  Fast - retry in 2 min  │     │
│  │  recovery          │                         │                         │     │
│  ├────────────────────┼─────────────────────────┼─────────────────────────┤     │
│  │  Rate limiting     │  Complex - retries may  │  Simple - single queue  │     │
│  │                    │  compete with scheduled │  per slot               │     │
│  ├────────────────────┼─────────────────────────┼─────────────────────────┤     │
│  │  Dispatcher query  │  Single global query    │  Query per hour_bucket  │     │
│  ├────────────────────┼─────────────────────────┼─────────────────────────┤     │
│  │  Complexity        │  Simpler (one query)    │  Slightly more (3 pass  │     │
│  │                    │                         │  logic)                 │     │
│  ├────────────────────┼─────────────────────────┼─────────────────────────┤     │
│  │  Best for          │  Low failure rates,     │  High failure rates,    │     │
│  │                    │  simplicity priority    │  equal load priority    │     │
│  └────────────────────┴─────────────────────────┴─────────────────────────┘     │
│                                                                                 │
│  RECOMMENDATION: Use Inline Retry (Part 7C) for equal distribution guarantee.  │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Implementation Parameters

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  RECOMMENDED CONFIGURATION                                                      │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────┬─────────────┬──────────────────────────────────┐   │
│  │  PARAMETER              │  VALUE      │  RATIONALE                       │   │
│  ├─────────────────────────┼─────────────┼──────────────────────────────────┤   │
│  │  Max passes per hour    │  3          │  Gives transient errors time to  │   │
│  │                         │             │  resolve without excessive load  │   │
│  ├─────────────────────────┼─────────────┼──────────────────────────────────┤   │
│  │  Wait between passes    │  2 minutes  │  Brief cooldown - Boeing might   │   │
│  │                         │             │  recover from rate limits        │   │
│  ├─────────────────────────┼─────────────┼──────────────────────────────────┤   │
│  │  Hard stop minute       │  55         │  5 min buffer before next hour   │   │
│  │                         │             │  to prevent overlap              │   │
│  ├─────────────────────────┼─────────────┼──────────────────────────────────┤   │
│  │  Max same-hour failures │  3          │  After 3 within-hour failures,   │   │
│  │                         │             │  defer to next day               │   │
│  ├─────────────────────────┼─────────────┼──────────────────────────────────┤   │
│  │  Max consecutive days   │  5          │  After 5 days failing, deactivate│   │
│  │                         │             │  (requires manual review)        │   │
│  ├─────────────────────────┼─────────────┼──────────────────────────────────┤   │
│  │  Status values          │  pending,   │  Track which pass a product      │   │
│  │                         │  syncing,   │  failed at                       │   │
│  │                         │  success,   │                                  │   │
│  │                         │  failed_p1, │                                  │   │
│  │                         │  failed_p2, │                                  │   │
│  │                         │  failed_p3  │                                  │   │
│  └─────────────────────────┴─────────────┴──────────────────────────────────┘   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 8: Scaling Analysis

### Current Design Capacity

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  SCALING LIMITS OF CURRENT DESIGN                                               │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  BOTTLENECK: Boeing API rate limit (2 calls/min × 10 SKUs = 20 SKUs/min)        │
│                                                                                 │
│  Daily capacity = 20 SKUs/min × 60 min × 24 hours = 28,800 SKUs                 │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  PRODUCT COUNT     HOURLY LOAD    UTILIZATION    STATUS                 │    │
│  │  ─────────────────────────────────────────────────────────────────────  │    │
│  │     1,000          42/hour         3.5%          ✅ Trivial             │    │
│  │     5,000         208/hour        17.3%          ✅ Comfortable         │    │
│  │    10,000         417/hour        34.7%          ✅ Moderate            │    │
│  │    20,000         833/hour        69.4%          ⚠️ High but OK         │    │
│  │    28,800       1,200/hour       100.0%          🔴 At capacity         │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  AT 5,000 PRODUCTS:                                                             │
│  • 17% utilization leaves 83% headroom for retries                              │
│  • Processing takes ~11 minutes per hour                                        │
│  • 49 minutes idle for error recovery                                           │
│  • Can handle 3× normal retries without falling behind                          │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Path to 10,000+ Products

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  IF YOU NEED TO SCALE BEYOND 10,000 PRODUCTS                                    │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  OPTION 1: NEGOTIATE HIGHER RATE LIMIT WITH BOEING                              │
│  ─────────────────────────────────────────────────────────────────────────────  │
│  • Current: 2 calls/min                                                         │
│  • If increased to 4 calls/min → capacity doubles to 57,600/day                 │
│  • No code changes needed (just update rate_limit config)                       │
│                                                                                 │
│  OPTION 2: REDUCE SYNC FREQUENCY                                                │
│  ─────────────────────────────────────────────────────────────────────────────  │
│  • Current: Every product syncs daily                                           │
│  • Alternative: Sync every 2 days → capacity doubles                            │
│  • Change: Modify next_sync_at calculation                                      │
│                                                                                 │
│  OPTION 3: PRIORITY-BASED SYNCING                                               │
│  ─────────────────────────────────────────────────────────────────────────────  │
│  • High-value products sync daily                                               │
│  • Low-value products sync every 3 days                                         │
│  • Add priority field to sync_schedule table                                    │
│                                                                                 │
│  OPTION 4: MULTIPLE BOEING ACCOUNTS (if allowed)                                │
│  ─────────────────────────────────────────────────────────────────────────────  │
│  • Each account has its own rate limit                                          │
│  • Run parallel workers with different credentials                              │
│  • Partition products across accounts                                           │
│                                                                                 │
│  FOR YOUR TARGET OF 5,000-10,000:                                               │
│  No changes needed. Current design handles this comfortably.                    │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 9: Incremental Product Addition

### How New Products Enter the System

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  PRODUCT LIFECYCLE: FROM ADDITION TO DAILY SYNC                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  WEEK 1: You add 100 products via bulk publish                                  │
│  ─────────────────────────────────────────────────────────────────────────────  │
│                                                                                 │
│  ┌────────────────────────────────────────────────────────────────────────┐     │
│  │  POST /api/bulk-publish                                                │     │
│  │  Body: ["SKU-001", "SKU-002", ... "SKU-100"]                           │     │
│  └────────────────────────────────────────────────────────────────────────┘     │
│                     │                                                           │
│                     ▼                                                           │
│  ┌────────────────────────────────────────────────────────────────────────┐     │
│  │  For each SKU:                                                         │     │
│  │  1. Fetch data from Boeing                                             │     │
│  │  2. Create product in Shopify                                          │     │
│  │  3. Save to local database                                             │     │
│  │  4. CREATE SYNC SCHEDULE ◀── Automatic entry into scheduler            │     │
│  │     • hour_bucket = hash(SKU) % 24                                     │     │
│  │     • next_sync_at = tomorrow at hour_bucket:00                        │     │
│  │     • sync_status = 'success'                                          │     │
│  └────────────────────────────────────────────────────────────────────────┘     │
│                                                                                 │
│  RESULT:                                                                        │
│  ┌────────────────────────────────────────────────────────────────────────┐     │
│  │  product_sync_schedule now has 100 entries                             │     │
│  │                                                                        │     │
│  │  Hour 00: ~4 products                                                  │     │
│  │  Hour 01: ~4 products                                                  │     │
│  │  Hour 02: ~5 products                                                  │     │
│  │  ...                                                                   │     │
│  │  Hour 23: ~4 products                                                  │     │
│  │                                                                        │     │
│  │  Products automatically distributed across 24 hours!                   │     │
│  └────────────────────────────────────────────────────────────────────────┘     │
│                                                                                 │
│  NEXT DAY: Scheduler picks them up automatically                                │
│  ─────────────────────────────────────────────────────────────────────────────  │
│  No manual action needed. Products sync at their assigned hours.                │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Gradual Growth Pattern

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  GROWTH OVER TIME: 500 PRODUCTS/WEEK FOR 10 WEEKS                               │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  WEEK     ADDED    TOTAL    PER HOUR    UTILIZATION                             │
│  ───────────────────────────────────────────────────────────────────────────    │
│    1       500       500      21           1.7%      ░░░░░░░░░░░░░░░░░░░░       │
│    2       500     1,000      42           3.5%      ░░░░░░░░░░░░░░░░░░░░       │
│    3       500     1,500      63           5.3%      █░░░░░░░░░░░░░░░░░░░       │
│    4       500     2,000      83           6.9%      █░░░░░░░░░░░░░░░░░░░       │
│    5       500     2,500     104           8.7%      █▌░░░░░░░░░░░░░░░░░░       │
│    6       500     3,000     125          10.4%      ██░░░░░░░░░░░░░░░░░░       │
│    7       500     3,500     146          12.2%      ██▌░░░░░░░░░░░░░░░░░       │
│    8       500     4,000     167          13.9%      ██▊░░░░░░░░░░░░░░░░░       │
│    9       500     4,500     188          15.7%      ███░░░░░░░░░░░░░░░░░       │
│   10       500     5,000     208          17.3%      ███▌░░░░░░░░░░░░░░░░       │
│                                                                                 │
│  THE SYSTEM ABSORBS NEW PRODUCTS AUTOMATICALLY:                                 │
│  • No reconfiguration needed                                                    │
│  • No rebalancing required                                                      │
│  • Hash distribution keeps hours balanced                                       │
│  • Utilization grows linearly and predictably                                   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 10: Change Detection Strategy

### Why Not Compare Raw Values?

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  WHY HASH-BASED CHANGE DETECTION?                                               │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  NAIVE APPROACH: Store and compare individual fields                            │
│  ─────────────────────────────────────────────────────────────────────────────  │
│  if (new_price != stored_price OR new_qty != stored_qty OR ...)                 │
│                                                                                 │
│  PROBLEMS:                                                                      │
│  • What if Boeing adds new fields? (Need to update comparison logic)            │
│  • Floating point comparison issues (150.00 vs 150.000001)                      │
│  • Multiple fields = multiple comparison operations                             │
│  • Hard to track "what changed" for debugging                                   │
│                                                                                 │
│  HASH APPROACH: One comparison, captures all relevant fields                    │
│  ─────────────────────────────────────────────────────────────────────────────  │
│  hash("150.00:50:true") = "a1b2c3d4..."                                         │
│  if (new_hash != stored_hash) → something changed                               │
│                                                                                 │
│  BENEFITS:                                                                      │
│  • Single comparison operation                                                  │
│  • Normalize values before hashing (handles float issues)                       │
│  • Easy to add new fields to hash                                               │
│  • Hash itself proves what was compared                                         │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### What Fields Matter?

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  FIELDS INCLUDED IN CHANGE DETECTION HASH                                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  INCLUDED (affect Shopify listing):                                             │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  price      →  Shopify price = Boeing price × 1.1                       │    │
│  │  quantity   →  Shopify inventory level                                  │    │
│  │  in_stock   →  Shopify availability (can be purchased or not)           │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  EXCLUDED (don't affect Shopify listing):                                       │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  description    →  Already set during initial publish                   │    │
│  │  weight         →  Doesn't change for same part number                  │    │
│  │  dimensions     →  Doesn't change for same part number                  │    │
│  │  images         →  Already uploaded during initial publish              │    │
│  │  lead_time      →  Not synced to Shopify                                │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  WHY THESE THREE?                                                               │
│  • They're the only fields that change AND matter for Shopify                   │
│  • Minimizes unnecessary Shopify API calls                                      │
│  • Price and availability are what customers see                                │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 11: Monitoring and Observability

### Key Metrics to Track

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  METRICS FOR LOW-MAINTENANCE OPERATION                                          │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  HEALTH INDICATORS (check daily):                                               │
│  ─────────────────────────────────────────────────────────────────────────────  │
│  • Products synced in last 24h vs total active                                  │
│    - Healthy: >95% synced                                                       │
│    - Warning: 80-95% synced                                                     │
│    - Alert: <80% synced                                                         │
│                                                                                 │
│  • Currently stuck in 'syncing' status                                          │
│    - Healthy: 0                                                                 │
│    - Warning: 1-10                                                              │
│    - Alert: >10                                                                 │
│                                                                                 │
│  • Products deactivated due to failures                                         │
│    - Healthy: <1% of total                                                      │
│    - Warning: 1-5% of total                                                     │
│    - Alert: >5% of total                                                        │
│                                                                                 │
│  OPERATIONAL METRICS (weekly review):                                           │
│  ─────────────────────────────────────────────────────────────────────────────  │
│  • Average sync duration per hour                                               │
│  • Number of Shopify updates (changes detected)                                 │
│  • Retry rate (failures that recovered)                                         │
│  • Queue depths (tasks waiting to be processed)                                 │
│                                                                                 │
│  THESE CAN BE QUERIED DIRECTLY FROM product_sync_schedule:                      │
│  ─────────────────────────────────────────────────────────────────────────────  │
│  • Total active:      SELECT COUNT(*) WHERE is_active = true                    │
│  • Synced today:      SELECT COUNT(*) WHERE last_sync_at > today                │
│  • Currently stuck:   SELECT COUNT(*) WHERE sync_status = 'syncing'             │
│                       AND last_sync_at < now() - interval '30 minutes'          │
│  • Deactivated:       SELECT COUNT(*) WHERE is_active = false                   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 12: Summary

### Design Principles Applied

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  PRINCIPLES THAT MAKE THIS LOW-MAINTENANCE                                      │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  1. DISTRIBUTE LOAD                                                             │
│     Problem: Processing everything at once creates bottlenecks                  │
│     Solution: Hash-based distribution across 24 hours                           │
│                                                                                 │
│  2. LOCK BEFORE PROCESSING                                                      │
│     Problem: Concurrent workers might double-process                            │
│     Solution: Status-based locking (syncing flag)                               │
│                                                                                 │
│  3. ENFORCE RATE LIMITS AT THE GATE                                             │
│     Problem: Easy to accidentally exceed API limits                             │
│     Solution: Single-worker queue with Celery rate_limit                        │
│                                                                                 │
│  4. SELF-HEAL FROM FAILURES                                                     │
│     Problem: Manual intervention for every failure                              │
│     Solution: Automatic retries, stuck job recovery, deactivation               │
│                                                                                 │
│  5. IDEMPOTENT OPERATIONS                                                       │
│     Problem: Duplicate processing causes inconsistency                          │
│     Solution: All operations are SET, not ADD                                   │
│                                                                                 │
│  6. DETECT CHANGES BEFORE ACTING                                                │
│     Problem: Unnecessary API calls waste resources                              │
│     Solution: Hash comparison before Shopify update                             │
│                                                                                 │
│  7. AUTOMATIC ONBOARDING                                                        │
│     Problem: Manual setup for each new product                                  │
│     Solution: Sync schedule created automatically on publish                    │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### What You Get

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  END RESULT: SET IT AND FORGET IT                                               │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ✅ 5,000 products synced daily with Boeing                                     │
│  ✅ Rate limits respected automatically                                         │
│  ✅ New products onboard automatically                                          │
│  ✅ Failures recover automatically                                              │
│  ✅ Bad products deactivate automatically                                       │
│  ✅ Shopify only updated when data actually changes                             │
│  ✅ Scales to 10,000+ without code changes                                      │
│  ✅ Minimal monitoring required (check metrics weekly)                          │
│                                                                                 │
│  MANUAL INTERVENTION ONLY FOR:                                                  │
│  • Reviewing deactivated products (why did they fail?)                          │
│  • Monitoring infrastructure (server health, not scheduler logic)               │
│  • Adding new features (not maintaining existing functionality)                 │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Appendix: Quick Reference

### Component Responsibilities

| Component | Responsibility |
|-----------|---------------|
| Celery Beat | Triggers hourly dispatch at HH:00 |
| Dispatcher | Queries products, acquires locks, creates batches |
| Boeing Worker | Calls Boeing API, detects changes |
| Shopify Worker | Updates Shopify when changes detected |
| PostgreSQL | Stores schedules, locks, history |
| Redis | Message broker for task queues |

### Database Table: product_sync_schedule

| Column | Purpose |
|--------|---------|
| hour_bucket | Which hour (0-23) this product syncs |
| next_sync_at | When next sync should happen |
| last_sync_at | When last sync completed |
| sync_status | Lock state (pending/syncing/success/failed) |
| last_boeing_hash | For change detection |
| consecutive_failures | For exponential backoff |
| is_active | Deactivated after 5 failures |

### Rate Limits

| API | Limit | Our Usage |
|-----|-------|-----------|
| Boeing | 2 calls/min | 2 calls/min (maxed) |
| Shopify | ~40 calls/min | ~5-10 calls/min (only on changes) |

### Capacity at Different Scales

| Products | Per Hour | Processing Time | Headroom |
|----------|----------|-----------------|----------|
| 1,000 | 42 | ~2 min | 96% |
| 5,000 | 208 | ~11 min | 83% |
| 10,000 | 417 | ~21 min | 65% |
| 20,000 | 833 | ~42 min | 31% |
