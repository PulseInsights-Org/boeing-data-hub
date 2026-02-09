-- Migration 006: Add inventory status and location tracking columns to product_sync_schedule
-- These columns enable better change detection and sync tracking

-- Add last_inventory_status column to track in_stock/out_of_stock state
ALTER TABLE product_sync_schedule
ADD COLUMN IF NOT EXISTS last_inventory_status TEXT;

-- Add last_location_summary column to track location quantities
ALTER TABLE product_sync_schedule
ADD COLUMN IF NOT EXISTS last_location_summary TEXT;

-- Add index for efficient status queries
CREATE INDEX IF NOT EXISTS idx_sync_schedule_inventory_status
ON product_sync_schedule(last_inventory_status)
WHERE is_active = TRUE;

-- Add failed_part_numbers column to batches table if not exists
ALTER TABLE batches
ADD COLUMN IF NOT EXISTS failed_part_numbers TEXT[] DEFAULT '{}';

-- Comment on new columns
COMMENT ON COLUMN product_sync_schedule.last_inventory_status IS 'Last known inventory status: in_stock or out_of_stock';
COMMENT ON COLUMN product_sync_schedule.last_location_summary IS 'Last known location summary e.g., "Dallas: 10; Chicago: 5"';
COMMENT ON COLUMN batches.failed_part_numbers IS 'Array of part numbers that failed during batch processing';
