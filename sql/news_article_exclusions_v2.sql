-- Gistprism v2 — apply after categories exist (see 20260411.md schema).
-- Excluded-by-filter URLs per category; skip refetch on later runs.
-- Natural key: (category_id, url); category implies user via public.categories.

create table if not exists public.news_article_exclusions (
  category_id uuid not null references public.categories (id) on delete cascade,
  url text not null,
  excluded_at timestamptz not null default now(),
  constraint news_article_exclusions_pkey primary key (category_id, url)
);

create index if not exists news_article_exclusions_category_idx
  on public.news_article_exclusions (category_id);

comment on table public.news_article_exclusions is
  'URLs excluded by filter for a category; scoped by categories.id (per-user categories).';

alter table public.news_article_exclusions enable row level security;

-- Owner can read exclusions for their categories (e.g. app UI).
create policy news_article_exclusions_select on public.news_article_exclusions
  for select to authenticated
  using (
    exists (
      select 1
      from public.categories c
      where c.id = category_id
        and c.user_id = (select auth.uid())
    )
  );

-- Inserts/updates/deletes: ingest uses service role (bypasses RLS), matching news_articles insert pattern.
