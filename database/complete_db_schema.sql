-- ============================================================
-- SUPABASE FULL DATABASE SCHEMA
-- ============================================================
-- Safe to run multiple times
-- ============================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================================
-- 1. SHARED updated_at TRIGGER FUNCTION
-- ============================================================

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- 2. USERS TABLE (from migration_001)
-- ============================================================

CREATE TABLE IF NOT EXISTS public.users (
  id TEXT PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  last_login TIMESTAMPTZ
);

-- Insert default user
INSERT INTO public.users (id, username, password)
VALUES ('user_001', 'sk-user1', 'pulse123')
ON CONFLICT (id) DO NOTHING;

-- ============================================================
-- 3. product_staging
-- ============================================================

CREATE TABLE IF NOT EXISTS public.product_staging (
  id TEXT PRIMARY KEY,
  sku TEXT NOT NULL,
  title TEXT NOT NULL,
  body_html TEXT,
  vendor TEXT,
  price NUMERIC,
  currency TEXT,
  inventory_quantity INTEGER,
  inventory_status TEXT,
  weight NUMERIC,
  weight_unit TEXT,
  country_of_origin TEXT,
  dim_length NUMERIC,
  dim_width NUMERIC,
  dim_height NUMERIC,
  dim_uom TEXT,
  status TEXT NOT NULL DEFAULT 'fetched',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  image_url TEXT,
  image_path TEXT,
  boeing_image_url TEXT,
  boeing_thumbnail_url TEXT,
  base_uom TEXT,
  hazmat_code TEXT,
  faa_approval_code TEXT,
  eccn TEXT,
  schedule_b_code TEXT,
  supplier_name TEXT,
  boeing_name TEXT,
  boeing_description TEXT,
  list_price NUMERIC,
  net_price NUMERIC,
  cost_per_item NUMERIC,
  location_summary TEXT,
  condition TEXT,
  pma BOOLEAN,
  estimated_lead_time_days INTEGER,
  trace TEXT,
  expiration_date DATE,
  notes TEXT,
  user_id TEXT NOT NULL DEFAULT 'system',
  shopify_product_id TEXT,
  batch_id TEXT,
  CONSTRAINT product_staging_user_sku_unique UNIQUE (user_id, sku)
);

CREATE INDEX IF NOT EXISTS idx_product_staging_user_id
  ON public.product_staging (user_id);

CREATE INDEX IF NOT EXISTS idx_product_staging_shopify_id
  ON public.product_staging (shopify_product_id)
  WHERE shopify_product_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_product_staging_batch_id
  ON public.product_staging (batch_id);

DROP TRIGGER IF EXISTS trg_product_staging_updated_at ON public.product_staging;
CREATE TRIGGER trg_product_staging_updated_at
BEFORE UPDATE ON public.product_staging
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- 4. product
-- ============================================================

CREATE TABLE IF NOT EXISTS public.product (
  id TEXT PRIMARY KEY,
  sku TEXT NOT NULL,
  title TEXT NOT NULL,
  body_html TEXT,
  vendor TEXT,
  price NUMERIC,
  currency TEXT,
  inventory_quantity INTEGER,
  inventory_status TEXT,
  weight NUMERIC,
  weight_unit TEXT,
  country_of_origin TEXT,
  dim_length NUMERIC,
  dim_width NUMERIC,
  dim_height NUMERIC,
  dim_uom TEXT,
  shopify_product_id TEXT,
  shopify_variant_id TEXT,
  shopify_handle TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  image_url TEXT,
  image_path TEXT,
  boeing_image_url TEXT,
  boeing_thumbnail_url TEXT,
  base_uom TEXT,
  hazmat_code TEXT,
  faa_approval_code TEXT,
  eccn TEXT,
  schedule_b_code TEXT,
  supplier_name TEXT,
  boeing_name TEXT,
  boeing_description TEXT,
  list_price NUMERIC,
  net_price NUMERIC,
  cost_per_item NUMERIC,
  location_summary TEXT,
  condition TEXT,
  pma BOOLEAN,
  estimated_lead_time_days INTEGER,
  trace TEXT,
  expiration_date DATE,
  notes TEXT,
  user_id TEXT NOT NULL DEFAULT 'system',
  CONSTRAINT product_user_sku_unique UNIQUE (user_id, sku)
);

