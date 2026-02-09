# Boeing Sync Scheduler - Complete Data Flow

## Visual System Overview

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                    BOEING SYNC SCHEDULER v2                                        │
│                                                                                                    │
│  ┌─────────────────┐                                                                               │
│  │   CELERY BEAT   │ ─────────────────────────────────────────────────────────────────────────┐    │
│  │   (Scheduler)   │                                                                          │    │
│  └────────┬────────┘                                                                          │    │
│           │                                                                                   │    │
│           │ HH:00 (hourly)                                         */15 (every 15 min)        │    │
│           ▼                                                                 │                 │    │
│  ┌─────────────────────────────┐                              ┌─────────────▼───────────────┐ │    │
│  │   HOURLY BUCKET DISPATCHER  │                              │   CATCH-UP DISPATCHER       │ │    │
│  │                             │                              │   (Recovery)                │ │    │
│  │   Query: hour_bucket = 14   │                              │   Query: overdue > 30 min   │ │    │
│  │   FOR UPDATE SKIP LOCKED    │                              │   FOR UPDATE SKIP LOCKED    │ │    │
│  └─────────────┬───────────────┘                              └─────────────┬───────────────┘ │    │
│                │                                                            │                 │    │
│                │ ~42 products                                    Missed/stuck products        │    │
│                │                                                            │                 │    │
│                └────────────────────────┬───────────────────────────────────┘                 │    │
│                                         │                                                     │    │
│                                         ▼                                                     │    │
│                              ┌──────────────────────┐                                         │    │
│                              │   BATCH GROUPING     │                                         │    │
│                              │   (10 SKUs/batch)    │                                         │    │
│                              └──────────┬───────────┘                                         │    │
│                                         │                                                     │    │
│                                         │ Queue to sync_boeing                                │    │
│                                         ▼                                                     │    │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐     │    │
│  │                              REDIS QUEUE: sync_boeing                                 │     │    │
│  │   [Batch1: PN-001..010] [Batch2: PN-011..020] [Batch3: PN-021..030] [Batch4] [Batch5] │     │    │
│  └────────────────────────────────────────┬─────────────────────────────────────────────┘     │    │
│                                           │                                                   │    │
│                                           │                                                   │    │
│  ┌────────────────────────────────────────▼─────────────────────────────────────────────┐     │    │
│  │                         GLOBAL RATE LIMITER (Redis Token Bucket)                     │     │    │
│  │                                                                                      │     │    │
│  │   Tokens: [●][●]  (max 2)          Refill: 1 token / 30 seconds                      │     │    │
│  │                                                                                      │     │    │
│  │   14:00:00 → Batch1 takes token [●][ ]                                               │     │    │
│  │   14:00:00 → Batch2 takes token [ ][ ] (empty)                                       │     │    │
│  │   14:00:30 → Refill            [●][ ]                                                │     │    │
│  │   14:00:30 → Batch2 takes token [ ][ ]                                               │     │    │
│  │   14:01:00 → Refill + Batch3   [ ][ ]                                                │     │    │
│  │   ... and so on                                                                      │     │    │
│  └────────────────────────────────────────┬─────────────────────────────────────────────┘     │    │
│                                           │                                                   │    │
│                                           │ Rate: 2 calls/minute (global)                     │    │
│                                           ▼                                                   │    │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐     │    │
│  │                              SYNC BATCH WORKER                                        │     │    │
│  │                                                                                       │     │    │
│  │   1. Acquire rate limit token (wait if needed)                                        │     │    │
│  │   2. Call Boeing API with 10 SKUs                                                     │     │    │
│  │   3. For each SKU in response:                                                        │     │    │
│  │      - Compute SHA-256 hash of (price, qty, inStock)                                  │     │    │
│  │      - Compare with stored last_boeing_hash                                           │     │    │
│  │      - If changed → Queue Shopify update                                              │     │    │
│  │   4. Update product_sync_schedule                                                     │     │    │
│  │   5. Log to sync_audit_log                                                            │     │    │
│  └───────────────────────────┬──────────────────────────────────────┬────────────────────┘     │    │
│                              │                                      │                         │    │
│                              │ Boeing API                           │ If change detected      │    │
│                              ▼                                      ▼                         │    │
│  ┌───────────────────────────────────────┐    ┌─────────────────────────────────────────┐     │    │
│  │          BOEING API                   │    │        REDIS QUEUE: sync_shopify        │     │    │
│  │                                       │    │                                         │     │    │
│  │   POST /price-availability            │    │   [SKU: PN-015, old: $100, new: $120]   │     │    │
│  │   Body: { productCodes: [...10] }     │    │   [SKU: PN-023, old: qty 50, new: 45]   │     │    │
│  │                                       │    │                                         │     │    │
│  │   Response: prices, quantities,       │    │   Rate limit: 30/min (Celery native)    │     │    │
│  │             stock status, locations   │    │                                         │     │    │
│  └───────────────────────────────────────┘    └────────────────────┬────────────────────┘     │    │
│                                                                    │                         │    │
│                                                                    ▼                         │    │
│                                               ┌──────────────────────────────────────────┐    │    │
│                                               │       SHOPIFY SYNC WORKER                │    │    │
│                                               │                                          │    │    │
│                                               │   1. Get product (shopify_product_id)    │    │    │
│                                               │   2. Update price if changed             │    │    │
│                                               │   3. Update inventory if changed         │    │    │
│                                               │   4. Update local DB                     │    │    │
│                                               │   5. Log to audit                        │    │    │
│                                               └───────────────────────┬──────────────────┘    │    │
│                                                                       │                      │    │
│                                                                       ▼                      │    │
│                                               ┌──────────────────────────────────────────┐    │    │
│                                               │          SHOPIFY API                     │    │    │
│                                               │                                          │    │    │
│                                               │   PUT /variants/{id}                     │    │    │
│                                               │   POST /inventory_levels/set             │    │    │
│                                               └──────────────────────────────────────────┘    │    │
│                                                                                              │    │
│  ┌───────────────────────────────────────────────────────────────────────────────────────┐   │    │
│  │                                    SUPABASE DATABASE                                  │   │    │
│  │                                                                                       │   │    │
│  │   ┌─────────────────────────────────────────────────────────────────────────────┐     │   │    │
│  │   │  product_sync_schedule                                                      │     │   │    │
│  │   │                                                                             │     │   │    │
│  │   │  sku      │ hour_bucket │ next_sync_at        │ last_boeing_hash │ status   │     │   │    │
│  │   │  ─────────┼─────────────┼─────────────────────┼──────────────────┼───────── │     │   │    │
│  │   │  PN-001   │ 14          │ 2024-01-16 14:37:00 │ a1b2c3d4...      │ success  │     │   │    │
│  │   │  PN-015   │ 14          │ 2024-01-16 14:22:00 │ e5f6g7h8...      │ updated  │     │   │    │
│  │   │  PN-099   │ 03          │ 2024-01-16 03:45:00 │ i9j0k1l2...      │ pending  │     │   │    │
│  │   └─────────────────────────────────────────────────────────────────────────────┘     │   │    │
│  │                                                                                       │   │    │
│  │   ┌─────────────────────────────────────────────────────────────────────────────┐     │   │    │
│  │   │  sync_audit_log                                                             │     │   │    │
│  │   │                                                                             │     │   │    │
│  │   │  timestamp           │ sku    │ event_type      │ old_value │ new_value     │     │   │    │
│  │   │  ────────────────────┼────────┼─────────────────┼───────────┼────────────── │     │   │    │
│  │   │  2024-01-15 14:02:30 │ PN-015 │ price_changed   │ $100      │ $120          │     │   │    │
│  │   │  2024-01-15 14:02:31 │ PN-015 │ shopify_updated │ -         │ -             │     │   │    │
│  │   │  2024-01-15 14:02:25 │ PN-001 │ sync_completed  │ -         │ -             │     │   │    │
│  │   └─────────────────────────────────────────────────────────────────────────────┘     │   │    │
│  └───────────────────────────────────────────────────────────────────────────────────────┘   │    │
│                                                                                              │    │
└──────────────────────────────────────────────────────────────────────────────────────────────┘    │
                                                                                                    │
