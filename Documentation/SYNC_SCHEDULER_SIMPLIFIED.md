# Boeing Sync Scheduler - Simplified Implementation

## Quick Summary

A simple scheduler that syncs 1000 products from Boeing API daily, detecting price/availability changes and updating Shopify.

---

## Design Decisions (Simplified)

| Component | Approach | Rationale |
|-----------|----------|-----------|
| Slot Assignment | Python `hash(sku) % 1440` | Simple, works for single deployment |
| Dispatch | Every 1 minute (Celery Beat) | Simple polling, no complexity |
| Rate Limiting | Celery `rate_limit='2/m'` + `concurrency=1` | Native Celery, no Redis token bucket |
| Locking | Status-based (`sync_status='syncing'`) | Simple, single worker avoids conflicts |
| Scheduling | `next_sync_at = last_sync_at + 24h` | Prevents drift, simple calculation |

---

## Scheduling Logic: Why `last_sync_at + 24h` Works

```
Scenario: Product assigned to slot 14:37

First sync:
  - Dispatcher picks up at 14:37:00
  - Processing completes at 14:37:15
  - last_sync_at = 14:37:15
  - next_sync_at = 14:37:15 + 24h = Tomorrow 14:37:15

Second sync (next day):
  - Dispatcher picks up at 14:37:15
  - Processing completes at 14:37:18
  - last_sync_at = 14:37:18
  - next_sync_at = 14:37:18 + 24h = Day after 14:37:18

Drift Analysis:
  - Drift = queue processing time (~seconds)
  - Bounded, doesn't accumulate exponentially
  - ACCEPTABLE for daily sync use case
```

**For failed syncs:** `next_sync_at = NOW() + backoff_hours`

---

## Architecture

```
┌─────────────┐     ┌─────────────────────┐     ┌─────────────────┐
│ CELERY BEAT │────▶│   SYNC DISPATCHER   │────▶│  REDIS QUEUE    │
│ (every min) │     │                     │     │  sync_boeing    │
└─────────────┘     │ 1. Query due prods  │     └────────┬────────┘
                    │ 2. Mark 'syncing'   │              │
                    │ 3. Batch (10 SKUs)  │              ▼
                    │ 4. Queue tasks      │     ┌─────────────────┐
                    └─────────────────────┘     │  SYNC WORKER    │
                                                │  concurrency=1  │
                                                │  rate_limit=2/m │
                                                └────────┬────────┘
                                                         │
                    ┌────────────────────────────────────┼────────────────────────────────────┐
                    │                                    │                                    │
                    ▼                                    ▼                                    ▼
           ┌─────────────────┐              ┌─────────────────┐              ┌─────────────────┐
           │  BOEING API     │              │ CHANGE DETECT   │              │  SUPABASE DB    │
           │  (10 SKUs/call) │──────────────│ hash(response)  │──────────────│  Update status  │
           └─────────────────┘              │ vs stored hash  │              │  next_sync_at   │
                                            └────────┬────────┘              └─────────────────┘
                                                     │
                                                     │ If changed
                                                     ▼
                                            ┌─────────────────┐
                                            │ SHOPIFY UPDATE  │
                                            │ rate_limit=30/m │
                                            └─────────────────┘
```

---

## Database Schema

### Table: `product_sync_schedule`

```sql
CREATE TABLE product_sync_schedule (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    sku TEXT NOT NULL,

    -- Scheduling
    sync_slot INTEGER NOT NULL,  -- 0-1439 (minute of day)
    next_sync_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_sync_at TIMESTAMP WITH TIME ZONE,

    -- Status
    sync_status TEXT DEFAULT 'pending',  -- pending, syncing, success, failed
    last_error TEXT,
    consecutive_failures INTEGER DEFAULT 0,

    -- Change Detection
    last_boeing_hash TEXT,
    last_price NUMERIC,
    last_quantity INTEGER,

    -- Control
    is_active BOOLEAN DEFAULT TRUE,

    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    CONSTRAINT sync_schedule_user_sku_unique UNIQUE (user_id, sku)
);

-- Index for dispatcher query
CREATE INDEX idx_sync_schedule_dispatch
    ON product_sync_schedule (next_sync_at, is_active, sync_status)
    WHERE is_active = TRUE AND sync_status != 'syncing';
```

---

## Task Flow

### 1. Dispatcher Task (Every Minute)

