-- Migration: Add Authentication and User Tracking
-- Date: 2024
-- Description: Creates users table and adds user_id columns for data isolation

-- ============================================
-- 1. CREATE USERS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS public.users (
  id TEXT PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password TEXT NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  last_login TIMESTAMP WITH TIME ZONE
);

-- Insert default user
INSERT INTO public.users (id, username, password)
VALUES ('user_001', 'sk-user1', 'pulse123')
ON CONFLICT (id) DO NOTHING;

-- ============================================
-- 2. MODIFY PRODUCT_STAGING TABLE
-- ============================================
-- Drop the unused raw_boeing_data column
ALTER TABLE public.product_staging
DROP COLUMN IF EXISTS raw_boeing_data;

-- Add user_id column
ALTER TABLE public.product_staging
ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'system';

-- Create index for user_id queries
CREATE INDEX IF NOT EXISTS idx_product_staging_user_id
ON public.product_staging(user_id);

-- ============================================
-- 3. MODIFY BATCHES TABLE
-- ============================================
ALTER TABLE public.batches
ADD COLUMN IF NOT EXISTS user_id VARCHAR(50) NOT NULL DEFAULT 'system';

CREATE INDEX IF NOT EXISTS idx_batches_user_id
ON public.batches(user_id);

-- ============================================
-- 4. MODIFY PRODUCT TABLE
-- ============================================
ALTER TABLE public.product
ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'system';

CREATE INDEX IF NOT EXISTS idx_product_user_id
ON public.product(user_id);

-- ============================================
-- 5. MODIFY BOEING_RAW_DATA TABLE
-- ============================================
ALTER TABLE public.boeing_raw_data
ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'system';

CREATE INDEX IF NOT EXISTS idx_boeing_raw_data_user_id
ON public.boeing_raw_data(user_id);

-- ============================================
-- VERIFICATION QUERIES (Run after migration)
-- ============================================
-- SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'users';
-- SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'product_staging' AND column_name = 'user_id';
-- SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'batches' AND column_name = 'user_id';
-- SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'product' AND column_name = 'user_id';
-- SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'boeing_raw_data' AND column_name = 'user_id';