────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Detailed Step-by-Step Flow

### Step 1: Celery Beat Triggers Hourly Dispatcher

```
TIME: 14:00:00 UTC
EVENT: Celery Beat fires scheduled task

┌────────────────────────────────────────────────────────────────┐
│                    CELERY BEAT                                 │
│                                                                │
│   beat_schedule = {                                            │
│     'hourly-bucket-dispatch': {                                │
│       'task': 'dispatch_hourly_bucket',                        │
│       'schedule': crontab(minute=0, hour='*')                  │
│     }                                                          │
│   }                                                            │
│                                                                │
│   Action: dispatch_hourly_bucket.delay()                       │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
```

### Step 2: Hourly Dispatcher Queries Database

```
TIME: 14:00:01 UTC
EVENT: Dispatcher queries products in bucket 14

┌────────────────────────────────────────────────────────────────┐
│                HOURLY BUCKET DISPATCHER                        │
│                                                                │
│   current_hour = 14                                            │
│                                                                │
│   SQL (via stored procedure):                                  │
│   ┌──────────────────────────────────────────────────────────┐ │
│   │  SELECT * FROM product_sync_schedule                     │ │
│   │  WHERE hour_bucket = 14                                  │ │
│   │    AND next_sync_at <= '2024-01-15 14:00:00'             │ │
│   │    AND is_active = TRUE                                  │ │
│   │  ORDER BY sync_priority DESC, next_sync_at ASC           │ │
│   │  FOR UPDATE SKIP LOCKED  ← Key for preventing duplicates │ │
│   │  LIMIT 100                                               │ │
│   └──────────────────────────────────────────────────────────┘ │
│                                                                │
│   Result: 42 products returned                                 │
│                                                                │
│   Products are now LOCKED until transaction commits            │
│   (Other dispatchers will SKIP these rows)                     │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
```

