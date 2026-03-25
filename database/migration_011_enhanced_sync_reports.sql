-- ============================================================
-- MIGRATION 011: ENHANCED SYNC REPORTS
-- Adds cycle lifecycle timestamps and report type to support
-- cycle-start notifications and richer completion reports.
-- ============================================================

-- Cycle lifecycle timestamps
ALTER TABLE public.sync_reports
  ADD COLUMN IF NOT EXISTS cycle_started_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS cycle_ended_at TIMESTAMPTZ;

-- Report type: 'cycle_start' or 'cycle_complete' (default for backward compat)
ALTER TABLE public.sync_reports
  ADD COLUMN IF NOT EXISTS report_type TEXT NOT NULL DEFAULT 'cycle_complete';

-- Allow cycle_start reports to omit report_text (they are lightweight)
ALTER TABLE public.sync_reports
  ALTER COLUMN report_text DROP NOT NULL;

-- Index for quick lookups by report type
CREATE INDEX IF NOT EXISTS idx_sync_reports_type
  ON public.sync_reports (report_type, created_at DESC);
