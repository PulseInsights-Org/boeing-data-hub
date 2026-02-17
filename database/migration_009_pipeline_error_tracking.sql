-- ============================================================
-- Migration 009: Pipeline Error Tracking Enhancement
-- ============================================================
-- 1. Drops redundant failed_part_numbers column (tracked in failed_items JSONB)
-- 2. Backfills existing failed_items with stage + timestamp fields
--
-- Safe to run multiple times.
-- ============================================================

-- 1. Drop redundant column
ALTER TABLE public.batches
  DROP COLUMN IF EXISTS failed_part_numbers;

-- 2. Backfill existing failed_items entries that lack stage/timestamp
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