### Step 3: Mark Products as "In Progress"

```
TIME: 14:00:02 UTC
EVENT: Update products to mark sync started

┌────────────────────────────────────────────────────────────────┐
│                UPDATE SYNC STARTED                             │
│                                                                │
│   SQL:                                                         │
│   ┌──────────────────────────────────────────────────────────┐ │
│   │  UPDATE product_sync_schedule                            │ │
│   │  SET last_sync_started_at = NOW()                        │ │
│   │  WHERE id IN (42 selected product IDs);                  │ │
│   │                                                          │ │
│   │  COMMIT;  ← Releases row locks                           │ │
│   └──────────────────────────────────────────────────────────┘ │
│                                                                │
│   Purpose:                                                     │
│   - Marks products so catch-up dispatcher knows they're active │
│   - Allows timeout detection (stuck > 10 minutes)              │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
```

### Step 4: Group into Batches

```
TIME: 14:00:02 UTC
EVENT: Create batches of 10 SKUs each

┌────────────────────────────────────────────────────────────────┐
│                    BATCH GROUPING                              │
│                                                                │
│   42 products → 5 batches:                                     │
│                                                                │
│   Batch 1: [PN-001, PN-002, PN-003, ..., PN-010]  (10 SKUs)   │
│   Batch 2: [PN-011, PN-012, PN-013, ..., PN-020]  (10 SKUs)   │
│   Batch 3: [PN-021, PN-022, PN-023, ..., PN-030]  (10 SKUs)   │
│   Batch 4: [PN-031, PN-032, PN-033, ..., PN-040]  (10 SKUs)   │
│   Batch 5: [PN-041, PN-042]                        (2 SKUs)   │
│                                                                │
│   Each batch includes:                                         │
│   - List of SKUs                                               │
│   - User ID                                                    │
│   - Last known hash (for change detection)                     │
│   - Last known price and quantity                              │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
```

### Step 5: Queue Batch Tasks

```
TIME: 14:00:02 UTC
EVENT: Push batches to Redis queue

┌────────────────────────────────────────────────────────────────┐
│                    QUEUE BATCH TASKS                           │
│                                                                │
│   For each batch:                                              │
│     sync_batch_task.apply_async(                               │
│       args=[batch_skus, user_id, batch_metadata],              │
│       queue='sync_boeing'                                      │
│     )                                                          │
│                                                                │
│   Redis Queue (sync_boeing):                                   │
│   ┌──────────────────────────────────────────────────────────┐ │
│   │  [Batch5] ← [Batch4] ← [Batch3] ← [Batch2] ← [Batch1]    │ │
│   │                                              (FIFO)       │ │
│   └──────────────────────────────────────────────────────────┘ │
│                                                                │
│   Log audit event:                                             │
│   INSERT INTO sync_audit_log (                                 │
│     event_type='dispatch_started',                             │
│     batch_id=...,                                              │
│     new_value='{"products": 42, "batches": 5}'                 │
│   )                                                            │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
```

### Step 6: Worker Acquires Rate Limit Token