```python
@celery_app.task
def sync_dispatcher():
    """Runs every minute via Celery Beat"""

    # 1. Query due products
    products = supabase.table('product_sync_schedule').select('*').filter(
        'next_sync_at', 'lte', datetime.utcnow().isoformat()
    ).filter(
        'is_active', 'eq', True
    ).filter(
        'sync_status', 'neq', 'syncing'
    ).limit(100).execute()

    if not products.data:
        return

    # 2. Mark as syncing
    ids = [p['id'] for p in products.data]
    supabase.table('product_sync_schedule').update({
        'sync_status': 'syncing'
    }).in_('id', ids).execute()

    # 3. Group into batches of 10
    skus = [p['sku'] for p in products.data]
    batches = [skus[i:i+10] for i in range(0, len(skus), 10)]

    # 4. Queue sync tasks
    for batch in batches:
        sync_boeing_batch.delay(batch, user_id)
```

### 2. Boeing Sync Task

```python
@celery_app.task(
    bind=True,
    rate_limit='2/m',  # Celery native rate limiting
    max_retries=3
)
def sync_boeing_batch(self, skus, user_id):
    """Sync batch of SKUs with Boeing API"""

    # 1. Call Boeing API
    response = boeing_client.fetch_price_availability_batch(skus)

    # 2. Process each SKU
    for sku in skus:
        item = find_in_response(response, sku)

        if item is None:
            mark_failed(sku, user_id, "Not found in Boeing response")
            continue

        # 3. Change detection
        new_hash = compute_hash(item['listPrice'], item['quantity'], item['inStock'])
        old_hash = get_stored_hash(sku, user_id)

        if new_hash != old_hash:
            # Change detected - queue Shopify update
            sync_shopify_update.delay(sku, user_id, {
                'price': item['listPrice'],
                'quantity': item['quantity']
            })

        # 4. Update schedule
        now = datetime.utcnow()
        supabase.table('product_sync_schedule').update({
            'last_sync_at': now.isoformat(),
            'next_sync_at': (now + timedelta(hours=24)).isoformat(),
            'sync_status': 'success',
            'last_boeing_hash': new_hash,
            'last_price': item['listPrice'],
            'last_quantity': item['quantity'],
            'consecutive_failures': 0,
            'last_error': None
        }).eq('sku', sku).eq('user_id', user_id).execute()
```

### 3. Shopify Update Task

```python
@celery_app.task(
    bind=True,
    rate_limit='30/m',
    max_retries=3
)
def sync_shopify_update(self, sku, user_id, new_data):
    """Update Shopify when change detected"""

    # 1. Get product from DB
    product = get_product(sku, user_id)

    # 2. Calculate new Shopify price
    new_price = new_data['price'] * 1.1  # 10% markup

    # 3. Update Shopify
    if product.get('shopify_variant_id'):
        shopify_client.update_variant(
            product['shopify_variant_id'],
            price=new_price
        )

        if 'quantity' in new_data:
            shopify_client.set_inventory_level(
                product['shopify_inventory_item_id'],
                new_data['quantity']
            )

    # 4. Update local DB
    update_product_price(sku, user_id, new_price, new_data['quantity'])
```

---

## Celery Configuration

```python
# celery_config.py additions

# Beat Schedule
beat_schedule = {
    'sync-dispatcher-every-minute': {
        'task': 'celery_app.tasks.sync_dispatcher.sync_dispatcher',
        'schedule': crontab(minute='*'),  # Every minute
    },
}

# Task Queues
task_queues = (
    Queue('default', routing_key='default'),
    Queue('extraction', routing_key='extraction'),
    Queue('normalization', routing_key='normalization'),
    Queue('publishing', routing_key='publishing'),
    Queue('sync_boeing', routing_key='sync.boeing'),      # NEW
    Queue('sync_shopify', routing_key='sync.shopify'),    # NEW
)

# Task Routes
task_routes = {
    'celery_app.tasks.sync_dispatcher.*': {'queue': 'default'},
    'celery_app.tasks.sync_boeing.*': {'queue': 'sync_boeing'},
    'celery_app.tasks.sync_shopify.*': {'queue': 'sync_shopify'},
}
```

---

## Worker Commands

```bash
# Start Celery Beat (scheduler) - ONLY ONE INSTANCE
celery -A celery_app beat --loglevel=info

# Start Boeing sync worker (concurrency=1 for rate limiting)
celery -A celery_app worker -Q sync_boeing -c 1 --hostname=boeing_sync@%h

# Start Shopify sync worker
celery -A celery_app worker -Q sync_shopify -c 2 --hostname=shopify_sync@%h

# Combined for development
celery -A celery_app worker -Q default,sync_boeing,sync_shopify -c 1 -B --loglevel=info
```

---

## Files to Create

