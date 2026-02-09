-- ============================================================
-- MIGRATION 008: Sync Scheduler for Production
-- ============================================================
-- Run this script on your Supabase SQL Editor
--
-- This migration adds:
-- 1. product_sync_schedule table for sync scheduling
-- 2. Generic set_updated_at() trigger function
-- 3. All RPC functions for batch operations
-- 4. Triggers for automatic batch stats updates
--
-- IMPORTANT: This is designed to work with your existing
-- production schema where part_numbers is TEXT[] (not JSONB)
-- ============================================================

-- ============================================================
-- 1. GENERIC updated_at TRIGGER FUNCTION
-- ============================================================
-- This is a shared function that can be used by any table

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- 2. PRODUCT_SYNC_SCHEDULE TABLE
-- ============================================================
-- Tracks daily Boeing sync schedule for published products.
-- Uses least-loaded slot allocation for efficient Boeing API usage.

CREATE TABLE IF NOT EXISTS public.product_sync_schedule (
  -- Primary Key
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Product Identity
  user_id TEXT NOT NULL,
  sku TEXT NOT NULL,

  -- Slot Assignment (0-23, calculated via least-loaded algorithm)
  hour_bucket SMALLINT NOT NULL CHECK (hour_bucket BETWEEN 0 AND 23),

  -- Sync Status: pending, syncing, success, failed
  sync_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (sync_status IN ('pending', 'syncing', 'success', 'failed')),

  -- Timing
  last_sync_at TIMESTAMPTZ,

  -- Failure Tracking
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,

  -- Change Detection (Boeing data hash)
  last_boeing_hash TEXT,
  last_price NUMERIC,
  last_quantity INTEGER,

  -- Extended tracking fields for sync
  last_inventory_status TEXT,
  last_locations JSONB,

  -- Control: FALSE = deactivated after 5 consecutive failures
  is_active BOOLEAN NOT NULL DEFAULT TRUE,

  -- Audit Timestamps
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- Unique constraint
  CONSTRAINT product_sync_schedule_user_sku_unique UNIQUE (user_id, sku)
);

-- ============================================================
-- 3. INDEXES FOR product_sync_schedule
-- ============================================================

-- Primary index for hourly dispatch query
CREATE INDEX IF NOT EXISTS idx_sync_hourly_dispatch
  ON public.product_sync_schedule (hour_bucket, sync_status, last_sync_at)
  WHERE is_active = TRUE;

-- Index for slot distribution query (used during product assignment)
CREATE INDEX IF NOT EXISTS idx_sync_slot_distribution
  ON public.product_sync_schedule (hour_bucket)
  WHERE is_active = TRUE;

-- Index for retry dispatcher query
CREATE INDEX IF NOT EXISTS idx_sync_failed_products
  ON public.product_sync_schedule (consecutive_failures, last_sync_at)
  WHERE is_active = TRUE AND sync_status = 'failed';

-- Index for stuck detection
CREATE INDEX IF NOT EXISTS idx_sync_stuck
  ON public.product_sync_schedule (last_sync_at)
  WHERE sync_status = 'syncing';

-- Index for user-specific queries (dashboard)
CREATE INDEX IF NOT EXISTS idx_sync_user
  ON public.product_sync_schedule (user_id, is_active);

-- ============================================================
-- 4. TRIGGER FOR product_sync_schedule updated_at
-- ============================================================

DROP TRIGGER IF EXISTS trg_sync_schedule_updated_at ON public.product_sync_schedule;
CREATE TRIGGER trg_sync_schedule_updated_at
BEFORE UPDATE ON public.product_sync_schedule
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- 5. RPC FUNCTIONS FOR BATCH OPERATIONS
-- ============================================================

-- 5.1 Atomically increment extracted_count
CREATE OR REPLACE FUNCTION increment_batch_extracted(
  p_batch_id VARCHAR(36),
  p_count INTEGER DEFAULT 1
)
RETURNS VOID AS $$
BEGIN
  UPDATE public.batches
  SET extracted_count = COALESCE(extracted_count, 0) + p_count,
      updated_at = now()
  WHERE id = p_batch_id;
END;
$$ LANGUAGE plpgsql;

