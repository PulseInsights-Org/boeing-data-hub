-- ============================================================
-- Migration 006: Sync Scheduler V2 - Boeing API Efficient Design
-- ============================================================
-- Purpose: Complete rewrite of sync scheduler with:
-- - Token bucket rate limiting support
-- - Least-loaded slot allocation
-- - Efficient batch processing (always 10 SKUs)
-- ============================================================

-- 1. Drop old table and recreate with new schema
DROP TABLE IF EXISTS product_sync_schedule CASCADE;

-- 2. Create new product_sync_schedule table
CREATE TABLE product_sync_schedule (
    -- Primary Key
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Product Identity
    user_id TEXT NOT NULL,
    sku TEXT NOT NULL,

    -- Slot Assignment (0-23, calculated via least-loaded algorithm)
    -- Not all slots are active - depends on product count
    hour_bucket SMALLINT NOT NULL CHECK (hour_bucket BETWEEN 0 AND 23),

    -- Sync Status
    -- pending: Ready for next sync
    -- syncing: Currently being processed (locked)
    -- success: Last sync completed successfully
    -- failed: Last sync failed (will be retried)
    sync_status TEXT NOT NULL DEFAULT 'pending' CHECK (
        sync_status IN ('pending', 'syncing', 'success', 'failed')
    ),

    -- Timing
    last_sync_at TIMESTAMPTZ,              -- Last sync attempt (success or failure)

    -- Failure Tracking
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,                        -- Error message (truncated to 500 chars)

    -- Change Detection (Boeing data hash)
    last_boeing_hash TEXT,                  -- MD5 of (price, qty, in_stock)
    last_price NUMERIC,                     -- Last known Boeing price
    last_quantity INTEGER,                  -- Last known Boeing quantity

    -- Control
    is_active BOOLEAN NOT NULL DEFAULT TRUE,  -- FALSE = deactivated after 5 failures

    -- Audit Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Unique constraint on user + sku
    CONSTRAINT product_sync_schedule_user_sku_unique UNIQUE (user_id, sku)
);

-- 3. Create optimized indexes

-- Primary index for hourly dispatch query
-- Used by: dispatch_hourly_sync
CREATE INDEX idx_sync_hourly_dispatch ON product_sync_schedule
    (hour_bucket, sync_status, last_sync_at)
    WHERE is_active = TRUE;

-- Index for slot distribution query (used during product assignment)
-- Used by: assign_hour_bucket_smart
CREATE INDEX idx_sync_slot_distribution ON product_sync_schedule
    (hour_bucket)
    WHERE is_active = TRUE;

-- Index for retry dispatcher query
-- Used by: dispatch_retry_sync
CREATE INDEX idx_sync_failed_products ON product_sync_schedule
    (consecutive_failures, last_sync_at)
    WHERE is_active = TRUE AND sync_status = 'failed';

-- Index for stuck detection
-- Used by: reset_stuck_syncing
CREATE INDEX idx_sync_stuck ON product_sync_schedule
    (last_sync_at)
    WHERE sync_status = 'syncing';

-- Index for user-specific queries (dashboard)
CREATE INDEX idx_sync_user ON product_sync_schedule
    (user_id, is_active);

-- 4. Create trigger for updated_at
CREATE OR REPLACE FUNCTION set_sync_schedule_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sync_schedule_updated_at ON product_sync_schedule;

CREATE TRIGGER trg_sync_schedule_updated_at
    BEFORE UPDATE ON product_sync_schedule
    FOR EACH ROW
    EXECUTE FUNCTION set_sync_schedule_updated_at();

-- 5. Add comments for documentation
COMMENT ON TABLE product_sync_schedule IS 'Tracks daily Boeing sync schedule for published products. Uses least-loaded slot allocation for efficient Boeing API usage.';
COMMENT ON COLUMN product_sync_schedule.hour_bucket IS 'Hour of day (0-23) assigned via least-loaded algorithm. Ensures minimum 10 products per active slot.';
COMMENT ON COLUMN product_sync_schedule.sync_status IS 'Current state: pending (ready), syncing (locked), success (done), failed (retry needed)';
COMMENT ON COLUMN product_sync_schedule.last_boeing_hash IS 'MD5 hash of (price, quantity, in_stock) for change detection. Only update Shopify if hash changed.';
COMMENT ON COLUMN product_sync_schedule.consecutive_failures IS 'Incremented on each failure, reset to 0 on success. Product deactivated at 5.';
COMMENT ON COLUMN product_sync_schedule.is_active IS 'FALSE after 5 consecutive failures. Product set to DRAFT in Shopify. Requires manual review.';
