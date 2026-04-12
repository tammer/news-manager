-- Run in Supabase SQL editor (see cache_change_plan.md).
-- Excluded-by-LLM URLs per category; skip refetch on later runs.

create table if not exists public.news_article_exclusions (
  url text not null,
  category text not null,
  excluded_at timestamptz not null default now(),
  why text,
  constraint news_article_exclusions_url_category_pk primary key (url, category)
);

create index if not exists news_article_exclusions_category_idx
  on public.news_article_exclusions (category);

comment on table public.news_article_exclusions is
  'URLs excluded by filter for a category; natural key (url, category).';
