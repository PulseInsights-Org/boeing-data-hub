-- Migration: Add part_numbers to batches table
-- Date: 2024
-- Description: Stores the list of part numbers being processed in each batch for pipeline tracking

-- ============================================
-- 1. ADD PART_NUMBERS COLUMN TO BATCHES
-- ============================================
-- Stores the list of part numbers as a JSONB array
ALTER TABLE public.batches
ADD COLUMN IF NOT EXISTS part_numbers JSONB DEFAULT '[]'::jsonb;

-- ============================================
-- VERIFICATION QUERY (Run after migration)
-- ============================================
-- SELECT column_name, data_type FROM information_schema.columns
-- WHERE table_name = 'batches' AND column_name = 'part_numbers';