```
TIME: 14:00:02 UTC (Batch 1)
TIME: 14:00:02 UTC (Batch 2 - waits)
EVENT: Workers compete for rate limit tokens

┌────────────────────────────────────────────────────────────────┐
│              GLOBAL RATE LIMITER (Redis Token Bucket)          │
│                                                                │
│   State at 14:00:02:                                           │
│   ┌──────────────────────────────────────────────────────────┐ │
│   │  Key: boeing_rate_limit                                  │ │
│   │  Tokens: 2.0                                             │ │
│   │  Last Refill: 14:00:00                                   │ │
│   └──────────────────────────────────────────────────────────┘ │
│                                                                │
│   Batch 1 Worker:                                              │
│   ┌──────────────────────────────────────────────────────────┐ │
│   │  1. Execute Lua script atomically:                       │ │
│   │     - elapsed = now - last_refill = 2 seconds            │ │
│   │     - tokens += elapsed * (1/30) = 2.0 + 0.067 = 2.067   │ │
│   │     - tokens = min(2, 2.067) = 2.0                       │ │
│   │     - tokens >= 1? YES                                   │ │
│   │     - tokens -= 1 → 1.0 remaining                        │ │
│   │     - Return SUCCESS                                     │ │
│   │                                                          │ │
│   │  2. Proceed to Boeing API call                           │ │
│   └──────────────────────────────────────────────────────────┘ │
│                                                                │
│   Batch 2 Worker (immediately after):                          │
│   ┌──────────────────────────────────────────────────────────┐ │
│   │  1. Execute Lua script:                                  │ │
│   │     - tokens = 1.0                                       │ │
│   │     - tokens >= 1? YES                                   │ │
│   │     - tokens -= 1 → 0.0 remaining                        │ │
│   │     - Return SUCCESS                                     │ │
│   │                                                          │ │
│   │  2. Proceed to Boeing API call                           │ │
│   └──────────────────────────────────────────────────────────┘ │
│                                                                │
│   Batch 3 Worker (immediately after):                          │
│   ┌──────────────────────────────────────────────────────────┐ │
│   │  1. Execute Lua script:                                  │ │
│   │     - tokens = 0.0                                       │ │
│   │     - tokens >= 1? NO                                    │ │
│   │     - Return WAIT                                        │ │
│   │                                                          │ │
│   │  2. Sleep 5 seconds, retry                               │ │
│   │  3. Repeat until token available or timeout              │ │
│   └──────────────────────────────────────────────────────────┘ │
│                                                                │
│   Token availability timeline:                                 │
│   14:00:02 → Batch 1 gets token (1 left)                       │
│   14:00:02 → Batch 2 gets token (0 left)                       │
│   14:00:30 → Refill (1 token)                                  │
│   14:00:30 → Batch 3 gets token (0 left)                       │
│   14:01:00 → Refill (1 token)                                  │
│   14:01:00 → Batch 4 gets token (0 left)                       │
│   14:01:30 → Refill (1 token)                                  │
│   14:01:30 → Batch 5 gets token (0 left)                       │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
```

### Step 7: Boeing API Call

```
TIME: 14:00:02 UTC (Batch 1)
EVENT: Call Boeing API with 10 SKUs

┌────────────────────────────────────────────────────────────────┐
│                    BOEING API CALL                             │
│                                                                │
│   Request:                                                     │
│   ┌──────────────────────────────────────────────────────────┐ │
│   │  POST https://boeing-api.example.com/price-availability  │ │
│   │                                                          │ │
│   │  Headers:                                                │ │
│   │    Authorization: Bearer {part_access_token}             │ │
│   │    Content-Type: application/json                        │ │
│   │                                                          │ │
│   │  Body:                                                   │ │
│   │  {                                                       │ │
│   │    "showNoStock": true,                                  │ │
│   │    "showLocation": true,                                 │ │
│   │    "productCodes": [                                     │ │
│   │      "PN-001", "PN-002", "PN-003", "PN-004", "PN-005",   │ │
│   │      "PN-006", "PN-007", "PN-008", "PN-009", "PN-010"    │ │
│   │    ]                                                     │ │
│   │  }                                                       │ │
│   └──────────────────────────────────────────────────────────┘ │
│                                                                │
│   Response:                                                    │
│   ┌──────────────────────────────────────────────────────────┐ │
│   │  {                                                       │ │
│   │    "currency": "USD",                                    │ │
│   │    "lineItems": [                                        │ │
│   │      {                                                   │ │
│   │        "aviallPartNumber": "PN-001",                     │ │
│   │        "listPrice": 150.00,                              │ │
│   │        "netPrice": 120.00,                               │ │
│   │        "quantity": 25,                                   │ │
│   │        "inStock": true,                                  │ │
│   │        ...                                               │ │
│   │      },                                                  │ │
│   │      ... (9 more items)                                  │ │
│   │    ]                                                     │ │
│   │  }                                                       │ │
│   └──────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
```

### Step 8: Change Detection (Per SKU)

