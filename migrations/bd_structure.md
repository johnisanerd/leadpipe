-- extensions
create extension if not exists "pgcrypto";

-- 1) Категории (опционально)
create table public.categories (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  description text,
  created_at timestamptz not null default now()
);

-- 2) Поисковые запросы (то, что вводите руками)
create table public.search_queries (
  id uuid primary key default gen_random_uuid(),
  category_id uuid references public.categories(id) on delete set null,
  query text not null,                 -- "стоматология"
  city text not null,                  -- "Москва"
  search_phrase text generated always as (trim(query) || ' ' || trim(city)) stored,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (query, city)
);

-- 3) Запуски парсинга (каждый POST /search/google)
create table public.search_runs (
  id uuid primary key default gen_random_uuid(),
  search_query_id uuid references public.search_queries(id) on delete set null,
  source text not null default 'google',   -- google | yandex | maps
  search_phrase text not null,
  city text not null,
  max_pages int not null default 1,
  pages_scraped int not null default 0,
  total_results int not null default 0,
  status text not null default 'completed'
    check (status in ('running', 'completed', 'failed', 'blocked')),
  error_message text,
  started_at timestamptz not null default now(),
  finished_at timestamptz
);

-- 4) Уникальные сайты (дедуп по домену)
create table public.sites (
  id uuid primary key default gen_random_uuid(),
  url text not null,                       -- полный URL
  domain text not null,                    -- sm-stomatology.ru
  title text,                              -- последний title из SERP
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  unique (domain)
);

-- 5) Результаты выдачи (ссылки из SERP)
create table public.serp_results (
  id uuid primary key default gen_random_uuid(),
  search_run_id uuid not null references public.search_runs(id) on delete cascade,
  site_id uuid not null references public.sites(id) on delete cascade,
  position int not null,
  page int not null default 1,
  title text,
  snippet text,
  found_at timestamptz not null default now(),
  unique (search_run_id, site_id, position)
);

-- 6) Контент/обогащение с самого сайта (email, телефон, формы и т.д.)
create table public.site_enrichments (
  id uuid primary key default gen_random_uuid(),
  site_id uuid not null references public.sites(id) on delete cascade,
  emails text[] not null default '{}',
  phones text[] not null default '{}',
  messengers jsonb not null default '{}'::jsonb,  -- {"telegram":"...", "whatsapp":"..."}
  has_contact_form boolean not null default false,
  has_feedback_form boolean not null default false,
  has_online_widget boolean not null default false,
  has_order_button boolean not null default false,
  page_title text,
  meta_description text,
  raw_text text,                                -- текст со страницы (опционально)
  llm_is_fit boolean,
  llm_fit_score int check (llm_fit_score between 0 and 100),
  llm_business_type text,
  llm_business_name text,
  llm_qualify_reason text,
  llm_qualified_at timestamptz,
  scrape_status text not null default 'pending'
    check (scrape_status in ('pending', 'running', 'done', 'failed')),
  error_message text,
  scraped_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (site_id)
);

-- 7) Статус outreach (кому уже отправляли)
create table public.site_outreach (
  id uuid primary key default gen_random_uuid(),
  site_id uuid not null references public.sites(id) on delete cascade,
  message_text text,
  message_subject text,
  sent_status text not null default 'pending'
    check (sent_status in ('pending', 'sent', 'failed', 'skipped')),
  sent_at timestamptz,
  error_message text,
  created_at timestamptz not null default now()
);

-- 8) Формы обратной связи на сайтах
create table public.site_forms (
  id uuid primary key default gen_random_uuid(),
  site_id uuid not null references public.sites(id) on delete cascade,
  page_url text not null,
  form_index int not null default 0,
  form_action text,
  form_method text not null default 'post',
  form_selector text not null,
  submit_selector text,
  fields jsonb not null default '[]'::jsonb,
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

-- индексы
create index idx_search_queries_phrase on public.search_queries(search_phrase);
create index idx_search_runs_query_id on public.search_runs(search_query_id);
create index idx_search_runs_started_at on public.search_runs(started_at desc);
create index idx_sites_domain on public.sites(domain);
create index idx_serp_results_run_id on public.serp_results(search_run_id);
create index idx_serp_results_site_id on public.serp_results(site_id);
create index idx_site_enrichments_status on public.site_enrichments(scrape_status);
create index idx_site_outreach_status on public.site_outreach(sent_status);
create index idx_site_forms_site_id on public.site_forms(site_id);
create index idx_site_forms_score on public.site_forms(form_score desc);

-- авто-update updated_at
create or replace function public.set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

create trigger trg_site_enrichments_updated_at
before update on public.site_enrichments
for each row execute function public.set_updated_at();

create trigger trg_site_forms_updated_at
before update on public.site_forms
for each row execute function public.set_updated_at();