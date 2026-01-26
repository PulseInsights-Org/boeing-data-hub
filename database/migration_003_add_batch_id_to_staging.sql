-- Migration: Add batch_id to product_staging
-- Date: 2024
-- Description: Adds batch_id column to product_staging to track which batch created each product

-- ============================================
-- 1. ADD BATCH_ID COLUMN TO PRODUCT_STAGING
-- ============================================
-- Note: Using TEXT type to match the batches.id column type (VARCHAR)
-- No foreign key constraint since batches.id is VARCHAR, not UUID
ALTER TABLE public.product_staging
ADD COLUMN IF NOT EXISTS batch_id TEXT;

-- Create index for batch_id queries (for fast filtering by batch)
CREATE INDEX IF NOT EXISTS idx_product_staging_batch_id
ON public.product_staging(batch_id);

-- ============================================
-- VERIFICATION QUERY (Run after migration)
-- ============================================
-- SELECT column_name, data_type FROM information_schema.columns
-- WHERE table_name = 'product_staging' AND column_name = 'batch_id';