```
TIME: 14:00:03 UTC
EVENT: Compare hashes for each SKU

┌────────────────────────────────────────────────────────────────┐
│                    CHANGE DETECTION                            │
│                                                                │
│   For each SKU in Boeing response:                             │
│                                                                │
│   ┌──────────────────────────────────────────────────────────┐ │
│   │  SKU: PN-001                                             │ │
│   │                                                          │ │
│   │  1. Extract relevant fields:                             │ │
│   │     relevant = {                                         │ │
│   │       "listPrice": 150.00,                               │ │
│   │       "netPrice": 120.00,                                │ │
│   │       "quantity": 25,                                    │ │
│   │       "inStock": true                                    │ │
│   │     }                                                    │ │
│   │                                                          │ │
│   │  2. Compute hash:                                        │ │
│   │     new_hash = SHA256(json.dumps(relevant, sort=True))   │ │
│   │     new_hash = "a1b2c3d4e5f6g7h8..."                     │ │
│   │                                                          │ │
│   │  3. Compare with stored hash:                            │ │
│   │     old_hash = "a1b2c3d4e5f6g7h8..." (from DB)           │ │
│   │                                                          │ │
│   │  4. Result: new_hash == old_hash                         │ │
│   │     → NO CHANGE                                          │ │
│   │     → Status: 'no_change'                                │ │
│   └──────────────────────────────────────────────────────────┘ │
│                                                                │
│   ┌──────────────────────────────────────────────────────────┐ │
│   │  SKU: PN-005                                             │ │
│   │                                                          │ │
│   │  1. Extract relevant fields:                             │ │
│   │     relevant = {                                         │ │
│   │       "listPrice": 200.00,    ← Was 180.00               │ │
│   │       "netPrice": 160.00,                                │ │
│   │       "quantity": 25,                                    │ │
│   │       "inStock": true                                    │ │
│   │     }                                                    │ │
│   │                                                          │ │
│   │  2. Compute hash:                                        │ │
│   │     new_hash = "x9y8z7w6v5u4..."                         │ │
│   │                                                          │ │
│   │  3. Compare with stored hash:                            │ │
│   │     old_hash = "m1n2o3p4q5r6..." (different!)            │ │
│   │                                                          │ │
│   │  4. Result: new_hash != old_hash                         │ │
│   │     → CHANGE DETECTED                                    │ │
│   │     → Identify change: price 180→200 (+11%)              │ │
│   │     → Queue Shopify update                               │ │
│   │     → Log to audit                                       │ │
│   │     → Status: 'updated'                                  │ │
│   └──────────────────────────────────────────────────────────┘ │
│                                                                │
│   Summary for Batch 1 (10 SKUs):                               │
│   - 8 products: no_change                                      │
│   - 1 product (PN-005): price changed                          │
│   - 1 product (PN-008): quantity changed                       │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
```

### Step 9: Update Database & Queue Shopify

```
TIME: 14:00:03 UTC
EVENT: Update sync schedule and queue Shopify updates

┌────────────────────────────────────────────────────────────────┐
│                    DATABASE UPDATES                            │
│                                                                │
│   For ALL products in batch (including no-change):             │
│   ┌──────────────────────────────────────────────────────────┐ │
│   │  UPDATE product_sync_schedule                            │ │
│   │  SET                                                     │ │
│   │    last_sync_at = NOW(),                                 │ │
│   │    next_sync_at = calculate_next_sync_at(                │ │
│   │                     hour_bucket, minute_offset),         │ │
│   │    last_sync_status = 'success' | 'updated' | 'no_change'│ │
│   │    last_boeing_hash = {new_hash},                        │ │
│   │    last_boeing_price = {new_price},                      │ │
│   │    last_boeing_quantity = {new_qty},                     │ │
│   │    last_boeing_in_stock = {in_stock},                    │ │
│   │    consecutive_failures = 0,                             │ │
│   │    last_sync_error = NULL                                │ │
│   │  WHERE sku = ?;                                          │ │
│   └──────────────────────────────────────────────────────────┘ │
│                                                                │
│   For CHANGED products only:                                   │
│   ┌──────────────────────────────────────────────────────────┐ │
│   │  -- Log the change                                       │ │
│   │  INSERT INTO sync_audit_log (                            │ │
│   │    user_id, sku, event_type,                             │ │
│   │    old_value, new_value                                  │ │
│   │  ) VALUES (                                              │ │
│   │    'user123', 'PN-005', 'price_changed',                 │ │
│   │    '{"price": 180.00}',                                  │ │
│   │    '{"price": 200.00}'                                   │ │
│   │  );                                                      │ │
│   │                                                          │ │
│   │  -- Queue Shopify update                                 │ │
│   │  sync_shopify_update.apply_async(                        │ │
│   │    args=['PN-005', old_data, new_data, user_id],         │ │
│   │    queue='sync_shopify'                                  │ │
│   │  );                                                      │ │
│   └──────────────────────────────────────────────────────────┘ │
│                                                                │
│   Anchored Scheduling (NO DRIFT):                              │
│   ┌──────────────────────────────────────────────────────────┐ │
│   │  Product PN-001:                                         │ │
│   │    hour_bucket = 14, minute_offset = 37                  │ │
│   │    Synced at: 14:00:03                                   │ │
│   │                                                          │ │
│   │  WRONG (v1): next_sync_at = 14:00:03 + 24h = 14:00:03    │ │
│   │              (Drifts 3 seconds per day!)                 │ │
│   │                                                          │ │
│   │  CORRECT (v2): next_sync_at = 2024-01-16 14:37:00        │ │
│   │                (Always anchored to slot time)            │ │
│   └──────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
```

