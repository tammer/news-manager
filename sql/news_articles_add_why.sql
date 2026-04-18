-- Gistprism v2 — add include rationale to news_articles.
-- Apply after sql/news_articles_v2_unique_user_category_url.sql.

alter table public.news_articles
  add column if not exists why text;

comment on column public.news_articles.why is
  'When present, a short LLM explanation of why the article was included for this category.';
