-- ============================================================
-- Migration 005: Add Sync Scheduler Tables
-- ============================================================
-- Purpose: Create table to track daily Boeing sync schedules
-- for each published product.
-- ============================================================

-- 1. Create product_sync_schedule table
CREATE TABLE IF NOT EXISTS product_sync_schedule (
    -- Identity
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    sku TEXT NOT NULL,

    -- Hourly Bucket Scheduling (0-23)
    -- Products are distributed across 24 hours based on hash(sku) % 24
    hour_bucket SMALLINT NOT NULL CHECK (hour_bucket BETWEEN 0 AND 23),

    -- Scheduling Timestamps
    next_sync_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_sync_at TIMESTAMP WITH TIME ZONE,

    -- Sync Status
    -- pending: Waiting for next sync
    -- syncing: Currently being processed
    -- success: Last sync completed successfully
    -- failed: Last sync failed
    sync_status TEXT NOT NULL DEFAULT 'pending' CHECK (
        sync_status IN ('pending', 'syncing', 'success', 'failed')
    ),

    -- Error Tracking
    last_error TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,

    -- Change Detection
    -- Hash of Boeing response (price, quantity, in_stock) to detect changes
    last_boeing_hash TEXT,
    last_price NUMERIC,
    last_quantity INTEGER,

    -- Control
    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    -- Audit Timestamps
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    -- Constraints
    CONSTRAINT product_sync_schedule_user_sku_unique UNIQUE (user_id, sku)
);

-- 2. Create index for hourly dispatch query
-- This is the primary query used by the dispatcher:
-- SELECT * FROM product_sync_schedule WHERE hour_bucket = X AND is_active = TRUE
CREATE INDEX IF NOT EXISTS idx_sync_schedule_hour_bucket
    ON product_sync_schedule (hour_bucket, is_active)
    WHERE is_active = TRUE;

-- 3. Create index for finding syncing products (to detect stuck tasks)
CREATE INDEX IF NOT EXISTS idx_sync_schedule_syncing
    ON product_sync_schedule (sync_status, last_sync_at)
    WHERE sync_status = 'syncing';

-- 4. Create index for user-specific queries (dashboard)
CREATE INDEX IF NOT EXISTS idx_sync_schedule_user_id
    ON product_sync_schedule (user_id, is_active);

-- 5. Create index for failed products (monitoring)
CREATE INDEX IF NOT EXISTS idx_sync_schedule_failures
    ON product_sync_schedule (consecutive_failures)
    WHERE consecutive_failures > 0 AND is_active = TRUE;

-- 6. Create trigger for updated_at
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

-- 7. Add comments for documentation
COMMENT ON TABLE product_sync_schedule IS 'Tracks daily Boeing sync schedule for each published product';
COMMENT ON COLUMN product_sync_schedule.hour_bucket IS 'Hour of day (0-23) when this product syncs. Calculated as hash(sku) % 24';
COMMENT ON COLUMN product_sync_schedule.next_sync_at IS 'When the next sync should happen. Updated to last_sync_at + 24h after each sync';
COMMENT ON COLUMN product_sync_schedule.last_boeing_hash IS 'MD5 hash of (price, quantity, in_stock) for change detection';
COMMENT ON COLUMN product_sync_schedule.consecutive_failures IS 'Number of consecutive sync failures. Product deactivated after 5';