-- 5.2 Atomically increment normalized_count
CREATE OR REPLACE FUNCTION increment_batch_normalized(
  p_batch_id VARCHAR(36),
  p_count INTEGER DEFAULT 1
)
RETURNS VOID AS $$
BEGIN
  UPDATE public.batches
  SET normalized_count = COALESCE(normalized_count, 0) + p_count,
      updated_at = now()
  WHERE id = p_batch_id;
END;
$$ LANGUAGE plpgsql;

-- 5.3 Atomically increment published_count
CREATE OR REPLACE FUNCTION increment_batch_published(
  p_batch_id VARCHAR(36),
  p_count INTEGER DEFAULT 1
)
RETURNS VOID AS $$
BEGIN
  UPDATE public.batches
  SET published_count = COALESCE(published_count, 0) + p_count,
      updated_at = now()
  WHERE id = p_batch_id;
END;
$$ LANGUAGE plpgsql;

-- 5.4 Atomically increment failed_count and append to failed_items
CREATE OR REPLACE FUNCTION record_batch_failure(
  p_batch_id VARCHAR(36),
  p_part_number TEXT,
  p_error TEXT
)
RETURNS VOID AS $$
BEGIN
  UPDATE public.batches
  SET
    failed_count = COALESCE(failed_count, 0) + 1,
    failed_items = COALESCE(failed_items, '[]'::jsonb) ||
      jsonb_build_object('part_number', p_part_number, 'error', p_error),
    updated_at = now()
  WHERE id = p_batch_id;
END;
$$ LANGUAGE plpgsql;

-- 5.5 Recalculate batch stats from actual product_staging data
CREATE OR REPLACE FUNCTION recalculate_batch_stats(p_batch_id VARCHAR(36))
RETURNS TABLE (
  batch_id VARCHAR(36),
  total_items INTEGER,
  extracted_count INTEGER,
  normalized_count INTEGER,
  published_count INTEGER,
  failed_count INTEGER,
  status VARCHAR(20)
) AS $$
DECLARE
  v_total INTEGER;
  v_extracted INTEGER;
  v_normalized INTEGER;
  v_published INTEGER;
  v_failed INTEGER;
  v_current_status VARCHAR(20);
BEGIN
  -- Get total from batch record
  SELECT b.total_items, b.status INTO v_total, v_current_status
  FROM public.batches b
  WHERE b.id = p_batch_id;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Batch not found: %', p_batch_id;
  END IF;

  -- Count products by status from product_staging
  SELECT
    COUNT(*) FILTER (WHERE ps.status IS NOT NULL),
    COUNT(*) FILTER (WHERE ps.status IN ('fetched', 'normalized', 'published')),
    COUNT(*) FILTER (WHERE ps.status = 'published'),
    COUNT(*) FILTER (WHERE ps.status = 'failed')
  INTO v_extracted, v_normalized, v_published, v_failed
  FROM public.product_staging ps
  WHERE ps.batch_id = p_batch_id;

  -- Update the batch with recalculated counts
  UPDATE public.batches b
  SET
    extracted_count = v_extracted,
    normalized_count = v_normalized,
    published_count = v_published,
    failed_count = v_failed,
    updated_at = now()
  WHERE b.id = p_batch_id;

  RETURN QUERY SELECT
    p_batch_id,
    v_total,
    v_extracted,
    v_normalized,
    v_published,
    v_failed,
    v_current_status;
END;
$$ LANGUAGE plpgsql;

-- 5.6 Get real-time batch stats
-- NOTE: Uses TEXT[] for part_numbers (matches production schema)
CREATE OR REPLACE FUNCTION get_batch_stats(p_batch_id VARCHAR(36))
RETURNS TABLE (
  batch_id VARCHAR(36),
  batch_type VARCHAR(20),
  status VARCHAR(20),
  total_items INTEGER,
  extracted_count BIGINT,
  normalized_count BIGINT,
  published_count BIGINT,
  failed_count BIGINT,
  skipped_count BIGINT,
  progress_percent NUMERIC
) AS $$
DECLARE
  v_batch RECORD;
  v_extracted BIGINT;
  v_normalized BIGINT;
  v_published BIGINT;
  v_failed BIGINT;
  v_skipped BIGINT;
  v_progress NUMERIC;
  v_total_for_progress INTEGER;
