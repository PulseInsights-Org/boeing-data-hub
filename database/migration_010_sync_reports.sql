-- ============================================================
-- MIGRATION 010: SYNC REPORTS TABLE
-- Stores LLM-generated reports for completed sync cycles.
-- ============================================================

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