CREATE INDEX IF NOT EXISTS idx_product_user_id
  ON public.product (user_id);

DROP TRIGGER IF EXISTS trg_product_updated_at ON public.product;
CREATE TRIGGER trg_product_updated_at
BEFORE UPDATE ON public.product
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- 5. batches
-- ============================================================

CREATE TABLE IF NOT EXISTS public.batches (
  id VARCHAR(36) PRIMARY KEY,
  batch_type VARCHAR(20) NOT NULL,
  status VARCHAR(20) DEFAULT 'pending',
  total_items INTEGER NOT NULL DEFAULT 0,
  extracted_count INTEGER DEFAULT 0,
  normalized_count INTEGER DEFAULT 0,
  published_count INTEGER DEFAULT 0,
  failed_count INTEGER DEFAULT 0,
  error_message TEXT,
  -- Each entry: {"part_number", "error", "stage", "timestamp"}
  failed_items JSONB DEFAULT '[]',
  celery_task_id VARCHAR(100),
  idempotency_key VARCHAR(100),
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  completed_at TIMESTAMPTZ,
  user_id VARCHAR(50) NOT NULL DEFAULT 'system',
  part_numbers JSONB DEFAULT '[]',
  publish_part_numbers TEXT[],
  skipped_count INTEGER NOT NULL DEFAULT 0,
  skipped_part_numbers TEXT[] DEFAULT '{}',
  CONSTRAINT batches_idempotency_key_unique UNIQUE (idempotency_key),
  CONSTRAINT batches_batch_type_check CHECK (
    batch_type IN ('extract', 'normalize', 'publish')
  ),
  CONSTRAINT batches_status_check CHECK (
    status IN ('pending', 'processing', 'completed', 'failed', 'cancelled')
  )
);

CREATE INDEX IF NOT EXISTS idx_batches_status
  ON public.batches (status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_batches_user_id
  ON public.batches (user_id);

CREATE INDEX IF NOT EXISTS idx_batches_idempotency
  ON public.batches (idempotency_key)
  WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_batches_active
  ON public.batches (created_at DESC)
  WHERE status IN ('pending', 'processing');

-- ============================================================
-- 6. boeing_raw_data
-- ============================================================

CREATE TABLE IF NOT EXISTS public.boeing_raw_data (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  search_query TEXT NOT NULL,
  raw_payload JSONB NOT NULL,
  user_id TEXT NOT NULL DEFAULT 'system'
);

CREATE INDEX IF NOT EXISTS idx_boeing_raw_data_user_id
  ON public.boeing_raw_data (user_id);

-- ============================================================
-- 7. product_sync_schedule (Sync Scheduler V2)
-- Tracks daily Boeing sync schedule for published products.
-- Uses least-loaded slot allocation for efficient Boeing API usage.
-- ============================================================

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

  -- Control: FALSE = deactivated after 5 consecutive failures
  is_active BOOLEAN NOT NULL DEFAULT TRUE,

  -- Audit Timestamps
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- Unique constraint
  CONSTRAINT product_sync_schedule_user_sku_unique UNIQUE (user_id, sku)
);

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

DROP TRIGGER IF EXISTS trg_sync_schedule_updated_at ON public.product_sync_schedule;
CREATE TRIGGER trg_sync_schedule_updated_at
BEFORE UPDATE ON public.product_sync_schedule
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- 8. RPC FUNCTIONS FOR BATCH OPERATIONS
-- ============================================================

-- ============================================================
-- 8.1 ATOMIC COUNTER INCREMENT FUNCTIONS
-- These use atomic operations to prevent race conditions
-- ============================================================

-- Atomically increment extracted_count
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

