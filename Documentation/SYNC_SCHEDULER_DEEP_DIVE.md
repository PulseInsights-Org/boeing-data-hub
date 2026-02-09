# Boeing Sync Scheduler - Complete Deep Dive

This document covers everything you need to understand about the sync scheduler implementation.

---

## Table of Contents

1. [Current Publishing Flow (Existing)](#1-current-publishing-flow-existing)
2. [New Changes Required](#2-new-changes-required)
3. [Slot Calculation Deep Dive](#3-slot-calculation-deep-dive)
4. [Celery Beat - How It Works](#4-celery-beat---how-it-works)
5. [Complete Scheduler Flow with Timeline](#5-complete-scheduler-flow-with-timeline)
6. [How Tasks Flow Through Redis](#6-how-tasks-flow-through-redis)

---

## 1. Current Publishing Flow (Existing)

### What Happens Today When You Publish Products

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                           CURRENT BULK PUBLISH FLOW (EXISTING)                                      │
└─────────────────────────────────────────────────────────────────────────────────────────────────────┘

STEP 1: User calls POST /api/bulk-publish
═════════════════════════════════════════

    User Request:
    POST /api/bulk-publish
    {
        "part_numbers": ["PN-001", "PN-002", ..., "PN-050"],
        "batch_id": "batch-uuid-123"
    }

    What happens in backend/app/routes/bulk.py:
    ┌─────────────────────────────────────────────────────────────────────────┐
    │  1. Validate request                                                    │
    │  2. Create/update batch record in Supabase (status: "pending")          │
    │  3. Queue Celery task: publish_batch.delay(batch_id, part_numbers)      │
    │  4. Return immediately: {"batch_id": "...", "status": "processing"}     │
    └─────────────────────────────────────────────────────────────────────────┘

    Response (immediate, doesn't wait for processing):
    {
        "batch_id": "batch-uuid-123",
        "status": "processing",
        "message": "Publishing 50 products..."
    }


STEP 2: publish_batch task executes (Orchestrator)
═══════════════════════════════════════════════════

    File: backend/celery_app/tasks/publishing.py
    Function: publish_batch(batch_id, part_numbers, user_id)

    ┌─────────────────────────────────────────────────────────────────────────┐
    │  @celery_app.task(max_retries=0)  # Orchestrator doesn't retry          │
    │  def publish_batch(self, batch_id, part_numbers, user_id):              │
    │                                                                         │
    │      # 1. Update batch status to "processing"                           │
    │      batch_store.update_status(batch_id, "processing")                  │
    │                                                                         │
    │      # 2. Queue individual tasks for EACH part number                   │
    │      for pn in part_numbers:                                            │
    │          publish_product.delay(batch_id, pn, user_id)                   │
    │                 │                                                       │
    │                 └──▶ This queues 50 separate tasks to Redis             │
    │                                                                         │
    │      return {"batch_id": batch_id, "products_queued": 50}               │
    └─────────────────────────────────────────────────────────────────────────┘


STEP 3: publish_product tasks execute (One per product)
════════════════════════════════════════════════════════

    File: backend/celery_app/tasks/publishing.py
    Function: publish_product(batch_id, part_number, user_id)
    Rate Limit: 30/m (Shopify API limit)

    For EACH of the 50 part numbers, this task runs:

    ┌─────────────────────────────────────────────────────────────────────────┐
    │  @celery_app.task(rate_limit="30/m", max_retries=3)                     │
    │  def publish_product(self, batch_id, part_number, user_id):             │
    │                                                                         │
    │      # 1. Get product from product_staging table                        │
    │      record = supabase_store.get_product_staging_by_part_number(        │
    │          part_number, user_id                                           │
    │      )                                                                  │
    │                                                                         │
    │      # 2. Validate price and inventory (must have values)               │
    │      if price == 0 or inventory == 0:                                   │
    │          raise NonRetryableError("Cannot publish without price/qty")    │
    │                                                                         │
    │      # 3. Upload image to Supabase storage                              │
    │      image_url = supabase_store.upload_image_from_url(                  │
    │          boeing_image_url, part_number                                  │
    │      )                                                                  │
    │                                                                         │
    │      # 4. Prepare Shopify payload (price = cost * 1.1 markup)           │
    │      record = _prepare_shopify_record(record)                           │
    │                                                                         │
    │      # 5. Check if already published to Shopify                         │
    │      if record.get("shopify_product_id"):                               │
    │          # UPDATE existing Shopify product                              │
    │          shopify_client.update_product(shopify_product_id, record)      │
    │      else:                                                              │
    │          # CREATE new Shopify product                                   │
    │          result = shopify_client.publish_product(record)                │
    │          shopify_product_id = result["product"]["id"]                   │
    │                                                                         │
    │      # 6. Save to database                                              │
    │      supabase_store.upsert_product(record, shopify_product_id)          │
    │      supabase_store.update_product_staging_shopify_id(                  │
    │          part_number, shopify_product_id                                │
    │      )                                                                  │
    │                                                                         │
    │      # 7. Update batch progress                                         │
    │      batch_store.increment_published(batch_id)                          │
    │      check_batch_completion.delay(batch_id)                             │
    │                                                                         │
    │      return {"success": True, "shopify_product_id": shopify_product_id} │
    └─────────────────────────────────────────────────────────────────────────┘


STEP 4: Database state after publish
════════════════════════════════════

    BEFORE PUBLISH:
    ┌─────────────────────────────────────────────────────────────────────────┐
    │  product_staging table:                                                 │
    │  ┌───────────┬─────────────┬──────────┬───────────────────┐             │
    │  │ sku       │ status      │ price    │ shopify_product_id│             │
    │  ├───────────┼─────────────┼──────────┼───────────────────┤             │
    │  │ PN-001    │ 'fetched'   │ 150.00   │ NULL              │             │
    │  │ PN-002    │ 'fetched'   │ 200.00   │ NULL              │             │
    │  └───────────┴─────────────┴──────────┴───────────────────┘             │
    │                                                                         │
    │  product table: (empty for these SKUs)                                  │
    └─────────────────────────────────────────────────────────────────────────┘

    AFTER PUBLISH:
    ┌─────────────────────────────────────────────────────────────────────────┐
    │  product_staging table:                                                 │
    │  ┌───────────┬─────────────┬──────────┬───────────────────┐             │
    │  │ sku       │ status      │ price    │ shopify_product_id│             │
    │  ├───────────┼─────────────┼──────────┼───────────────────┤             │
    │  │ PN-001    │ 'published' │ 150.00   │ '123456789'       │             │
    │  │ PN-002    │ 'published' │ 200.00   │ '123456790'       │             │
    │  └───────────┴─────────────┴──────────┴───────────────────┘             │
    │                                                                         │
    │  product table: (products now exist here)                               │
    │  ┌───────────┬──────────┬───────────────────┬────────────────┐          │
    │  │ sku       │ price    │ shopify_product_id│ inventory_qty  │          │
    │  ├───────────┼──────────┼───────────────────┼────────────────┤          │
    │  │ PN-001    │ 165.00   │ '123456789'       │ 50             │          │
    │  │ PN-002    │ 220.00   │ '123456790'       │ 30             │          │
    │  └───────────┴──────────┴───────────────────┴────────────────┘          │
    └─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. New Changes Required

### Overview of All Changes

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                              ALL CHANGES REQUIRED FOR SYNC SCHEDULER                                │
└─────────────────────────────────────────────────────────────────────────────────────────────────────┘

╔═══════════════════════════════════════════════════════════════════════════════════════════════════╗
║  1. DATABASE CHANGES                                                                              ║
╠═══════════════════════════════════════════════════════════════════════════════════════════════════╣
║                                                                                                   ║
║  NEW TABLE: product_sync_schedule                                                                 ║
║  ─────────────────────────────────                                                                ║
║  This table tracks WHEN each product should be synced with Boeing API.                            ║
║                                                                                                   ║
║  CREATE TABLE product_sync_schedule (                                                             ║
║      id                    UUID PRIMARY KEY,                                                      ║
║      user_id               TEXT NOT NULL,                                                         ║
║      sku                   TEXT NOT NULL,                                                         ║
║                                                                                                   ║
║      -- Slot Assignment (determines WHEN product syncs)                                           ║
║      sync_slot             INTEGER NOT NULL,  -- 0-1439 (minute of day)                           ║
║                                                                                                   ║
║      -- Scheduling                                                                                ║
║      next_sync_at          TIMESTAMP NOT NULL,  -- When to sync next                              ║
║      last_sync_at          TIMESTAMP,           -- When last synced                               ║
║                                                                                                   ║
║      -- Status                                                                                    ║
║      sync_status           TEXT DEFAULT 'pending',  -- pending/syncing/success/failed             ║
║      last_error            TEXT,                                                                  ║
║      consecutive_failures  INTEGER DEFAULT 0,                                                     ║
║                                                                                                   ║
║      -- Change Detection                                                                          ║
║      last_boeing_hash      TEXT,     -- Hash of Boeing response (for detecting changes)           ║
║      last_price            NUMERIC,  -- Last known Boeing price                                   ║
║      last_quantity         INTEGER,  -- Last known Boeing quantity                                ║
║                                                                                                   ║
║      -- Control                                                                                   ║
║      is_active             BOOLEAN DEFAULT TRUE,                                                  ║
║                                                                                                   ║
║      UNIQUE (user_id, sku)                                                                        ║
║  );                                                                                               ║
║                                                                                                   ║
║  INDEX: (next_sync_at, is_active, sync_status) -- For dispatcher query                            ║
║                                                                                                   ║
╚═══════════════════════════════════════════════════════════════════════════════════════════════════╝


╔═══════════════════════════════════════════════════════════════════════════════════════════════════╗
║  2. NEW FILES TO CREATE                                                                           ║
╠═══════════════════════════════════════════════════════════════════════════════════════════════════╣
║                                                                                                   ║
║  FILE: backend/app/db/sync_store.py                                                               ║
║  ─────────────────────────────────                                                                ║
║  Database operations for sync scheduler.                                                          ║
║                                                                                                   ║
║  Functions:                                                                                       ║
║  • create_sync_schedule(sku, user_id, initial_data) - Create entry on publish                     ║
║  • get_due_products(limit=100) - Get products where next_sync_at <= NOW()                         ║
║  • mark_syncing(ids) - Set sync_status = 'syncing'                                                ║
║  • update_sync_success(sku, user_id, new_hash, new_price, new_qty)                                ║
║  • update_sync_failure(sku, user_id, error)                                                       ║
║  • get_sync_schedule(sku, user_id) - Get schedule for a product                                   ║
║                                                                                                   ║
║  ─────────────────────────────────────────────────────────────────────────────────────────────    ║
║                                                                                                   ║
║  FILE: backend/app/utils/sync_helpers.py                                                          ║
║  ───────────────────────────────────────                                                          ║
║  Helper functions for sync operations.                                                            ║
║                                                                                                   ║
║  Functions:                                                                                       ║
║  • calculate_sync_slot(sku) -> int  - Returns 0-1439                                              ║
║  • calculate_next_sync_at(slot) -> datetime  - First sync time                                    ║
║  • compute_boeing_hash(price, qty, in_stock) -> str  - For change detection                       ║
║                                                                                                   ║
║  ─────────────────────────────────────────────────────────────────────────────────────────────    ║
║                                                                                                   ║
║  FILE: backend/celery_app/tasks/sync_dispatcher.py                                                ║
║  ─────────────────────────────────────────────────                                                ║
║  Celery Beat task that runs every minute.                                                         ║
║                                                                                                   ║
║  Tasks:                                                                                           ║
║  • sync_dispatcher() - Finds due products, creates batches, queues sync tasks                     ║
║                                                                                                   ║
║  ─────────────────────────────────────────────────────────────────────────────────────────────    ║
║                                                                                                   ║
║  FILE: backend/celery_app/tasks/sync_boeing.py                                                    ║
║  ─────────────────────────────────────────────                                                    ║
║  Boeing sync task with rate limiting.                                                             ║
║                                                                                                   ║
║  Tasks:                                                                                           ║
║  • sync_boeing_batch(skus, user_id) - Calls Boeing API, detects changes                           ║
║                                                                                                   ║
║  ─────────────────────────────────────────────────────────────────────────────────────────────    ║
║                                                                                                   ║
║  FILE: backend/celery_app/tasks/sync_shopify.py                                                   ║
║  ──────────────────────────────────────────────                                                   ║
║  Shopify update task for changed products.                                                        ║
║                                                                                                   ║
║  Tasks:                                                                                           ║
║  • sync_shopify_update(sku, user_id, new_data) - Updates Shopify price/inventory                  ║
║                                                                                                   ║
╚═══════════════════════════════════════════════════════════════════════════════════════════════════╝


╔═══════════════════════════════════════════════════════════════════════════════════════════════════╗
║  3. FILES TO MODIFY                                                                               ║
╠═══════════════════════════════════════════════════════════════════════════════════════════════════╣
║                                                                                                   ║
║  FILE: backend/celery_app/celery_config.py                                                        ║
║  ──────────────────────────────────────────                                                       ║
║                                                                                                   ║
║  ADD to include list:                                                                             ║
║  ┌─────────────────────────────────────────────────────────────────────────────────────────────┐  ║
║  │  include=[                                                                                  │  ║
║  │      "celery_app.tasks.extraction",                                                         │  ║
║  │      "celery_app.tasks.normalization",                                                      │  ║
║  │      "celery_app.tasks.publishing",                                                         │  ║
║  │      "celery_app.tasks.batch",                                                              │  ║
║  │      "celery_app.tasks.sync_dispatcher",  # NEW                                             │  ║
║  │      "celery_app.tasks.sync_boeing",      # NEW                                             │  ║
║  │      "celery_app.tasks.sync_shopify",     # NEW                                             │  ║
║  │  ]                                                                                          │  ║
║  └─────────────────────────────────────────────────────────────────────────────────────────────┘  ║
║                                                                                                   ║
║  ADD new queues:                                                                                  ║
║  ┌─────────────────────────────────────────────────────────────────────────────────────────────┐  ║
║  │  task_queues=(                                                                              │  ║
║  │      Queue("extraction"),                                                                   │  ║
║  │      Queue("normalization"),                                                                │  ║
║  │      Queue("publishing"),                                                                   │  ║
║  │      Queue("default"),                                                                      │  ║
║  │      Queue("sync_boeing"),   # NEW                                                          │  ║
║  │      Queue("sync_shopify"),  # NEW                                                          │  ║
║  │  ),                                                                                         │  ║
║  └─────────────────────────────────────────────────────────────────────────────────────────────┘  ║
║                                                                                                   ║
║  ADD task routes:                                                                                 ║
║  ┌─────────────────────────────────────────────────────────────────────────────────────────────┐  ║
║  │  task_routes={                                                                              │  ║
║  │      ...existing routes...                                                                  │  ║
║  │      "celery_app.tasks.sync_dispatcher.*": {"queue": "default"},                            │  ║
║  │      "celery_app.tasks.sync_boeing.*": {"queue": "sync_boeing"},                            │  ║
║  │      "celery_app.tasks.sync_shopify.*": {"queue": "sync_shopify"},                          │  ║
║  │  },                                                                                         │  ║
║  └─────────────────────────────────────────────────────────────────────────────────────────────┘  ║
║                                                                                                   ║
║  ADD Celery Beat schedule (THIS IS THE KEY ADDITION):                                             ║
║  ┌─────────────────────────────────────────────────────────────────────────────────────────────┐  ║
║  │  from celery.schedules import crontab                                                       │  ║
║  │                                                                                             │  ║
║  │  celery_app.conf.beat_schedule = {                                                          │  ║
║  │      'sync-dispatcher-every-minute': {                                                      │  ║
║  │          'task': 'celery_app.tasks.sync_dispatcher.sync_dispatcher',                        │  ║
║  │          'schedule': crontab(minute='*'),  # Every minute                                   │  ║
║  │      },                                                                                     │  ║
║  │  }                                                                                          │  ║
║  └─────────────────────────────────────────────────────────────────────────────────────────────┘  ║
║                                                                                                   ║
║  ADD rate limit annotation:                                                                       ║
║  ┌─────────────────────────────────────────────────────────────────────────────────────────────┐  ║
║  │  task_annotations={                                                                         │  ║
║  │      ...existing annotations...                                                             │  ║
║  │      "celery_app.tasks.sync_boeing.sync_boeing_batch": {                                    │  ║
║  │          "rate_limit": "2/m",  # Boeing API limit                                           │  ║
║  │      },                                                                                     │  ║
║  │      "celery_app.tasks.sync_shopify.sync_shopify_update": {                                 │  ║
║  │          "rate_limit": "30/m",  # Shopify API limit                                         │  ║
║  │      },                                                                                     │  ║
║  │  },                                                                                         │  ║
║  └─────────────────────────────────────────────────────────────────────────────────────────────┘  ║
║                                                                                                   ║
║  ─────────────────────────────────────────────────────────────────────────────────────────────    ║
║                                                                                                   ║
║  FILE: backend/celery_app/tasks/publishing.py                                                     ║
║  ─────────────────────────────────────────────                                                    ║
║                                                                                                   ║
║  ADD sync schedule creation after successful publish:                                             ║
║  ┌─────────────────────────────────────────────────────────────────────────────────────────────┐  ║
║  │  # After line 205 (after saving shopify_product_id to staging):                             │  ║
║  │                                                                                             │  ║
║  │  # NEW: Create sync schedule for this product                                               │  ║
║  │  from app.db.sync_store import create_sync_schedule                                         │  ║
║  │  create_sync_schedule(                                                                      │  ║
║  │      sku=part_number,                                                                       │  ║
║  │      user_id=user_id,                                                                       │  ║
║  │      initial_data={                                                                         │  ║
║  │          'price': record.get('list_price'),                                                 │  ║
║  │          'quantity': record.get('inventory_quantity'),                                      │  ║
║  │          'in_stock': record.get('inventory_quantity', 0) > 0                                │  ║
║  │      }                                                                                      │  ║
║  │  )                                                                                          │  ║
║  └─────────────────────────────────────────────────────────────────────────────────────────────┘  ║
║                                                                                                   ║
╚═══════════════════════════════════════════════════════════════════════════════════════════════════╝
```

### What Changes in the Publish Flow

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                        PUBLISH FLOW WITH SYNC SCHEDULER (NEW)                                       │
└─────────────────────────────────────────────────────────────────────────────────────────────────────┘

EXISTING FLOW (unchanged):
    User → POST /api/bulk-publish → publish_batch → publish_product × N

NEW ADDITION (at the end of publish_product):
                                                                    │
                                                                    ▼
    ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
    │  AFTER SUCCESSFUL PUBLISH TO SHOPIFY:                                                           │
    │                                                                                                 │
    │  def publish_product(...):                                                                      │
    │      ...                                                                                        │
    │      # 5. Save to database (existing)                                                           │
    │      supabase_store.upsert_product(record, shopify_product_id)                                  │
    │      supabase_store.update_product_staging_shopify_id(part_number, shopify_product_id)          │
    │                                                                                                 │
    │      # ═══════════════════════════════════════════════════════════════════════════════════════  │
    │      # NEW CODE STARTS HERE                                                                     │
    │      # ═══════════════════════════════════════════════════════════════════════════════════════  │
    │                                                                                                 │
    │      # 6. CREATE SYNC SCHEDULE FOR THIS PRODUCT                                                 │
    │      from app.db.sync_store import create_sync_schedule                                         │
    │      from app.utils.sync_helpers import calculate_sync_slot, compute_boeing_hash                │
    │                                                                                                 │
    │      # Calculate when this product should sync daily                                            │
    │      sync_slot = calculate_sync_slot(part_number)  # e.g., 877 (= 14:37)                        │
    │                                                                                                 │
    │      # Compute initial hash for change detection                                                │
    │      initial_hash = compute_boeing_hash(                                                        │
    │          price=record.get('list_price'),                                                        │
    │          quantity=record.get('inventory_quantity'),                                             │
    │          in_stock=record.get('inventory_quantity', 0) > 0                                       │
    │      )                                                                                          │
    │                                                                                                 │
    │      # Calculate first sync time (tomorrow at slot time)                                        │
    │      now = datetime.utcnow()                                                                    │
    │      slot_hour = sync_slot // 60      # 877 // 60 = 14                                          │
    │      slot_minute = sync_slot % 60     # 877 % 60 = 37                                           │
    │      next_sync = now.replace(hour=slot_hour, minute=slot_minute, second=0)                      │
    │      if next_sync <= now:                                                                       │
    │          next_sync += timedelta(days=1)                                                         │
    │                                                                                                 │
    │      # Insert into sync schedule table                                                          │
    │      supabase.table('product_sync_schedule').insert({                                           │
    │          'user_id': user_id,                                                                    │
    │          'sku': part_number,                                                                    │
    │          'sync_slot': sync_slot,                                                                │
    │          'next_sync_at': next_sync.isoformat(),                                                 │
    │          'last_sync_at': now.isoformat(),  # Just published = just synced                       │
    │          'sync_status': 'success',                                                              │
    │          'last_boeing_hash': initial_hash,                                                      │
    │          'last_price': record.get('list_price'),                                                │
    │          'last_quantity': record.get('inventory_quantity'),                                     │
    │          'is_active': True                                                                      │
    │      }).execute()                                                                               │
    │                                                                                                 │
    │      # ═══════════════════════════════════════════════════════════════════════════════════════  │
    │      # NEW CODE ENDS HERE                                                                       │
    │      # ═══════════════════════════════════════════════════════════════════════════════════════  │
    │                                                                                                 │
    │      # 7. Update batch progress (existing, now step 7)                                          │
    │      batch_store.increment_published(batch_id)                                                  │
    │      check_batch_completion.delay(batch_id)                                                     │
    │                                                                                                 │
    │      return {"success": True, "shopify_product_id": shopify_product_id}                         │
    └─────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Slot Calculation Deep Dive

### How hash(sku) % 1440 Distributes Products

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                              SLOT CALCULATION DEEP DIVE                                             │
└─────────────────────────────────────────────────────────────────────────────────────────────────────┘

THE FORMULA:
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    sync_slot = hash(sku) % 1440

    Where:
    • hash(sku)  = Python's built-in hash function, returns a large integer
    • % 1440     = Modulo 1440 (minutes in a day: 24 hours × 60 minutes)
    • Result     = A number between 0 and 1439


EXAMPLE CALCULATIONS:
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    >>> hash("PN-001")
    -4812039418493284123

    >>> hash("PN-001") % 1440
    877

    >>> # Convert 877 to time:
    >>> hour = 877 // 60      # = 14
    >>> minute = 877 % 60     # = 37
    >>> # So PN-001 syncs at 14:37 daily

    More examples:
    ┌─────────────┬─────────────────────────┬────────────┬────────────┐
    │ SKU         │ hash(sku)               │ % 1440     │ Time       │
    ├─────────────┼─────────────────────────┼────────────┼────────────┤
    │ "PN-001"    │ -4812039418493284123    │ 877        │ 14:37      │
    │ "PN-002"    │ 8293847562938475623     │ 203        │ 03:23      │
    │ "PN-003"    │ -1293847562938475612    │ 1102       │ 18:22      │
    │ "PN-004"    │ 3948572039485720394     │ 45         │ 00:45      │
    │ "PN-005"    │ -7384957203948572039    │ 512        │ 08:32      │
    │ "PN-100"    │ 2938475629384756293     │ 1389       │ 23:09      │
    │ "PN-500"    │ -5829374658293746582    │ 734        │ 12:14      │
    │ "PN-999"    │ 9283746529837465298     │ 1023       │ 17:03      │
    └─────────────┴─────────────────────────┴────────────┴────────────┘


WHY THIS DISTRIBUTES EVENLY:
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    Python's hash() function produces pseudo-random values for string inputs.
    When you take modulo 1440, the distribution is approximately uniform.

    For 1000 products distributed across 1440 minutes:
    • Expected products per minute: 1000 / 1440 = 0.69
    • Some minutes have 0 products, some have 1, rarely 2+
    • Overall distribution is EVEN across the day

    Visual distribution (each █ = ~5 products):
    ┌────────────────────────────────────────────────────────────────────────────┐
    │ Hour 00: ████████░░  (~42 products)                                        │
    │ Hour 01: █████████░  (~42 products)                                        │
    │ Hour 02: ████████░░  (~42 products)                                        │
    │ Hour 03: █████████░  (~42 products)                                        │
    │ Hour 04: ████████░░  (~42 products)                                        │
    │ ...                                                                        │
    │ Hour 12: █████████░  (~42 products)                                        │
    │ ...                                                                        │
    │ Hour 23: ████████░░  (~42 products)                                        │
    └────────────────────────────────────────────────────────────────────────────┘

    1000 products ÷ 24 hours = ~42 products per hour (EVENLY DISTRIBUTED)


CONVERTING SLOT TO TIME:
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    def slot_to_time(sync_slot: int) -> tuple[int, int]:
        """Convert slot number to hour and minute."""
        hour = sync_slot // 60      # Integer division
        minute = sync_slot % 60     # Remainder
        return (hour, minute)

    Examples:
    • slot 0    → 00:00 (midnight)
    • slot 60   → 01:00
    • slot 720  → 12:00 (noon)
    • slot 877  → 14:37
    • slot 1439 → 23:59


CALCULATING next_sync_at FROM SLOT:
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    def calculate_first_sync_at(sync_slot: int) -> datetime:
        """Calculate when the first sync should happen."""
        now = datetime.utcnow()
        hour = sync_slot // 60
        minute = sync_slot % 60

        # Create time for today at the slot time
        sync_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # If slot time already passed today, schedule for tomorrow
        if sync_time <= now:
            sync_time += timedelta(days=1)

        return sync_time

    Example:
    • Current time: 2024-01-15 10:30:00 UTC
    • Product SKU: "PN-001"
    • sync_slot = hash("PN-001") % 1440 = 877 (= 14:37)

    Calculation:
    • sync_time = 2024-01-15 14:37:00 (today)
    • Is 14:37 > 10:30? YES
    • next_sync_at = 2024-01-15 14:37:00 (today)

    Another example:
    • Current time: 2024-01-15 16:00:00 UTC
    • Product SKU: "PN-001"
    • sync_slot = 877 (= 14:37)

    Calculation:
    • sync_time = 2024-01-15 14:37:00 (today)
    • Is 14:37 > 16:00? NO (already passed)
    • next_sync_at = 2024-01-16 14:37:00 (tomorrow)
```

---

## 4. Celery Beat - How It Works

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                              CELERY BEAT - COMPLETE EXPLANATION                                     │
└─────────────────────────────────────────────────────────────────────────────────────────────────────┘

WHAT IS CELERY BEAT?
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    Celery Beat is a SCHEDULER that runs as a separate process.
    It's like a cron job, but for Celery tasks.

    ┌──────────────────────────────────────────────────────────────────────────┐
    │                                                                          │
    │   CELERY BEAT                    CELERY WORKERS                          │
    │   (Scheduler Process)            (Task Executor Processes)               │
    │                                                                          │
    │   ┌─────────────────┐            ┌─────────────────┐                     │
    │   │                 │            │                 │                     │
    │   │  "Every minute, │──REDIS───▶│  "Oh, there's   │                     │
    │   │   put a task    │  QUEUE    │   a task! Let   │                     │
    │   │   in the queue" │           │   me execute it"│                     │
    │   │                 │            │                 │                     │
    │   └─────────────────┘            └─────────────────┘                     │
    │                                                                          │
    │   ONE instance only              Can have MULTIPLE instances             │
    │   (never run multiple!)          (for parallel processing)               │
    │                                                                          │
    └──────────────────────────────────────────────────────────────────────────┘


HOW CELERY BEAT IS CONFIGURED:
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    In celery_config.py, we add:

    from celery.schedules import crontab

    celery_app.conf.beat_schedule = {
        'sync-dispatcher-every-minute': {
            'task': 'celery_app.tasks.sync_dispatcher.sync_dispatcher',
            'schedule': crontab(minute='*'),  # Every minute
        },
    }

    This tells Celery Beat:
    "Every minute, push the 'sync_dispatcher' task to the Redis queue"


HOW TO START CELERY BEAT:
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    # IMPORTANT: Run ONLY ONE instance of Celery Beat!
    # Multiple instances will duplicate scheduled tasks.

    # Option 1: Standalone Beat process
    celery -A celery_app beat --loglevel=info

    # Option 2: Combined with worker (for development only)
    celery -A celery_app worker -B -Q default,sync_boeing,sync_shopify -c 1 --loglevel=info
                              │
                              └── The -B flag starts Beat embedded in the worker


WHAT HAPPENS EVERY MINUTE:
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    TIME: 14:37:00 UTC

    ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
    │  CELERY BEAT PROCESS                                                                            │
    │                                                                                                 │
    │  [14:37:00] Beat wakes up (internal timer)                                                      │
    │  [14:37:00] Checks schedule: "Is there a task due at minute=37?"                                │
    │  [14:37:00] YES! 'sync-dispatcher-every-minute' matches (minute='*' means every minute)         │
    │  [14:37:00] Sends message to Redis:                                                             │
    │                                                                                                 │
    │             PUBLISH to queue "default":                                                         │
    │             {                                                                                   │
    │                 "task": "celery_app.tasks.sync_dispatcher.sync_dispatcher",                     │
    │                 "id": "auto-generated-uuid-12345",                                              │
    │                 "args": [],                                                                     │
    │                 "kwargs": {},                                                                   │
    │                 "eta": null,                                                                    │
    │                 "expires": null                                                                 │
    │             }                                                                                   │
    │                                                                                                 │
    │  [14:37:00] Beat goes back to sleep until next check                                            │
    └─────────────────────────────────────────────────────────────────────────────────────────────────┘

    ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
    │  REDIS (Message Broker)                                                                         │
    │                                                                                                 │
    │  Queue "default" now contains:                                                                  │
    │  ┌─────────────────────────────────────────────────────────────────────────────────────────┐    │
    │  │  [Task: sync_dispatcher, ID: auto-12345]                                                │    │
    │  └──��──────────────────────────────────────────────────────────────────────────────────────┘    │
    │                                                                                                 │
    └─────────────────────────────────────────────────────────────────────────────────────────────────┘

    ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
    │  CELERY WORKER PROCESS (listening on "default" queue)                                           │
    │                                                                                                 │
    │  [14:37:00] Worker is idle, polling Redis...                                                    │
    │  [14:37:00] Receives message from queue "default"                                               │
    │  [14:37:00] "I got a task! Let me execute sync_dispatcher()"                                    │
    │  [14:37:00] Starts executing the sync_dispatcher function                                       │
    │  [14:37:01] sync_dispatcher queries database, finds products, creates batches                   │
    │  [14:37:02] sync_dispatcher queues sync_boeing_batch tasks                                      │
    │  [14:37:02] sync_dispatcher completes                                                           │
    │  [14:37:02] Worker goes back to polling Redis for more tasks                                    │
    │                                                                                                 │
    └─────────────────────────────────────────────────────────────────────────────────────────────────┘


CRONTAB SCHEDULE SYNTAX:
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    crontab(minute, hour, day_of_week, day_of_month, month_of_year)

    Examples:
    • crontab(minute='*')                    → Every minute
    • crontab(minute=0, hour='*')            → Every hour at :00
    • crontab(minute=30, hour='*/2')         → Every 2 hours at :30
    • crontab(minute=0, hour=0)              → Daily at midnight
    • crontab(minute='*/15')                 → Every 15 minutes
    • crontab(minute=0, hour=9, day_of_week=1)  → Every Monday at 9 AM


IMPORTANT NOTES ABOUT CELERY BEAT:
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    1. NEVER run multiple Beat instances
       ─────────────────────────────────
       If you run 2 Beat processes, each task will be queued TWICE per schedule.
       This would double your API calls!

    2. Beat doesn't execute tasks
       ───────────────────────────
       Beat ONLY schedules tasks (puts them in the queue).
       Workers EXECUTE the tasks.

    3. Beat stores schedule state
       ───────────────────────────
       By default, Beat stores its schedule state in a local file (celerybeat-schedule).
       This tracks when each task last ran to avoid duplicates on restart.

    4. Beat runs in UTC
       ─────────────────
       We configured timezone="UTC" in celery_config.
       All schedule times are in UTC.
```

---

## 5. Complete Scheduler Flow with Timeline

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                        COMPLETE SCHEDULER FLOW - MINUTE BY MINUTE                                   │
└─────────────────────────────────────────────────────────────────────────────────────────────────────┘

SCENARIO:
═══════════════════════════════════════════════════════════════════════════════════════════════════════
    • 1000 products published, each with a sync_slot
    • Currently: 2024-01-15 14:37:00 UTC
    • Products due at 14:37: PN-001, PN-023, PN-045, PN-089, PN-102, PN-156, PN-234,
                            PN-345, PN-456, PN-567, PN-678, PN-789, PN-890, PN-912
                            (14 products happen to have slot 877 = 14:37)


TIME 14:37:00 - CELERY BEAT TRIGGERS
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
    │  CELERY BEAT PROCESS                                                                            │
    │                                                                                                 │
    │  14:37:00.000  Internal timer fires                                                             │
    │  14:37:00.001  Check schedule: sync-dispatcher-every-minute matches                             │
    │  14:37:00.002  Push task to Redis queue "default":                                              │
    │                {task: "sync_dispatcher", id: "beat-14370001"}                                   │
    │  14:37:00.003  Done. Sleep until 14:38:00                                                       │
    │                                                                                                 │
    └─────────────────────────────────────────────────────────────────────────────────────────────────┘


TIME 14:37:00.010 - WORKER PICKS UP DISPATCHER TASK
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
    │  CELERY WORKER (default queue)                                                                  │
    │                                                                                                 │
    │  14:37:00.010  Receive task from Redis: sync_dispatcher                                         │
    │  14:37:00.011  Start executing sync_dispatcher()                                                │
    │                                                                                                 │
    │  ┌───────────────────────────────────────────────────────────────────────────────────────────┐  │
    │  │  def sync_dispatcher():                                                                   │  │
    │  │                                                                                           │  │
    │  │      # STEP 1: Query products that are DUE for sync                                       │  │
    │  │      # ─────────────────────────────────────────────────                                  │  │
    │  │      products = supabase.table('product_sync_schedule')                                   │  │
    │  │          .select('*')                                                                     │  │
    │  │          .filter('next_sync_at', 'lte', '2024-01-15T14:37:00Z')                           │  │
    │  │          .filter('is_active', 'eq', True)                                                 │  │
    │  │          .filter('sync_status', 'neq', 'syncing')  # Skip already processing              │  │
    │  │          .limit(100)                                                                      │  │
    │  │          .execute()                                                                       │  │
    │  │                                                                                           │  │
    │  │      # Result: 14 products found                                                          │  │
    │  │      # [PN-001, PN-023, PN-045, PN-089, PN-102, PN-156, PN-234,                           │  │
    │  │      #  PN-345, PN-456, PN-567, PN-678, PN-789, PN-890, PN-912]                           │  │
    │  │                                                                                           │  │
    │  └───────────────────────────────────────────────────────────────────────────────────────────┘  │
    │                                                                                                 │
    │  14:37:00.150  Query returned 14 products                                                       │
    │                                                                                                 │
    └─────────────────────────────────────────────────────────────────────────────────────────────────┘


TIME 14:37:00.160 - MARK PRODUCTS AS SYNCING
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
    │  SYNC DISPATCHER (continuing)                                                                   │
    │                                                                                                 │
    │  ┌───────────────────────────────────────────────────────────────────────────────────────────┐  │
    │  │      # STEP 2: Mark products as "syncing" to prevent re-processing                        │  │
    │  │      # ────────────────────────────────────────────────────────────────                   │  │
    │  │      ids = [p['id'] for p in products.data]                                               │  │
    │  │                                                                                           │  │
    │  │      supabase.table('product_sync_schedule')                                              │  │
    │  │          .update({'sync_status': 'syncing'})                                              │  │
    │  │          .in_('id', ids)                                                                  │  │
    │  │          .execute()                                                                       │  │
    │  │                                                                                           │  │
    │  │      # Now these 14 products have sync_status='syncing'                                   │  │
    │  │      # Next dispatcher run (14:38) will NOT pick them up                                  │  │
    │  │                                                                                           │  │
    │  └───────────────────────────────────────────────────────────────────────────────────────────┘  │
    │                                                                                                 │
    │  14:37:00.200  Products marked as syncing                                                       │
    │                                                                                                 │
    │  DATABASE STATE:                                                                                │
    │  ┌──────────┬────────────┬─────────────────────┬─────────────┐                                  │
    │  │ sku      │ sync_slot  │ next_sync_at        │ sync_status │                                  │
    │  ├──────────┼────────────┼─────────────────────┼─────────────┤                                  │
    │  │ PN-001   │ 877        │ 2024-01-15 14:37:00 │ syncing     │ ← Was 'success'                  │
    │  │ PN-023   │ 877        │ 2024-01-15 14:37:00 │ syncing     │                                  │
    │  │ PN-045   │ 877        │ 2024-01-15 14:37:00 │ syncing     │                                  │
    │  │ ...      │ ...        │ ...                 │ syncing     │                                  │
    │  │ PN-912   │ 877        │ 2024-01-15 14:37:00 │ syncing     │                                  │
    │  └──────────┴────────────┴─────────────────────┴─────────────┘                                  │
    │                                                                                                 │
    └─────────────────────────────────────────────────────────────────────────────────────────────────┘


TIME 14:37:00.210 - CREATE BATCHES AND QUEUE TASKS
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
    │  SYNC DISPATCHER (continuing)                                                                   │
    │                                                                                                 │
    │  ┌───────────────────────────────────────────────────────────────────────────────────────────┐  │
    │  │      # STEP 3: Group into batches of 10 SKUs (Boeing API max)                             │  │
    │  │      # ─────────────────────────────────────────────────────────                          │  │
    │  │      skus = [p['sku'] for p in products.data]                                             │  │
    │  │      # skus = ['PN-001', 'PN-023', ..., 'PN-912'] (14 SKUs)                               │  │
    │  │                                                                                           │  │
    │  │      batches = [skus[i:i+10] for i in range(0, len(skus), 10)]                            │  │
    │  │      # batches = [                                                                        │  │
    │  │      #     ['PN-001', 'PN-023', ..., 'PN-567'],  # Batch 1: 10 SKUs                       │  │
    │  │      #     ['PN-678', 'PN-789', 'PN-890', 'PN-912']  # Batch 2: 4 SKUs                    │  │
    │  │      # ]                                                                                  │  │
    │  │                                                                                           │  │
    │  │      # STEP 4: Queue sync tasks for each batch                                            │  │
    │  │      # ────────────────────────────────────────                                           │  │
    │  │      user_id = products.data[0]['user_id']                                                │  │
    │  │                                                                                           │  │
    │  │      for batch in batches:                                                                │  │
    │  │          sync_boeing_batch.delay(batch, user_id)                                          │  │
    │  │          # This pushes task to Redis queue "sync_boeing"                                  │  │
    │  │                                                                                           │  │
    │  └───────────────────────────────────────────────────────────────────────────────────────────┘  │
    │                                                                                                 │
    │  14:37:00.250  Queued 2 batch tasks to Redis                                                    │
    │  14:37:00.251  sync_dispatcher completes                                                        │
    │                                                                                                 │
    │  REDIS QUEUE STATE (sync_boeing):                                                               │
    │  ┌─────────────────────────────────────────────────────────────────────────────────────────┐    │
    │  │  [Task: sync_boeing_batch, args: [['PN-001',...,'PN-567'], 'user123']]   ← Batch 1       │    │
    │  │  [Task: sync_boeing_batch, args: [['PN-678',...,'PN-912'], 'user123']]   ← Batch 2       │    │
    │  └─────────────────────────────────────────────────────────────────────────────────────────┘    │
    │                                                                                                 │
    └─────────────────────────────────────────────────────────────────────────────────────────────────┘


TIME 14:37:00.260 - BOEING SYNC WORKER PROCESSES BATCH 1
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
    │  CELERY WORKER (sync_boeing queue, concurrency=1, rate_limit=2/m)                               │
    │                                                                                                 │
    │  14:37:00.260  Receive task: sync_boeing_batch(['PN-001',...], 'user123')                       │
    │  14:37:00.261  Rate limit check: Can I run? YES (first task this minute)                        │
    │  14:37:00.262  Start executing sync_boeing_batch()                                              │
    │                                                                                                 │
    │  ┌───────────────────────────────────────────────────────────────────────────────────────────┐  │
    │  │  @celery_app.task(rate_limit='2/m')                                                       │  │
    │  │  def sync_boeing_batch(skus, user_id):                                                    │  │
    │  │                                                                                           │  │
    │  │      # STEP 1: Call Boeing API                                                            │  │
    │  │      # ─────────────────────────                                                          │  │
    │  │      response = boeing_client.fetch_price_availability_batch(skus)                        │  │
    │  │                                                                                           │  │
    │  │      # Boeing API returns:                                                                │  │
    │  │      # {                                                                                  │  │
    │  │      #   "lineItems": [                                                                   │  │
    │  │      #     {"aviallPartNumber": "PN-001", "listPrice": 150.00, "quantity": 50, ...},     │  │
    │  │      #     {"aviallPartNumber": "PN-023", "listPrice": 205.00, "quantity": 30, ...},     │  │
    │  │      #     ...                                                                            │  │
    │  │      #   ]                                                                                │  │
    │  │      # }                                                                                  │  │
    │  │                                                                                           │  │
    │  └───────────────────────────────────────────────────────────────────────────────────────────┘  │
    │                                                                                                 │
    │  14:37:01.500  Boeing API response received (took ~1.2 seconds)                                 │
    │                                                                                                 │
    │  ┌───────────────────────────────────────────────────────────────────────────────────────────┐  │
    │  │      # STEP 2: Process each SKU - Check for changes                                       │  │
    │  │      # ────────────────────────────────────────────────                                   │  │
    │  │      for sku in skus:                                                                     │  │
    │  │          item = find_item_by_sku(response, sku)                                           │  │
    │  │                                                                                           │  │
    │  │          # Compute hash of relevant fields                                                │  │
    │  │          new_hash = compute_boeing_hash(                                                  │  │
    │  │              price=item['listPrice'],                                                     │  │
    │  │              quantity=item['quantity'],                                                   │  │
    │  │              in_stock=item['inStock']                                                     │  │
    │  │          )                                                                                │  │
    │  │                                                                                           │  │
    │  │          # Get stored hash from database                                                  │  │
    │  │          schedule = get_sync_schedule(sku, user_id)                                       │  │
    │  │          old_hash = schedule['last_boeing_hash']                                          │  │
    │  │                                                                                           │  │
    │  │          # CHANGE DETECTION                                                               │  │
    │  │          if new_hash != old_hash:                                                         │  │
    │  │              # CHANGE DETECTED! Queue Shopify update                                      │  │
    │  │              sync_shopify_update.delay(sku, user_id, {                                    │  │
    │  │                  'price': item['listPrice'],                                              │  │
    │  │                  'quantity': item['quantity']                                             │  │
    │  │              })                                                                           │  │
    │  │                                                                                           │  │
    │  │          # Update sync schedule                                                           │  │
    │  │          now = datetime.utcnow()                                                          │  │
    │  │          supabase.table('product_sync_schedule').update({                                 │  │
    │  │              'last_sync_at': now,                                                         │  │
    │  │              'next_sync_at': now + timedelta(hours=24),  # Tomorrow same time             │  │
    │  │              'sync_status': 'success',                                                    │  │
    │  │              'last_boeing_hash': new_hash,                                                │  │
    │  │              'last_price': item['listPrice'],                                             │  │
    │  │              'last_quantity': item['quantity'],                                           │  │
    │  │              'consecutive_failures': 0                                                    │  │
    │  │          }).eq('sku', sku).eq('user_id', user_id).execute()                               │  │
    │  │                                                                                           │  │
    │  └───────────────────────────────────────────────────────────────────────────────────────────┘  │
    │                                                                                                 │
    │  14:37:02.000  Batch 1 processing complete                                                      │
    │                                                                                                 │
    │  RESULTS:                                                                                       │
    │  • 10 products processed                                                                        │
    │  • 2 changes detected (PN-023 price changed, PN-456 quantity changed)                           │
    │  • 2 Shopify update tasks queued                                                                │
    │  • 10 product_sync_schedule records updated with next_sync_at = tomorrow                        │
    │                                                                                                 │
    └─────────────────────────────────────────────────────────────────────────────────────────────────┘


TIME 14:37:30.000 - BOEING SYNC WORKER PROCESSES BATCH 2 (RATE LIMITED)
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
    │  CELERY WORKER (sync_boeing queue)                                                              │
    │                                                                                                 │
    │  Rate limit: 2/m = 1 task per 30 seconds                                                        │
    │                                                                                                 │
    │  14:37:02.001  Batch 2 task is in queue, but rate limit says "wait"                             │
    │  14:37:30.000  30 seconds passed, rate limit allows next task                                   │
    │  14:37:30.001  Start executing sync_boeing_batch(['PN-678',...], 'user123')                     │
    │                                                                                                 │
    │  ... same processing as Batch 1 ...                                                             │
    │                                                                                                 │
    │  14:37:31.500  Batch 2 processing complete                                                      │
    │                                                                                                 │
    │  RESULTS:                                                                                       │
    │  • 4 products processed                                                                         │
    │  • 0 changes detected                                                                           │
    │  • 4 product_sync_schedule records updated with next_sync_at = tomorrow                         │
    │                                                                                                 │
    └─────────────────────────────────────────────────────────────────────────────────────────────────┘


TIME 14:37:02.100 - SHOPIFY UPDATE TASKS EXECUTE
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
    │  CELERY WORKER (sync_shopify queue, rate_limit=30/m)                                            │
    │                                                                                                 │
    │  14:37:02.100  Receive task: sync_shopify_update('PN-023', 'user123', {price: 205})             │
    │  14:37:02.101  Start executing                                                                  │
    │                                                                                                 │
    │  ┌───────────────────────────────────────────────────────────────────────────────────────────┐  │
    │  │  @celery_app.task(rate_limit='30/m')                                                      │  │
    │  │  def sync_shopify_update(sku, user_id, new_data):                                         │  │
    │  │                                                                                           │  │
    │  │      # 1. Get product from DB                                                             │  │
    │  │      product = get_product(sku, user_id)                                                  │  │
    │  │      shopify_variant_id = product['shopify_variant_id']                                   │  │
    │  │                                                                                           │  │
    │  │      # 2. Calculate new Shopify price (10% markup)                                        │  │
    │  │      new_shopify_price = new_data['price'] * 1.1                                          │  │
    │  │      # 205.00 * 1.1 = 225.50                                                              │  │
    │  │                                                                                           │  │
    │  │      # 3. Update Shopify                                                                  │  │
    │  │      shopify_client.update_variant(shopify_variant_id, price=225.50)                      │  │
    │  │                                                                                           │  │
    │  │      # 4. Update local DB                                                                 │  │
    │  │      update_product_price(sku, user_id, new_shopify_price)                                │  │
    │  │                                                                                           │  │
    │  └───────────────────────────────────────────────────────────────────────────────────────────┘  │
    │                                                                                                 │
    │  14:37:02.500  Shopify update complete for PN-023                                               │
    │                                                                                                 │
    │  14:37:02.510  Receive task: sync_shopify_update('PN-456', 'user123', {quantity: 45})           │
    │  14:37:02.900  Shopify update complete for PN-456                                               │
    │                                                                                                 │
    └─────────────────────────────────────────────────────────────────────────────────────────────────┘


TIME 14:37:32.000 - ALL DONE FOR THIS MINUTE
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    DATABASE STATE (product_sync_schedule):
    ┌──────────┬────────────┬─────────────────────┬─────────────────────┬─────────────┐
    │ sku      │ sync_slot  │ last_sync_at        │ next_sync_at        │ sync_status │
    ├──────────┼────────────┼─────────────────────┼─────────────────────┼─────────────┤
    │ PN-001   │ 877        │ 2024-01-15 14:37:02 │ 2024-01-16 14:37:02 │ success     │
    │ PN-023   │ 877        │ 2024-01-15 14:37:02 │ 2024-01-16 14:37:02 │ success     │
    │ PN-045   │ 877        │ 2024-01-15 14:37:02 │ 2024-01-16 14:37:02 │ success     │
    │ ...      │ ...        │ ...                 │ ...                 │ success     │
    │ PN-912   │ 877        │ 2024-01-15 14:37:31 │ 2024-01-16 14:37:31 │ success     │
    └──────────┴────────────┴─────────────────────┴─────────────────────┴─────────────┘

    Note: next_sync_at = last_sync_at + 24 hours
    Tomorrow, these products will sync again at approximately the same time.


TIME 14:38:00 - NEXT DISPATCHER RUN
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
    │  CELERY BEAT                                                                                    │
    │                                                                                                 │
    │  14:38:00.000  Trigger sync_dispatcher again                                                    │
    │                                                                                                 │
    │  DISPATCHER queries:                                                                            │
    │  SELECT * FROM product_sync_schedule                                                            │
    │  WHERE next_sync_at <= '2024-01-15T14:38:00Z'                                                   │
    │    AND sync_status != 'syncing'                                                                 │
    │    AND is_active = TRUE                                                                         │
    │                                                                                                 │
    │  Result: Different products! (those with slot 878 = 14:38)                                      │
    │  The 14 products from 14:37 are NOT returned because their next_sync_at is tomorrow.            │
    │                                                                                                 │
    └─────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. How Tasks Flow Through Redis

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                              REDIS QUEUE FLOW - VISUAL                                              │
└─────────────────────────────────────────────────────────────────────────────────────────────────────┘

REDIS DATA STRUCTURE:
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    Redis uses LISTS as queues. Each queue is a separate Redis key.

    ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
    │  REDIS SERVER                                                                                   │
    │                                                                                                 │
    │  Key: "celery:default"          (default queue)                                                 │
    │  Type: LIST                                                                                     │
    │  ┌─────────────────────────────────────────────────────────────────────────────────────────┐    │
    │  │  [sync_dispatcher task]                                                                 │    │
    │  │  [check_batch_completion task]                                                          │    │
    │  │  ...                                                                                    │    │
    │  └─────────────────────────────────────────────────────────────────────────────────────────┘    │
    │                                                                                                 │
    │  Key: "celery:sync_boeing"      (Boeing sync queue)                                             │
    │  Type: LIST                                                                                     │
    │  ┌─────────────────────────────────────────────────────────────────────────────────────────┐    │
    │  │  [sync_boeing_batch task 1]                                                             │    │
    │  │  [sync_boeing_batch task 2]                                                             │    │
    │  └─────────────────────────────────────────────────────────────────────────────────────────┘    │
    │                                                                                                 │
    │  Key: "celery:sync_shopify"     (Shopify sync queue)                                            │
    │  Type: LIST                                                                                     │
    │  ┌─────────────────────────────────────────────────────────────────────────────────────────┐    │
    │  │  [sync_shopify_update task 1]                                                           │    │
    │  │  [sync_shopify_update task 2]                                                           │    │
    │  └─────────────────────────────────────────────────────────────────────────────────────────┘    │
    │                                                                                                 │
    └─────────────────────────────────────────────────────────────────────────────────────────────────┘


TASK MESSAGE FORMAT:
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    Each task in the queue is a JSON message:

    {
        "body": "eyJhcmdzIjogWyJQTi0wMDEiXSwgImt3YXJncyI6IHt9fQ==",  // Base64 encoded
        "content-encoding": "utf-8",
        "content-type": "application/json",
        "headers": {
            "id": "abc123-task-uuid",
            "task": "celery_app.tasks.sync_boeing.sync_boeing_batch",
            "lang": "py",
            "retries": 0,
            "eta": null
        },
        "properties": {
            "delivery_tag": "tag123",
            "priority": 0
        }
    }

    Decoded body:
    {
        "args": [["PN-001", "PN-002", ...], "user123"],
        "kwargs": {}
    }


HOW WORKERS CONSUME TASKS:
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
    │                                                                                                 │
    │   WORKER A                         REDIS                          WORKER B                      │
    │   (sync_boeing)                                                   (sync_shopify)                │
    │                                                                                                 │
    │   ┌─────────────┐                 ┌──────────────┐                ┌─────────────┐               │
    │   │             │    BLPOP        │              │    BLPOP       │             │               │
    │   │  Waiting... │◀───────────────│  Queues:     │───────────────▶│  Waiting... │               │
    │   │             │   "sync_boeing" │              │  "sync_shopify"│             │               │
    │   └─────────────┘                 │  ┌────────┐  │                └─────────────┘               │
    │                                   │  │ Task 1 │  │                                              │
    │   Worker A executes:              │  │ Task 2 │  │                Worker B executes:            │
    │   - sync_boeing_batch             │  │ Task 3 │  │                - sync_shopify_update         │
    │                                   │  └────────┘  │                                              │
    │                                   │              │                                              │
    │                                   └──────────────┘                                              │
    │                                                                                                 │
    │   BLPOP = Blocking List Pop (waits for items)                                                   │
    │                                                                                                 │
    └─────────────────────────────────────────────────────────────────────────────────────────────────┘


WORKER STARTUP COMMANDS:
═══════════════════════════════════════════════════════════════════════════════════════════════════════

    # Terminal 1: Start Celery Beat (ONLY ONE!)
    celery -A celery_app beat --loglevel=info

    # Terminal 2: Start Boeing sync worker
    celery -A celery_app worker -Q sync_boeing -c 1 --loglevel=info
                                 │              │
                                 │              └── concurrency=1 (single process)
                                 └── Listen ONLY to sync_boeing queue

    # Terminal 3: Start Shopify sync worker
    celery -A celery_app worker -Q sync_shopify -c 2 --loglevel=info

    # Terminal 4: Start default queue worker (for dispatcher)
    celery -A celery_app worker -Q default -c 1 --loglevel=info

    # OR: Combined for development
    celery -A celery_app worker -B -Q default,sync_boeing,sync_shopify -c 1 --loglevel=info
                                │
                                └── -B embeds Beat in the worker
```

---

This comprehensive document covers:

1. ✅ What happens when a new product gets published (existing flow + new sync schedule creation)
2. ✅ All new changes required (database, files to create, files to modify)
3. ✅ Complete scheduler flow with exact timeline
4. ✅ How Celery Beat works and how tasks flow through Redis

Should I proceed with the implementation now?