### Step 10: Shopify Update (Changed Products Only)

```
TIME: 14:00:04 UTC
EVENT: Update Shopify for products with changes

┌────────────────────────────────────────────────────────────────┐
│                    SHOPIFY SYNC TASK                           │
│                                                                │
│   Input: PN-005, old_price=180, new_price=200                  │
│                                                                │
│   1. Get product from DB:                                      │
│   ┌──────────────────────────────────────────────────────────┐ │
│   │  SELECT shopify_product_id, shopify_variant_id           │ │
│   │  FROM product                                            │ │
│   │  WHERE sku = 'PN-005' AND user_id = 'user123';           │ │
│   │                                                          │ │
│   │  Result: product_id = 123456, variant_id = 789012        │ │
│   └──────────────────────────────────────────────────────────┘ │
│                                                                │
│   2. Calculate new Shopify price:                              │
│   ┌──────────────────────────────────────────────────────────┐ │
│   │  new_cost = 200.00 (Boeing list_price)                   │ │
│   │  new_shopify_price = new_cost * 1.1 = 220.00             │ │
│   │                                                          │ │
│   │  (Old: cost=180, price=198)                              │ │
│   │  (New: cost=200, price=220)                              │ │
│   └──────────────────────────────────────────────────────────┘ │
│                                                                │
│   3. Update Shopify:                                           │
│   ┌──────────────────────────────────────────────────────────┐ │
│   │  PUT /admin/api/2024-10/variants/789012.json             │ │
│   │  {                                                       │ │
│   │    "variant": {                                          │ │
│   │      "price": "220.00"                                   │ │
│   │    }                                                     │ │
│   │  }                                                       │ │
│   │                                                          │ │
│   │  (If inventory changed, also call inventory_levels/set)  │ │
│   └──────────────────────────────────────────────────────────┘ │
│                                                                │
│   4. Update local DB:                                          │
│   ┌──────────────────────────────────────────────────────────┐ │
│   │  UPDATE product                                          │ │
│   │  SET price = 220.00,                                     │ │
│   │      cost_per_item = 200.00,                             │ │
│   │      updated_at = NOW()                                  │ │
│   │  WHERE sku = 'PN-005';                                   │ │
│   │                                                          │ │
│   │  UPDATE product_staging SET ... WHERE sku = 'PN-005';    │ │
│   └──────────────────────────────────────────────────────────┘ │
│                                                                │
│   5. Log to audit:                                             │
│   ┌──────────────────────────────────────────────────────────┐ │
│   │  INSERT INTO sync_audit_log (                            │ │
│   │    event_type = 'shopify_updated',                       │ │
│   │    sku = 'PN-005',                                       │ │
│   │    old_value = '{"shopify_price": 198.00}',              │ │
│   │    new_value = '{"shopify_price": 220.00}'               │ │
│   │  );                                                      │ │
│   └──────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────┘
```

---

