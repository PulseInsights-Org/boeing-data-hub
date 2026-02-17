-- ============================================================
-- PRODUCTION MIGRATION
-- Migrates production DB to match the restructured schema
-- ============================================================
-- Generated: 2026-02-17
-- From: production-db-schema.sql + triggers-and-RPC.json
-- To:   complete_db_schema.sql (with code-verified additions)
--
-- SAFE TO RUN MULTIPLE TIMES (all operations are idempotent)
-- RECOMMENDED: Run in a transaction in Supabase SQL editor
-- ============================================================

BEGIN;

-- ============================================================
-- 1. SHARED TRIGGER FUNCTION (must exist before triggers)
-- ============================================================
-- Consolidate all updated_at triggers into one shared function.
-- Production currently has 3 separate functions:
--   set_product_updated_at(), set_product_staging_updated_at(), set_updated_at()
-- Target has only set_updated_at() shared across all tables.

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ============================================================
-- 2. USERS TABLE (new)
-- ============================================================
-- Legacy local auth table. Not in production yet.

CREATE TABLE IF NOT EXISTS public.users (
  id TEXT PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  last_login TIMESTAMPTZ
);

INSERT INTO public.users (id, username, password)
VALUES ('user_001', 'sk-user1', 'pulse123')
ON CONFLICT (id) DO NOTHING;


-- ============================================================
-- 3. BATCHES TABLE ALTERATIONS
-- ============================================================

-- 3a. Add skipped_count column (used by batch_store.record_skipped)
ALTER TABLE public.batches
  ADD COLUMN IF NOT EXISTS skipped_count INTEGER NOT NULL DEFAULT 0;

-- 3b. Add skipped_part_numbers column (used by batch_store.record_skipped)
ALTER TABLE public.batches
  ADD COLUMN IF NOT EXISTS skipped_part_numbers TEXT[] DEFAULT '{}';

-- 3c. Drop redundant failed_part_numbers column (tracked in failed_items JSONB)
ALTER TABLE public.batches
  DROP COLUMN IF EXISTS failed_part_numbers;