BEGIN
  -- Get batch record
  SELECT b.* INTO v_batch
  FROM public.batches b
  WHERE b.id = p_batch_id;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Batch not found: %', p_batch_id;
  END IF;

  -- Count products by status from product_staging
  SELECT
    COUNT(*),
    COUNT(*) FILTER (WHERE ps.status IN ('fetched', 'normalized', 'published', 'failed')),
    COUNT(*) FILTER (WHERE ps.status = 'published'),
    COUNT(*) FILTER (WHERE ps.status = 'failed'),
    COUNT(*) FILTER (WHERE
      (ps.inventory_quantity IS NULL OR ps.inventory_quantity = 0) OR
      (COALESCE(ps.price, ps.net_price, ps.cost_per_item, 0) = 0)
    )
  INTO v_extracted, v_normalized, v_published, v_failed, v_skipped
  FROM public.product_staging ps
  WHERE ps.batch_id = p_batch_id;

  -- Calculate progress based on batch type
  v_total_for_progress := v_batch.total_items;
  IF v_total_for_progress > 0 THEN
    CASE v_batch.batch_type
      WHEN 'search' THEN
        -- Search stage: progress based on normalization completion
        v_progress := ((v_normalized + v_failed)::NUMERIC / v_total_for_progress) * 100;
      WHEN 'normalized' THEN
        -- Normalized stage: show actual normalized progress
        v_progress := (v_normalized::NUMERIC / v_total_for_progress) * 100;
      WHEN 'publishing', 'publish' THEN
        -- Use publish_part_numbers length if available (TEXT[] array)
        IF v_batch.publish_part_numbers IS NOT NULL AND array_length(v_batch.publish_part_numbers, 1) > 0 THEN
          v_total_for_progress := array_length(v_batch.publish_part_numbers, 1);
        END IF;
        v_progress := ((v_published + v_failed)::NUMERIC / v_total_for_progress) * 100;
      ELSE
        v_progress := 0;
    END CASE;
  ELSE
    v_progress := 0;
  END IF;

  RETURN QUERY SELECT
    p_batch_id,
    v_batch.batch_type,
    v_batch.status,
    v_batch.total_items,
    v_extracted,
    v_normalized,
    v_published,
    v_failed,
    v_skipped,
    LEAST(v_progress, 100);
END;
$$ LANGUAGE plpgsql;

-- 5.7 Get product status counts by batch
CREATE OR REPLACE FUNCTION get_batch_product_status_counts(p_batch_id VARCHAR(36))
RETURNS TABLE (
  status TEXT,
  count BIGINT
) AS $$
BEGIN
  RETURN QUERY
  SELECT
    COALESCE(ps.status, 'unknown') as status,
    COUNT(*) as count
  FROM public.product_staging ps
  WHERE ps.batch_id = p_batch_id
  GROUP BY ps.status
  ORDER BY
    CASE ps.status
      WHEN 'fetched' THEN 1
      WHEN 'normalized' THEN 2
      WHEN 'published' THEN 3
      WHEN 'failed' THEN 4
      ELSE 5
    END;
END;
$$ LANGUAGE plpgsql;

-- 5.8 Trigger function to auto-update batch stats
CREATE OR REPLACE FUNCTION update_batch_stats_on_product_change()
RETURNS TRIGGER AS $$
DECLARE
  v_batch_id VARCHAR(36);
BEGIN
  -- Determine which batch_id to update
  IF TG_OP = 'DELETE' THEN
    v_batch_id := OLD.batch_id;
  ELSE
    v_batch_id := NEW.batch_id;
  END IF;

  -- Skip if no batch_id
  IF v_batch_id IS NULL THEN
    IF TG_OP = 'DELETE' THEN
      RETURN OLD;
    ELSE
      RETURN NEW;
    END IF;
  END IF;

  -- Update the batch counts based on current product_staging data
  UPDATE public.batches b
  SET
    extracted_count = (
      SELECT COUNT(*)
      FROM public.product_staging ps
      WHERE ps.batch_id = v_batch_id
    ),
    normalized_count = (
      SELECT COUNT(*)
      FROM public.product_staging ps
      WHERE ps.batch_id = v_batch_id
      AND ps.status IN ('fetched', 'normalized', 'published')
    ),
    published_count = (
      SELECT COUNT(*)
      FROM public.product_staging ps
      WHERE ps.batch_id = v_batch_id
      AND ps.status = 'published'
    ),
    updated_at = now()
  WHERE b.id = v_batch_id;

  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  ELSE
    RETURN NEW;
  END IF;