## Complete Timeline (Hour 14 Example)

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                        COMPLETE TIMELINE - HOUR 14                                      │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                         │
│  TIME        │ EVENT                                                                    │
│  ────────────┼───────────────────────────────────────────────────────────────────────── │
│  14:00:00    │ Celery Beat triggers dispatch_hourly_bucket(hour=14)                     │
│  14:00:01    │ Dispatcher queries: SELECT ... WHERE hour_bucket=14 FOR UPDATE SKIP LOCK │
│  14:00:01    │ Returns 42 products, rows are locked                                     │
│  14:00:02    │ UPDATE last_sync_started_at = NOW(), COMMIT (releases locks)             │
│  14:00:02    │ Create 5 batches, queue to sync_boeing                                   │
│              │                                                                          │
│  14:00:02    │ Batch 1 worker acquires token (2→1)                                      │
│  14:00:02    │ Batch 2 worker acquires token (1→0)                                      │
│  14:00:02    │ Batch 1 calls Boeing API (10 SKUs)                                       │
│  14:00:02    │ Batch 2 calls Boeing API (10 SKUs)                                       │
│  14:00:02    │ Batch 3 worker waits (no tokens)                                         │
│              │                                                                          │
│  14:00:03    │ Batch 1 response received, 1 change detected (PN-005 price)              │
│  14:00:03    │ Batch 2 response received, 1 change detected (PN-018 qty)                │
│  14:00:03    │ Update sync_schedule for 20 products                                     │
│  14:00:03    │ Queue 2 Shopify updates                                                  │
│              │                                                                          │
│  14:00:04    │ Shopify update for PN-005 (price: $198→$220)                             │
│  14:00:04    │ Shopify update for PN-018 (qty: 50→45)                                   │
│              │                                                                          │
│  14:00:30    │ Token refills (0→1)                                                      │
│  14:00:30    │ Batch 3 worker acquires token (1→0)                                      │
│  14:00:30    │ Batch 3 calls Boeing API (10 SKUs)                                       │
│              │                                                                          │
│  14:00:31    │ Batch 3 response, 0 changes detected                                     │
│  14:00:31    │ Update sync_schedule for 10 products                                     │
│              │                                                                          │
│  14:01:00    │ Token refills (0→1)                                                      │
│  14:01:00    │ Batch 4 worker acquires token                                            │
│  14:01:00    │ Batch 4 calls Boeing API (10 SKUs)                                       │
│              │                                                                          │
│  14:01:01    │ Batch 4 response, 1 change detected (PN-035 went out of stock)           │
│  14:01:01    │ Queue Shopify update for PN-035                                          │
│  14:01:02    │ Shopify update for PN-035 (inventory: 10→0)                              │
│              │                                                                          │
│  14:01:30    │ Token refills (0→1)                                                      │
│  14:01:30    │ Batch 5 worker acquires token                                            │
│  14:01:30    │ Batch 5 calls Boeing API (2 SKUs)                                        │
│              │                                                                          │
│  14:01:31    │ Batch 5 response, 0 changes detected                                     │
│  14:01:31    │ Update sync_schedule for 2 products                                      │
│              │                                                                          │
│  14:01:31    │ ══════════════════════════════════════════════════════════════════════  │
│              │ HOUR 14 SYNC COMPLETE                                                    │
│              │                                                                          │
│              │ Summary:                                                                 │
│              │ - 42 products synced                                                     │
│              │ - 5 Boeing API calls                                                     │
│              │ - 3 products had changes                                                 │
│              │ - 3 Shopify updates                                                      │
│              │ - Total time: ~1.5 minutes                                               │
│              │ - Rate limit: 2 calls/min maintained                                     │
│              │ ══════════════════════════════════════════════════════════════════════  │
│              │                                                                          │
│  14:15:00    │ Catch-up dispatcher runs                                                 │
│  14:15:00    │ Query: WHERE next_sync_at < NOW() - 30 min                               │
│  14:15:00    │ Returns 0 products (all processed on time)                               │
│  14:15:00    │ No action needed                                                         │
│              │                                                                          │
│  14:30:00    │ Catch-up dispatcher runs                                                 │
│  14:30:00    │ Finds 1 product (PN-099 from hour 13 that failed)                        │
│  14:30:01    │ Queues retry batch for PN-099                                            │
│  14:30:30    │ PN-099 synced successfully                                               │
│              │                                                                          │
│  15:00:00    │ Celery Beat triggers dispatch_hourly_bucket(hour=15)                     │
│  15:00:00    │ New hour begins...                                                       │
│                                                                                         │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Error Recovery Flow

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                              ERROR RECOVERY FLOW                                        │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                         │
│  SCENARIO: Boeing API returns error for PN-023                                          │
│                                                                                         │
│  ┌───────────────────────────────────────────────────────────────────────────────────┐  │
│  │  SYNC BATCH TASK                                                                  │  │
│  │                                                                                   │  │
│  │  Boeing Response:                                                                 │  │
│  │  {                                                                                │  │
│  │    "lineItems": [                                                                 │  │
│  │      { "aviallPartNumber": "PN-021", "listPrice": 100, ... },  // OK             │  │
│  │      { "aviallPartNumber": "PN-022", "listPrice": 150, ... },  // OK             │  │
│  │      { "aviallPartNumber": "PN-023", "error": "Part discontinued" }, // ERROR    │  │
│  │      { "aviallPartNumber": "PN-024", "listPrice": 200, ... },  // OK             │  │
│  │      ...                                                                          │  │
│  │    ]                                                                              │  │
│  │  }                                                                                │  │
│  └───────────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                         │
│  ┌───────────────────────────────────────────────────────────────────────────────────┐  │
│  │  FAILURE HANDLING FOR PN-023                                                      │  │
│  │                                                                                   │  │
│  │  1. Log failure:                                                                  │  │
│  │     INSERT INTO sync_audit_log (                                                  │  │
│  │       event_type = 'sync_failed',                                                 │  │
│  │       sku = 'PN-023',                                                             │  │
│  │       error_message = 'Part discontinued'                                         │  │
│  │     );                                                                            │  │
│  │                                                                                   │  │
│  │  2. Update sync schedule with backoff:                                            │  │
│  │     consecutive_failures = 1 (was 0)                                              │  │
│  │     backoff_hours = MIN(24, 2^1) = 2 hours                                        │  │
│  │     next_retry = NOW() + 2 hours = 16:00                                          │  │
│  │     next_slot = tomorrow 14:23 (anchored)                                         │  │
│  │     next_sync_at = LEAST(16:00, tomorrow 14:23) = 16:00                           │  │
│  │                                                                                   │  │
│  │     UPDATE product_sync_schedule                                                  │  │
│  │     SET last_sync_at = NOW(),                                                     │  │
│  │         next_sync_at = '2024-01-15 16:00:00',                                     │  │
│  │         last_sync_status = 'failed',                                              │  │
│  │         last_sync_error = 'Part discontinued',                                    │  │
│  │         consecutive_failures = 1                                                  │  │
│  │     WHERE sku = 'PN-023';                                                         │  │
│  │                                                                                   │  │
│  │  3. Continue processing other SKUs in batch normally                              │  │
│  └───────────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                         │
│  ┌───────────────────────────────────────────────────────────────────────────────────┐  │
│  │  RETRY AT 16:00 (Catch-up dispatcher)                                             │  │
│  │                                                                                   │  │
│  │  Catch-up query finds PN-023:                                                     │  │
│  │  WHERE next_sync_at < NOW() - 30 min  (16:00 - 30min = 15:30, so 14:00+2h=16:00) │  │
│  │                                                                                   │  │
│  │  If retry succeeds:                                                               │  │
│  │    - consecutive_failures = 0                                                     │  │
│  │    - next_sync_at = tomorrow at slot time                                         │  │
│  │                                                                                   │  │
│  │  If retry fails again:                                                            │  │
│  │    - consecutive_failures = 2                                                     │  │
│  │    - backoff_hours = MIN(24, 2^2) = 4 hours                                       │  │
│  │    - next_sync_at = 20:00                                                         │  │
│  └───────────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                         │
│  ┌───────────────────────────────────────────────────────────────────────────────────┐  │
│  │  AFTER 5 CONSECUTIVE FAILURES                                                     │  │
│  │                                                                                   │  │
│  │  If consecutive_failures >= 5:                                                    │  │
│  │                                                                                   │  │
│  │  UPDATE product_sync_schedule                                                     │  │
│  │  SET is_active = FALSE,                                                           │  │
│  │      last_sync_status = 'failed',                                                 │  │
│  │      last_sync_error = 'Deactivated after 5 failures: Part discontinued'          │  │
│  │  WHERE sku = 'PN-023';                                                            │  │
│  │                                                                                   │  │
│  │  INSERT INTO sync_audit_log (                                                     │  │
│  │    event_type = 'product_deactivated',                                            │  │
│  │    sku = 'PN-023',                                                                │  │
│  │    error_message = 'Deactivated after 5 consecutive failures'                     │  │
│  │  );                                                                               │  │
│  │                                                                                   │  │
│  │  → Send alert to admin                                                            │  │
│  │  → Product excluded from future syncs until manually reactivated                  │  │
│  └───────────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                         │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Key Metrics Summary

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                              KEY METRICS SUMMARY                                        │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                         │
│  THROUGHPUT:                                                                            │
│  ───────────                                                                            │
│  Products per day:           1,000                                                      │
│  Products per hour:          ~42 (1000 ÷ 24)                                            │
│  Batches per hour:           ~5 (42 ÷ 10)                                               │
│  Boeing API calls per hour:  ~5                                                         │
│  Boeing API calls per day:   ~100-120                                                   │
│                                                                                         │
│  TIMING:                                                                                │
│  ───────                                                                                │
│  Processing time per hour:   ~2.5 minutes                                               │
│  Idle time per hour:         ~57.5 minutes                                              │
│  Rate limit buffer:          ~90% headroom                                              │
│                                                                                         │
│  DATABASE:                                                                              │
│  ─────────                                                                              │
│  Dispatch queries per day:   ~120 (24 hourly + 96 catch-up)                             │
│  Update queries per day:     ~1,000 (one per product)                                   │
│  Audit log entries per day:  ~1,000-1,500                                               │
│                                                                                         │
│  RATE LIMITS:                                                                           │
│  ────────────                                                                           │
│  Boeing API:                 2 calls/min (global)                                       │
│  Shopify API:                30 calls/min (per worker)                                  │
│  Actual Boeing usage:        ~0.08 calls/min average (5 per hour)                       │
│  Actual Shopify usage:       ~5-10 calls/day (only changes)                             │
│                                                                                         │
│  EXPECTED CHANGES:                                                                      │
│  ─────────────────                                                                      │
│  Assuming 5% of products change daily:                                                  │
│  - ~50 price/qty changes per day                                                        │
│  - ~2 changes per hour                                                                  │
│  - ~50 Shopify updates per day                                                          │
│                                                                                         │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```