-- 3d. Migrate part_numbers from TEXT[] to JSONB
-- The code writes Python lists which work with both types via PostgREST,
-- but the RPC functions use jsonb_array_elements_text() which needs JSONB.
DO $$
BEGIN
  -- Check if part_numbers is still a text array type
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'batches'
      AND column_name = 'part_numbers'
      AND data_type = 'ARRAY'
  ) THEN
    -- 1. Drop the TEXT[] default first (can't auto-cast default during type change)
    ALTER TABLE public.batches
      ALTER COLUMN part_numbers DROP DEFAULT;

    -- 2. Convert existing TEXT[] data to JSONB
    ALTER TABLE public.batches
      ALTER COLUMN part_numbers TYPE JSONB
      USING COALESCE(to_jsonb(part_numbers), '[]'::jsonb);

    -- 3. Set the new JSONB default
    ALTER TABLE public.batches
      ALTER COLUMN part_numbers SET DEFAULT '[]'::jsonb;
  END IF;
END $$;

-- 3e. Update batch_type CHECK constraint
-- Production: ('search', 'normalized', 'publishing', 'publish')
-- Target:     ('extract', 'normalize', 'publish')
-- First migrate existing data to new values, then update the constraint.

UPDATE public.batches SET batch_type = 'extract'   WHERE batch_type = 'search';
UPDATE public.batches SET batch_type = 'normalize'  WHERE batch_type = 'normalized';
UPDATE public.batches SET batch_type = 'publish'    WHERE batch_type = 'publishing';

ALTER TABLE public.batches DROP CONSTRAINT IF EXISTS batches_batch_type_check;
ALTER TABLE public.batches ADD CONSTRAINT batches_batch_type_check CHECK (
  batch_type IN ('extract', 'normalize', 'publish')
);

-- 3f. Remove duplicate idempotency index (idx_batches_idempotency already covers this)
DROP INDEX IF EXISTS idx_batches_idempotency_key;

-- 3g. Backfill existing failed_items entries that lack stage/timestamp
-- (from migration_009)
UPDATE public.batches
SET failed_items = (
  SELECT jsonb_agg(
    CASE
      WHEN item ? 'stage' THEN item
      ELSE item || jsonb_build_object(
        'stage', 'unknown',
        'timestamp', COALESCE(updated_at, now())::text
      )
    END
  )
  FROM jsonb_array_elements(failed_items) AS item
)
WHERE failed_items IS NOT NULL
  AND jsonb_array_length(failed_items) > 0
  AND EXISTS (
    SELECT 1
    FROM jsonb_array_elements(failed_items) AS item
    WHERE NOT (item ? 'stage')
  );


-- ============================================================
-- 4. PRODUCT_SYNC_SCHEDULE ADDITIONS
-- ============================================================
-- These columns exist in production and are used by the code
-- (sync_store.update_sync_success, report_service.py) but were
-- missed in complete_db_schema.sql. Adding IF NOT EXISTS to be safe.

ALTER TABLE public.product_sync_schedule
  ADD COLUMN IF NOT EXISTS last_inventory_status TEXT;

ALTER TABLE public.product_sync_schedule
  ADD COLUMN IF NOT EXISTS last_locations JSONB;


-- ============================================================
-- 5. SYNC_REPORTS TABLE (new - from migration_010)
-- ============================================================
-- Stores LLM-generated reports for completed sync cycles.
-- Used by report_store.py and report_service.py.

CREATE TABLE IF NOT EXISTS public.sync_reports (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  cycle_id TEXT NOT NULL,
  report_text TEXT NOT NULL,
  summary_stats JSONB DEFAULT '{}',
  file_path TEXT,
  email_sent BOOLEAN DEFAULT FALSE,
  email_recipients TEXT[],
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sync_reports_created
  ON public.sync_reports (created_at DESC);


-- ============================================================
-- 6. TRIGGER CONSOLIDATION
-- ============================================================
-- Replace per-table trigger functions with shared set_updated_at()

-- 6a. product table: replace set_product_updated_at -> set_updated_at
DROP TRIGGER IF EXISTS trg_product_updated_at ON public.product;
CREATE TRIGGER trg_product_updated_at
BEFORE UPDATE ON public.product
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 6b. product_staging table: replace set_product_staging_updated_at -> set_updated_at
DROP TRIGGER IF EXISTS trg_product_staging_updated_at ON public.product_staging;
CREATE TRIGGER trg_product_staging_updated_at
BEFORE UPDATE ON public.product_staging
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 6c. Drop old per-table trigger functions (no longer used)
DROP FUNCTION IF EXISTS set_product_updated_at();
DROP FUNCTION IF EXISTS set_product_staging_updated_at();


-- ============================================================
-- 7. RPC FUNCTION UPDATES
-- ============================================================

-- 7a. Drop production-only functions that are no longer in the codebase
DROP FUNCTION IF EXISTS increment_batch_counter(text, text, integer);
DROP FUNCTION IF EXISTS update_batch_status(text, text, text);

-- 7b. Drop duplicate record_batch_failure overloads (text vs varchar signatures)
-- We will recreate a single version with the p_stage parameter below.
DROP FUNCTION IF EXISTS record_batch_failure(text, text, text);
DROP FUNCTION IF EXISTS record_batch_failure(character varying, text, text);
DROP FUNCTION IF EXISTS record_batch_failure(character varying, text, text, text);

-- ============================================================
-- 7c. ATOMIC COUNTER INCREMENT FUNCTIONS
-- ============================================================

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

-- ============================================================
-- 7d. RECORD BATCH FAILURE (with stage + timestamp)
-- ============================================================

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
-- 7e. RECALCULATE BATCH STATS
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
  SELECT b.total_items, b.status INTO v_total, v_current_status
  FROM public.batches b
  WHERE b.id = p_batch_id;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Batch not found: %', p_batch_id;
  END IF;

  SELECT
    COUNT(*) FILTER (WHERE ps.status IS NOT NULL),
    COUNT(*) FILTER (WHERE ps.status IN ('fetched', 'normalized', 'published', 'blocked', 'failed')),
    COUNT(*) FILTER (WHERE ps.status = 'published'),
    COUNT(*) FILTER (WHERE ps.status IN ('blocked', 'failed'))
  INTO v_extracted, v_normalized, v_published, v_failed
  FROM public.product_staging ps
  WHERE ps.batch_id = p_batch_id;

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
-- 7f. GET BATCH STATS (with skipped_count + progress)
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
  SELECT b.* INTO v_batch
  FROM public.batches b
  WHERE b.id = p_batch_id;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Batch not found: %', p_batch_id;
  END IF;

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

  v_total_for_progress := v_batch.total_items;
  IF v_total_for_progress > 0 THEN
    CASE v_batch.batch_type
      WHEN 'extract' THEN
        v_progress := ((v_normalized + v_failed)::NUMERIC / v_total_for_progress) * 100;
      WHEN 'normalize' THEN
        v_progress := 100;
      WHEN 'publish' THEN
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

-- ============================================================
-- 7g. GET BATCH PRODUCT STATUS COUNTS
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
-- 7h. BATCH STATS TRIGGER (on product_staging changes)
-- ============================================================

CREATE OR REPLACE FUNCTION update_batch_stats_on_product_change()
RETURNS TRIGGER AS $$
DECLARE
  v_batch_id VARCHAR(36);
BEGIN
  IF TG_OP = 'DELETE' THEN
    v_batch_id := OLD.batch_id;
  ELSE
    v_batch_id := NEW.batch_id;
  END IF;

  IF v_batch_id IS NULL THEN
    IF TG_OP = 'DELETE' THEN
      RETURN OLD;
    ELSE
      RETURN NEW;
    END IF;
  END IF;

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

-- Recreate the trigger
DROP TRIGGER IF EXISTS trg_update_batch_stats ON public.product_staging;
CREATE TRIGGER trg_update_batch_stats
AFTER INSERT OR UPDATE OR DELETE ON public.product_staging
FOR EACH ROW EXECUTE FUNCTION update_batch_stats_on_product_change();

-- ============================================================
-- 7i. CHECK BATCH COMPLETION
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
  SELECT * INTO v_batch
  FROM public.batches b
  WHERE b.id = p_batch_id;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Batch not found: %', p_batch_id;
  END IF;

  CASE v_batch.batch_type
    WHEN 'extract' THEN
      v_total_processed := v_batch.normalized_count + v_batch.failed_count;
    WHEN 'publish' THEN
      v_total_processed := v_batch.published_count + v_batch.failed_count;
    ELSE
      v_total_processed := v_batch.normalized_count + v_batch.failed_count;
  END CASE;

  v_is_complete := v_total_processed >= v_batch.total_items;

  IF v_is_complete THEN
    IF v_batch.failed_count > 0 AND v_batch.failed_count = v_batch.total_items THEN
      v_new_status := 'failed';
    ELSE
      v_new_status := 'completed';
    END IF;

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
-- 7j. GET BATCH PART NUMBERS WITH STATUS
-- Updated to handle JSONB part_numbers column
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
-- 8. REALTIME PUBLICATION
-- ============================================================
-- Enable Supabase Realtime for key tables.
-- These are idempotent — Supabase ignores duplicates.

DO $$
BEGIN
  ALTER PUBLICATION supabase_realtime ADD TABLE public.batches;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
  ALTER PUBLICATION supabase_realtime ADD TABLE public.product_staging;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
  ALTER PUBLICATION supabase_realtime ADD TABLE public.product;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;


COMMIT;

-- ============================================================
-- END OF MIGRATION
-- ============================================================
--
-- SUMMARY OF CHANGES:
-- -------------------
-- TABLES:
--   [NEW]    users                        - Legacy auth table
--   [NEW]    sync_reports                  - LLM sync cycle reports
--   [ALTER]  batches.part_numbers          - TEXT[] -> JSONB
--   [ALTER]  batches.batch_type            - CHECK ('search','normalized','publishing','publish') -> ('extract','normalize','publish')
--   [ADD]    batches.skipped_count         - INTEGER NOT NULL DEFAULT 0
--   [ADD]    batches.skipped_part_numbers  - TEXT[] DEFAULT '{}'
--   [DROP]   batches.failed_part_numbers   - Redundant (tracked in failed_items JSONB)
--   [ADD]    product_sync_schedule.last_inventory_status - TEXT (code uses it)
--   [ADD]    product_sync_schedule.last_locations        - JSONB (code uses it)
--
-- INDEXES:
--   [DROP]   idx_batches_idempotency_key   - Duplicate of idx_batches_idempotency
--   [NEW]    idx_sync_reports_created       - For sync_reports table
--
-- TRIGGERS:
--   [UPDATE] product.trg_product_updated_at          -> uses set_updated_at()
--   [UPDATE] product_staging.trg_product_staging_updated_at -> uses set_updated_at()
--   [DROP]   set_product_updated_at()      - Replaced by shared set_updated_at()
--   [DROP]   set_product_staging_updated_at() - Replaced by shared set_updated_at()
--
-- RPC FUNCTIONS:
--   [UPDATE] record_batch_failure          - Added p_stage parameter
--   [UPDATE] recalculate_batch_stats       - Handles blocked status
--   [UPDATE] get_batch_stats               - Added skipped_count + progress
--   [UPDATE] check_batch_completion        - Uses new batch types
--   [UPDATE] get_batch_part_numbers_with_status - JSONB part_numbers
--   [UPDATE] update_batch_stats_on_product_change - trigger function
--   [DROP]   increment_batch_counter       - No longer used
--   [DROP]   update_batch_status           - No longer used
--   [DROP]   record_batch_failure (text overloads) - Consolidated to one
--
-- DATA MIGRATIONS:
--   batch_type values: search->extract, normalized->normalize, publishing->publish
--   failed_items backfill: adds stage + timestamp to old entries
--   part_numbers: TEXT[] values converted to JSONB arrays
-- ============================================================
