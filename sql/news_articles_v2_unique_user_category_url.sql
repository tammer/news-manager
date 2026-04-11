-- Gistprism v2 — apply after public.news_articles exists (20260411.md).
-- Enables upsert on_conflict for ingest: one row per user, category, URL.

alter table public.news_articles
  add constraint news_articles_user_category_url_key
  unique (user_id, category_id, url);
