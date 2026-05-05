-- ============================================================
-- ROLLBACK FOR MIGRATION 012: RLS LOCKDOWN
-- Reverts the database to the pre-012 state.
--
-- Recovery scope:
--   * SELECT policies are dropped.
--   * RLS is disabled on all six public tables that 012 touched.
--   * anon, authenticated grants are restored to {SELECT, INSERT,
--     UPDATE, DELETE} on every public table that 012 touched.
--   * public.users is recreated with the same shape and seed row
--     defined in complete_db_schema.sql / production_migration.sql.
--     NOTE: any production data ever written to public.users is
--     NOT recovered — this is schema-only restoration.
-- ============================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ============================================================
-- 1. DROP SELECT POLICIES
-- ============================================================

DROP POLICY IF EXISTS "anon_read_batches"               ON public.batches;
DROP POLICY IF EXISTS "anon_read_product"               ON public.product;
DROP POLICY IF EXISTS "anon_read_product_staging"       ON public.product_staging;
DROP POLICY IF EXISTS "anon_read_product_sync_schedule" ON public.product_sync_schedule;


-- ============================================================
-- 2. DISABLE RLS ON ALL TOUCHED TABLES
-- ============================================================

ALTER TABLE public.batches               DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.product               DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.product_staging       DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.product_sync_schedule DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.boeing_raw_data       DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.sync_reports          DISABLE ROW LEVEL SECURITY;


-- ============================================================
-- 3. RESTORE GRANTS TO anon, authenticated
-- ============================================================
-- Pre-012, all public tables had the default Supabase grants
-- (SELECT, INSERT, UPDATE, DELETE) for anon and authenticated.

GRANT SELECT, INSERT, UPDATE, DELETE ON public.batches               TO anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.product               TO anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.product_staging       TO anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.product_sync_schedule TO anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.boeing_raw_data       TO anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.sync_reports          TO anon, authenticated;


-- ============================================================
-- 4. RESTORE LEGACY users TABLE (schema only)
-- ============================================================
-- Matches complete_db_schema.sql §2 and production_migration.sql §2.
-- Production data in this table is NOT recovered.

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


COMMIT;

NOTIFY pgrst, 'reload schema';
