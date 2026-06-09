-- LLM-квалификация лидов (после enrichment)
-- Выполните в Supabase SQL Editor после 001_site_forms.sql

alter table public.site_enrichments
  add column if not exists llm_is_fit boolean,
  add column if not exists llm_fit_score int check (llm_fit_score between 0 and 100),
  add column if not exists llm_business_type text,
  add column if not exists llm_business_name text,
  add column if not exists llm_qualify_reason text,
  add column if not exists llm_qualified_at timestamptz;

create index if not exists idx_site_enrichments_llm_fit
  on public.site_enrichments(llm_is_fit, llm_fit_score desc)
  where llm_qualified_at is not null;
 