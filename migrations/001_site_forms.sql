-- Формы обратной связи на сайтах (для будущего outreach)
-- Выполните в Supabase SQL Editor после основной схемы из bd_structure.md

create table if not exists public.site_forms (
  id uuid primary key default gen_random_uuid(),
  site_id uuid not null references public.sites(id) on delete cascade,

  page_url text not null,
  form_index int not null default 0,
  form_action text,
  form_method text not null default 'post',
  form_selector text not null,
  submit_selector text,

  fields jsonb not null default '[]'::jsonb,
  -- [{name, type, selector, label, placeholder, purpose, required}]

  form_type text not null default 'unknown'
    check (form_type in ('contact', 'feedback', 'callback', 'order', 'unknown')),
  form_score int not null default 0 check (form_score between 0 and 100),
  has_captcha boolean not null default false,
  in_iframe boolean not null default false,
  is_submittable boolean not null default true,

  scrape_status text not null default 'discovered'
    check (scrape_status in ('discovered', 'failed', 'skipped')),
  error_message text,

  discovered_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  unique (site_id, page_url, form_index)
);

create index if not exists idx_site_forms_site_id on public.site_forms(site_id);
create index if not exists idx_site_forms_type on public.site_forms(form_type);
create index if not exists idx_site_forms_score on public.site_forms(form_score desc);
create index if not exists idx_site_forms_submittable on public.site_forms(is_submittable)
  where is_submittable = true and scrape_status = 'discovered';

drop trigger if exists trg_site_forms_updated_at on public.site_forms;
create trigger trg_site_forms_updated_at
before update on public.site_forms
for each row execute function public.set_updated_at();
