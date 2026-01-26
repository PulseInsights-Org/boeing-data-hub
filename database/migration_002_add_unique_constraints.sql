-- Migration 002: Add composite unique constraints for user-specific idempotency
-- This ensures no duplicate products per user while allowing different users to have the same part numbers

-- ============================================
-- IMPORTANT: Run this AFTER migration_001_add_auth.sql
-- ============================================

-- ============================================
-- Step 0: Add shopify_product_id column FIRST (needed before constraints)
-- ============================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
        AND table_name = 'product_staging'
        AND column_name = 'shopify_product_id'
    ) THEN
        ALTER TABLE product_staging ADD COLUMN shopify_product_id TEXT;
        RAISE NOTICE 'Added shopify_product_id column to product_staging';
    ELSE
        RAISE NOTICE 'shopify_product_id column already exists in product_staging';
    END IF;
END $$;

-- ============================================
-- Step 1: Handle duplicates in product_staging
-- ============================================

DO $$
DECLARE
    dup_count INTEGER;
BEGIN
    -- Check for duplicates in product_staging
    SELECT COUNT(*) INTO dup_count
    FROM (
        SELECT user_id, sku, COUNT(*) as cnt
        FROM product_staging
        WHERE user_id IS NOT NULL AND sku IS NOT NULL
        GROUP BY user_id, sku
        HAVING COUNT(*) > 1
    ) dupes;

    IF dup_count > 0 THEN
        RAISE NOTICE 'Found % duplicate user_id/sku combinations in product_staging. Keeping most recent records.', dup_count;

        -- Delete older duplicates, keeping only the most recently updated record
        DELETE FROM product_staging ps1
        WHERE EXISTS (
            SELECT 1 FROM product_staging ps2
            WHERE ps2.user_id = ps1.user_id
            AND ps2.sku = ps1.sku
            AND ps2.updated_at > ps1.updated_at
        );
    END IF;
END $$;

-- Add unique constraint on product_staging (user_id, sku)
-- Using a partial index to handle NULL user_ids (legacy data with "system" user)
ALTER TABLE product_staging
    DROP CONSTRAINT IF EXISTS product_staging_user_sku_unique;

ALTER TABLE product_staging
    ADD CONSTRAINT product_staging_user_sku_unique
    UNIQUE (user_id, sku);

-- ============================================
-- Product table: Add composite unique constraint
-- ============================================

-- Handle duplicates in product table
DO $$
DECLARE
    dup_count INTEGER;
BEGIN
    -- Check for duplicates in product
    SELECT COUNT(*) INTO dup_count
    FROM (
        SELECT user_id, sku, COUNT(*) as cnt
        FROM product
        WHERE user_id IS NOT NULL AND sku IS NOT NULL
        GROUP BY user_id, sku
        HAVING COUNT(*) > 1
    ) dupes;

    IF dup_count > 0 THEN
        RAISE NOTICE 'Found % duplicate user_id/sku combinations in product. Keeping most recent records.', dup_count;

        -- Delete older duplicates, keeping only the most recently updated record
        DELETE FROM product p1
        WHERE EXISTS (
            SELECT 1 FROM product p2
            WHERE p2.user_id = p1.user_id
            AND p2.sku = p1.sku
            AND p2.updated_at > p1.updated_at
        );
    END IF;
END $$;

-- Add unique constraint on product (user_id, sku)
ALTER TABLE product
    DROP CONSTRAINT IF EXISTS product_user_sku_unique;

ALTER TABLE product
    ADD CONSTRAINT product_user_sku_unique
    UNIQUE (user_id, sku);

-- ============================================
-- Step 4: Create indexes for faster lookups
-- ============================================

-- Create index for faster lookups by shopify_product_id
CREATE INDEX IF NOT EXISTS idx_product_staging_shopify_id
ON product_staging(shopify_product_id)
WHERE shopify_product_id IS NOT NULL;

-- Indexes for user_id (may already exist from migration_001)
CREATE INDEX IF NOT EXISTS idx_product_staging_user_id
ON product_staging(user_id);

CREATE INDEX IF NOT EXISTS idx_product_user_id
ON product(user_id);

-- ============================================
-- Summary of constraints after migration:
-- ============================================
-- product_staging: UNIQUE (user_id, sku) - prevents same part number per user
-- product: UNIQUE (user_id, sku) - prevents same part number per user
-- boeing_raw_data: NO unique constraint - allows multiple searches (audit trail)
-- ============================================

-- Verify constraints were created
DO $$
DECLARE
    ps_constraint BOOLEAN;
    p_constraint BOOLEAN;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'product_staging_user_sku_unique'
    ) INTO ps_constraint;

    SELECT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'product_user_sku_unique'
    ) INTO p_constraint;

    IF ps_constraint AND p_constraint THEN
        RAISE NOTICE 'Migration successful: All unique constraints created';
    ELSE
        RAISE WARNING 'Migration may have issues: product_staging constraint=%s, product constraint=%s', ps_constraint, p_constraint;
    END IF;
END $$;