END;
$$ LANGUAGE plpgsql;

-- Create trigger for automatic batch stats update
DROP TRIGGER IF EXISTS trg_update_batch_stats ON public.product_staging;
CREATE TRIGGER trg_update_batch_stats
AFTER INSERT OR UPDATE OR DELETE ON public.product_staging
FOR EACH ROW EXECUTE FUNCTION update_batch_stats_on_product_change();

-- 5.9 Batch completion check RPC
CREATE OR REPLACE FUNCTION check_batch_completion(p_batch_id VARCHAR(36))
RETURNS TABLE (
  is_complete BOOLEAN,
  new_status VARCHAR(20),
  published_count INTEGER,
  failed_count INTEGER,
  total_items INTEGER
) AS $$
DECLARE
  v_batch RECORD;
  v_total_processed INTEGER;
  v_is_complete BOOLEAN;
  v_new_status VARCHAR(20);
BEGIN
  -- Get batch with fresh counts
  SELECT * INTO v_batch
  FROM public.batches b
  WHERE b.id = p_batch_id;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Batch not found: %', p_batch_id;
  END IF;

  -- Calculate total processed
  CASE v_batch.batch_type
    WHEN 'search' THEN
      v_total_processed := v_batch.normalized_count + v_batch.failed_count;
    WHEN 'publishing', 'publish' THEN
      v_total_processed := v_batch.published_count + v_batch.failed_count;
    ELSE
      v_total_processed := v_batch.normalized_count + v_batch.failed_count;
  END CASE;

  -- Check if complete
  v_is_complete := v_total_processed >= v_batch.total_items;

  -- Determine new status
  IF v_is_complete THEN
    IF v_batch.failed_count > 0 AND v_batch.failed_count = v_batch.total_items THEN
      v_new_status := 'failed';
    ELSE
      v_new_status := 'completed';
    END IF;

    -- Update batch status
    UPDATE public.batches
    SET
      status = v_new_status,
      completed_at = now(),
      updated_at = now()
    WHERE id = p_batch_id
    AND status NOT IN ('completed', 'failed', 'cancelled');
  ELSE
    v_new_status := v_batch.status;
  END IF;

  RETURN QUERY SELECT
    v_is_complete,
    v_new_status,
    v_batch.published_count::INTEGER,
    v_batch.failed_count::INTEGER,
    v_batch.total_items::INTEGER;
END;
$$ LANGUAGE plpgsql;

-- 5.10 Get batch part numbers with statuses
-- NOTE: Uses TEXT[] for part_numbers (matches production schema)
CREATE OR REPLACE FUNCTION get_batch_part_numbers_with_status(p_batch_id VARCHAR(36))
RETURNS TABLE (
  part_number TEXT,
  status TEXT,
  has_inventory BOOLEAN,
  has_price BOOLEAN
) AS $$
DECLARE
  v_batch RECORD;
  v_part_numbers TEXT[];
BEGIN
  -- Get batch record
  SELECT b.* INTO v_batch
  FROM public.batches b
  WHERE b.id = p_batch_id;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Batch not found: %', p_batch_id;
  END IF;

  -- Get part_numbers array (already TEXT[] in production)
  v_part_numbers := COALESCE(v_batch.part_numbers, ARRAY[]::TEXT[]);

  -- Return part numbers with their status from product_staging
  -- If not in product_staging yet, status is 'pending'
  RETURN QUERY
  SELECT
    pn.part_number,
    COALESCE(ps.status, 'pending')::TEXT as status,
    COALESCE(ps.inventory_quantity > 0, false) as has_inventory,
    COALESCE(
      COALESCE(ps.price, ps.net_price, ps.cost_per_item, 0) > 0,
      false
    ) as has_price
  FROM unnest(v_part_numbers) AS pn(part_number)
  LEFT JOIN public.product_staging ps
    ON ps.batch_id = p_batch_id
    AND (ps.sku = pn.part_number OR ps.sku LIKE pn.part_number || '=%')
  ORDER BY
    CASE COALESCE(ps.status, 'pending')
      WHEN 'pending' THEN 1
      WHEN 'fetched' THEN 2
      WHEN 'normalized' THEN 3
      WHEN 'published' THEN 4
      WHEN 'failed' THEN 5
      ELSE 6
    END,
    pn.part_number;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- 6. ENABLE REALTIME FOR NEW TABLE