```
backend/
├── app/
│   ├── db/
│   │   └── sync_store.py           # DB operations for sync
│   ├── utils/
│   │   └── sync_helpers.py         # Hash calculation, slot assignment
│   └── routes/
│       └── sync.py                 # Optional: API endpoints
│
├── celery_app/
│   ├── celery_config.py            # UPDATE: Add beat schedule
│   └── tasks/
│       ├── sync_dispatcher.py      # NEW: Dispatcher task
│       ├── sync_boeing.py          # NEW: Boeing sync task
│       ├── sync_shopify.py         # NEW: Shopify update task
│       └── publishing.py           # UPDATE: Create schedule on publish
│
└── scripts/
    └── backfill_sync_schedule.py   # Backfill existing products

database/
└── migration_005_sync_scheduler.sql
```

---

## Key Numbers

| Metric | Value |
|--------|-------|
| Total products | 1,000 |
| Boeing batch size | 10 SKUs/call |
| Boeing rate limit | 2 calls/min = 20 SKUs/min |
| Time to sync all | 1000 ÷ 20 = 50 minutes |
| Distributed over | 24 hours (~42 products/hour) |
| Dispatcher frequency | Every 1 minute |
| Expected changes/day | ~50 (5% of products) |

---

## Error Handling

```python
def mark_failed(sku, user_id, error):
    """Handle sync failure with exponential backoff"""

    # Get current failure count
    schedule = get_schedule(sku, user_id)
    failures = schedule['consecutive_failures'] + 1

    # Calculate backoff (2, 4, 8, 16, 24 hours max)
    backoff_hours = min(24, 2 ** failures)
    next_sync = datetime.utcnow() + timedelta(hours=backoff_hours)

    # Deactivate after 5 failures
    is_active = failures < 5

    supabase.table('product_sync_schedule').update({
        'sync_status': 'failed',
        'last_error': error,
        'consecutive_failures': failures,
        'next_sync_at': next_sync.isoformat(),
        'is_active': is_active
    }).eq('sku', sku).eq('user_id', user_id).execute()
```

---

## Integration with Publish Flow

After a product is published to Shopify, create its sync schedule:

```python
# In publishing.py, after successful publish

def create_sync_schedule(sku, user_id, initial_data):
    """Create sync schedule entry for newly published product"""

    # Calculate slot using Python hash
    sync_slot = hash(sku) % 1440

    # Calculate first sync time (tomorrow at slot time)
    now = datetime.utcnow()
    slot_hour = sync_slot // 60
    slot_minute = sync_slot % 60

    next_sync = now.replace(hour=slot_hour, minute=slot_minute, second=0)
    if next_sync <= now:
        next_sync += timedelta(days=1)

    # Create schedule entry
    supabase.table('product_sync_schedule').insert({
        'user_id': user_id,
        'sku': sku,
        'sync_slot': sync_slot,
        'next_sync_at': next_sync.isoformat(),
        'last_sync_at': now.isoformat(),
        'sync_status': 'success',  # Just published = synced
        'last_boeing_hash': compute_hash(initial_data),
        'last_price': initial_data.get('list_price'),
        'last_quantity': initial_data.get('inventory_quantity'),
        'is_active': True
    }).execute()
```

---

## What We're NOT Doing (Simplifications)

| Removed Feature | Reason |
|-----------------|--------|
| SHA-256 stable hashing | Python hash() is simpler, acceptable for single deployment |
| Hourly bucket dispatch | Every-minute polling is simpler, DB handles it fine |
| Global Redis token bucket | Celery native rate_limit + concurrency=1 works |
| FOR UPDATE SKIP LOCKED | Status-based locking + single worker avoids conflicts |
| Anchored slot scheduling | last_sync_at + 24h is simpler, drift is minimal |
| Separate catch-up dispatcher | Every-minute dispatcher handles recovery naturally |
| Complex audit logging | Basic status tracking, can add later if needed |

---

## Implementation Checklist

- [ ] Create `database/migration_005_sync_scheduler.sql`
- [ ] Create `backend/app/db/sync_store.py`
- [ ] Create `backend/app/utils/sync_helpers.py`
- [ ] Create `backend/celery_app/tasks/sync_dispatcher.py`
- [ ] Create `backend/celery_app/tasks/sync_boeing.py`
- [ ] Create `backend/celery_app/tasks/sync_shopify.py`
- [ ] Update `backend/celery_app/celery_config.py`
- [ ] Update `backend/celery_app/tasks/publishing.py`
- [ ] Create backfill script for existing products
- [ ] Test end-to-end
