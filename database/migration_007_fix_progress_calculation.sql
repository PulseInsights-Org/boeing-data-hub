-- ============================================================
-- MIGRATION 007: Fix Progress Calculation for Normalized Batches
--
-- This migration updates the get_batch_stats RPC to show actual
-- normalized progress (normalized_count / total_items) instead of
-- hardcoding 100% for 'normalized' batch type.
-- ============================================================

-- Drop and recreate the function with updated progress logic
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
        -- Normalized stage: show actual normalized progress (normalized_count / total_items)
        -- This reflects how many items were successfully normalized out of total requested
        v_progress := (v_normalized::NUMERIC / v_total_for_progress) * 100;
      WHEN 'publishing', 'publish' THEN
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

-- Add comment explaining the progress calculation
COMMENT ON FUNCTION get_batch_stats(VARCHAR(36)) IS
'Returns real-time batch stats by querying product_staging directly.
Progress calculation:
- search: (normalized + failed) / total_items
- normalized: normalized_count / total_items (shows actual normalization success rate)
- publishing: (published + failed) / publish_part_numbers.length';
