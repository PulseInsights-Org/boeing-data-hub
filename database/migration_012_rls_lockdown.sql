-- ============================================================
-- MIGRATION 012: RLS LOCKDOWN + DROP LEGACY users TABLE
-- Resolves Supabase Advisors `rls_disabled_in_public` and
-- `sensitive_columns_exposed` for project arbvlbnnuoyfjioltula
-- (Boeing-Data-Sync).
--
-- Posture after migration (single-tenant deployment):
--   public.users                 → DROPPED (legacy local-auth, dead code)
--   public.boeing_raw_data       → RLS on, anon revoked (backend-only)
--   public.sync_reports          → RLS on, anon revoked (backend-only)
--   public.batches               → RLS on, anon SELECT permitted, writes revoked
--   public.product               → RLS on, anon SELECT permitted, writes revoked
--   public.product_staging       → RLS on, anon SELECT permitted, writes revoked
--   public.product_sync_schedule → RLS on, anon SELECT permitted, writes revoked
--
-- The service_role bypasses RLS at the role level (BYPASSRLS), so the
-- FastAPI / Celery backend is unaffected. SPA reads via anon key keep
-- working through permissive SELECT policies; SPA writes were rerouted
-- to FastAPI in restructure-v3 steps 1-2 prior to applying this file.
--
-- Single transaction: any failure rolls back the entire migration.
-- Run inside the Supabase SQL editor or psql against the production
-- project.
-- ============================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ============================================================
-- 1. DROP LEGACY users TABLE
-- ============================================================
-- Replaces sensitive_columns_exposed (users.password) and
-- rls_disabled_in_public for this table at the root.
-- Pre-drop audit on production must be run first; see
-- AUDIT_VENDOR_ALERTS.md or the migration plan for the queries.

DROP TABLE IF EXISTS public.users;


-- ============================================================
-- 2. BACKEND-ONLY TABLES: RLS on, no policies, anon revoked
-- ============================================================
-- These tables have zero anon-key callers in the SPA.
-- Service role bypasses RLS, so the backend keeps working.

ALTER TABLE public.boeing_raw_data ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sync_reports    ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.boeing_raw_data FROM anon, authenticated;
REVOKE ALL ON public.sync_reports    FROM anon, authenticated;


-- ============================================================
-- 3. FRONTEND-FACING TABLES: RLS on, SELECT-only for anon
-- ============================================================
-- The SPA still reads these tables and subscribes to Realtime
-- events on them. Realtime delivers postgres_changes events only
-- when the role would be allowed to SELECT under RLS, so a
-- permissive SELECT policy is required to keep live updates
-- working. Writes are revoked because the SPA was rerouted to
-- FastAPI in restructure-v3 steps 1-2 (PUT /api/v1/products/staging/{id}
-- and PATCH /api/v1/products/staging/{id}/status).

ALTER TABLE public.batches               ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.product               ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.product_staging       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.product_sync_schedule ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anon_read_batches"
  ON public.batches
  FOR SELECT
  TO anon, authenticated
  USING (true);

CREATE POLICY "anon_read_product"
  ON public.product
  FOR SELECT
  TO anon, authenticated
  USING (true);

CREATE POLICY "anon_read_product_staging"
  ON public.product_staging
  FOR SELECT
  TO anon, authenticated
  USING (true);

CREATE POLICY "anon_read_product_sync_schedule"
  ON public.product_sync_schedule
  FOR SELECT
  TO anon, authenticated
  USING (true);

REVOKE INSERT, UPDATE, DELETE ON public.batches               FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE ON public.product               FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE ON public.product_staging       FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE ON public.product_sync_schedule FROM anon, authenticated;


-- ============================================================
-- 4. POST-MIGRATION VERIFICATION
-- ============================================================
-- Output: every public table flipped to relrowsecurity = t,
-- users absent.

SELECT relname,
       relrowsecurity AS rls_enabled
FROM pg_class
WHERE relnamespace = 'public'::regnamespace
  AND relkind = 'r'
ORDER BY relname;

COMMIT;

-- ============================================================
-- AFTER COMMIT: reload PostgREST schema cache
-- ============================================================
-- Either run this NOTIFY in a separate session, or use
-- Supabase Dashboard → Settings → API → "Reload schema".
-- Skipping this leaves PostgREST's in-memory grant cache stale
-- for up to ~10 minutes; the lockdown still applies at the DB
-- level either way.

NOTIFY pgrst, 'reload schema';
