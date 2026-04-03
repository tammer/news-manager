-- Run in Supabase SQL editor. See database_plan.md for semantics.
-- Optional: ensure pgcrypto for gen_random_uuid() (usually enabled on Supabase).
-- create extension if not exists "pgcrypto";

create table if not exists public.news_articles (
  id uuid primary key default gen_random_uuid(),
  category text not null,
  url text not null,
  headline text not null,
  article_date timestamptz null,
  source text not null default '',
  short_summary text not null default '',
  full_summary text not null default '',
  read boolean not null default false,
  liked boolean null,
  inserted_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint news_articles_url_category_unique unique (url, category)
);

create index if not exists news_articles_category_idx
  on public.news_articles (category);

create index if not exists news_articles_article_date_idx
  on public.news_articles (article_date desc nulls last);

create index if not exists news_articles_source_idx
  on public.news_articles (source);

comment on table public.news_articles is
  'Summarized articles from news-manager; natural key is (url, category).';

comment on column public.news_articles.liked is
  'User preference; NULL means unset. Maps to product term "like".';

-- Optional: keep updated_at fresh on any column update (safe alongside REST upserts).
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists news_articles_set_updated_at on public.news_articles;

create trigger news_articles_set_updated_at
  before update on public.news_articles
  for each row
  execute function public.set_updated_at();