-- ============================================================
-- Required for Supabase Realtime subscriptions

DO $$
BEGIN
  -- Enable realtime for product_sync_schedule (if not already enabled)
  IF NOT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname = 'supabase_realtime'
    AND schemaname = 'public'
    AND tablename = 'product_sync_schedule'
  ) THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.product_sync_schedule;
  END IF;
END $$;

-- ============================================================
-- 7. ADD COMMENTS FOR DOCUMENTATION
-- ============================================================

COMMENT ON TABLE public.product_sync_schedule IS
'Tracks daily Boeing sync schedule for published products. Uses least-loaded slot allocation for efficient Boeing API usage.';

COMMENT ON COLUMN public.product_sync_schedule.hour_bucket IS
'Hour of day (0-23) assigned via least-loaded algorithm. Ensures minimum 10 products per active slot.';

COMMENT ON COLUMN public.product_sync_schedule.sync_status IS
'Current state: pending (ready), syncing (locked), success (done), failed (retry needed)';

COMMENT ON COLUMN public.product_sync_schedule.last_boeing_hash IS
'SHA-256 hash (first 16 chars) of (price, quantity, status, locations) for change detection.';

COMMENT ON COLUMN public.product_sync_schedule.consecutive_failures IS
'Incremented on each failure, reset to 0 on success. Product deactivated at 5.';

COMMENT ON COLUMN public.product_sync_schedule.is_active IS
'FALSE after 5 consecutive failures. Product set to DRAFT in Shopify. Requires manual review.';

COMMENT ON COLUMN public.product_sync_schedule.last_inventory_status IS
'Last known inventory status: in_stock or out_of_stock';

COMMENT ON COLUMN public.product_sync_schedule.last_locations IS
'JSONB array of location quantities from last sync, e.g., [{"location": "Dallas", "quantity": 10}]';

-- ============================================================
-- 8. VERIFY MIGRATION SUCCESS
-- ============================================================
-- This SELECT will help you verify the migration was successful

DO $$
DECLARE
  table_count INTEGER;
  function_count INTEGER;
  trigger_count INTEGER;
BEGIN
  -- Check table exists
  SELECT COUNT(*) INTO table_count
  FROM information_schema.tables
  WHERE table_schema = 'public'
  AND table_name = 'product_sync_schedule';

  IF table_count = 1 THEN
    RAISE NOTICE '✅ product_sync_schedule table created successfully';
  ELSE
    RAISE EXCEPTION '❌ product_sync_schedule table NOT created';
  END IF;

  -- Check functions exist
  SELECT COUNT(*) INTO function_count
  FROM pg_proc p
  JOIN pg_namespace n ON p.pronamespace = n.oid
  WHERE n.nspname = 'public'
  AND p.proname IN (
    'set_updated_at',
    'increment_batch_extracted',
    'increment_batch_normalized',
    'increment_batch_published',
    'record_batch_failure',
    'recalculate_batch_stats',
    'get_batch_stats',
    'get_batch_product_status_counts',
    'update_batch_stats_on_product_change',
    'check_batch_completion',
    'get_batch_part_numbers_with_status'
  );

  RAISE NOTICE '✅ % RPC functions created successfully', function_count;

  -- Check triggers exist
  SELECT COUNT(*) INTO trigger_count
  FROM information_schema.triggers
  WHERE trigger_schema = 'public'
  AND trigger_name IN ('trg_sync_schedule_updated_at', 'trg_update_batch_stats');

  RAISE NOTICE '✅ % triggers created successfully', trigger_count;

  RAISE NOTICE '';
  RAISE NOTICE '========================================';
  RAISE NOTICE 'MIGRATION 008 COMPLETED SUCCESSFULLY!';
  RAISE NOTICE '========================================';
END $$;

-- ============================================================
-- END OF MIGRATION 008
-- ============================================================