-- Atomically increment normalized_count
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

-- Atomically increment published_count
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

-- Atomically increment failed_count and append to failed_items with stage + timestamp
CREATE OR REPLACE FUNCTION record_batch_failure(
  p_batch_id VARCHAR(36),
  p_part_number TEXT,
  p_error TEXT,
  p_stage TEXT DEFAULT 'unknown'
)
RETURNS VOID AS $$
BEGIN
  UPDATE public.batches
  SET
    failed_count = COALESCE(failed_count, 0) + 1,
    failed_items = COALESCE(failed_items, '[]'::jsonb) ||
      jsonb_build_object(
        'part_number', p_part_number,
        'error', p_error,
        'stage', p_stage,
        'timestamp', now()::text
      ),
    updated_at = now()
  WHERE id = p_batch_id;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- 8.2 BATCH STATS RECALCULATION RPC
-- Recalculates batch counts from actual product_staging data
-- ============================================================

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
    COUNT(*) FILTER (WHERE ps.status IN ('fetched', 'normalized', 'published', 'blocked', 'failed')),
    COUNT(*) FILTER (WHERE ps.status = 'published'),
    COUNT(*) FILTER (WHERE ps.status IN ('blocked', 'failed'))
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

-- ============================================================
-- 8.3 GET BATCH STATS RPC
-- Returns real-time batch stats by querying product_staging directly
-- ============================================================

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
      WHEN 'extract' THEN
        v_progress := ((v_normalized + v_failed)::NUMERIC / v_total_for_progress) * 100;
      WHEN 'normalize' THEN
        v_progress := 100;
      WHEN 'publish' THEN
        -- Use publish_part_numbers length if available
        IF v_batch.publish_part_numbers IS NOT NULL AND jsonb_array_length(v_batch.publish_part_numbers::jsonb) > 0 THEN
          v_total_for_progress := jsonb_array_length(v_batch.publish_part_numbers::jsonb);
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

-- ============================================================
-- 8.4 GET PRODUCT STATUS COUNTS BY BATCH
-- Returns counts grouped by product status for a batch
-- ============================================================

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

-- ============================================================
-- 8.5 TRIGGER TO AUTO-UPDATE BATCH STATS ON PRODUCT CHANGE
-- Automatically updates batch counts when product_staging changes
-- ============================================================

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
-- NOTE: This is optional - comment out if you prefer manual control via RPCs
DROP TRIGGER IF EXISTS trg_update_batch_stats ON public.product_staging;
CREATE TRIGGER trg_update_batch_stats
AFTER INSERT OR UPDATE OR DELETE ON public.product_staging
FOR EACH ROW EXECUTE FUNCTION update_batch_stats_on_product_change();

-- ============================================================
-- 8.6 BATCH COMPLETION CHECK RPC
-- Checks if a batch is complete and updates status accordingly
-- ============================================================

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
    WHEN 'extract' THEN
      v_total_processed := v_batch.normalized_count + v_batch.failed_count;
    WHEN 'publish' THEN
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

-- ============================================================
-- 8.7 GET BATCH PART NUMBERS WITH STATUSES
-- Returns all part numbers for a batch with their actual processing status
-- Used by frontend to show accurate color-coded tags
-- ============================================================

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

  -- Convert JSONB array to TEXT array
  SELECT ARRAY(
    SELECT jsonb_array_elements_text(v_batch.part_numbers::jsonb)
  ) INTO v_part_numbers;

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
-- 9. ENABLE REALTIME FOR TABLES
-- Required for Supabase Realtime subscriptions
-- ============================================================

-- Enable realtime for batches table
ALTER PUBLICATION supabase_realtime ADD TABLE public.batches;

-- Enable realtime for product_staging table
ALTER PUBLICATION supabase_realtime ADD TABLE public.product_staging;

-- Enable realtime for product table
ALTER PUBLICATION supabase_realtime ADD TABLE public.product;

-- ============================================================
-- END OF SCHEMA
-- ============================================================
