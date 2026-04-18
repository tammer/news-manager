-- Ensure category deletion cascades to dependent data.
-- Applies to existing schemas where category-linked FKs were created without ON DELETE CASCADE.

BEGIN;

ALTER TABLE public.sources
  DROP CONSTRAINT IF EXISTS sources_category_id_fkey,
  ADD CONSTRAINT sources_category_id_fkey
    FOREIGN KEY (category_id) REFERENCES public.categories (id)
    ON DELETE CASCADE;

ALTER TABLE public.news_articles
  DROP CONSTRAINT IF EXISTS news_articles_category_id_fkey,
  ADD CONSTRAINT news_articles_category_id_fkey
    FOREIGN KEY (category_id) REFERENCES public.categories (id)
    ON DELETE CASCADE;

ALTER TABLE public.news_article_exclusions
  DROP CONSTRAINT IF EXISTS news_article_exclusions_category_id_fkey,
  ADD CONSTRAINT news_article_exclusions_category_id_fkey
    FOREIGN KEY (category_id) REFERENCES public.categories (id)
    ON DELETE CASCADE;

ALTER TABLE public.news_article_exclusions
  DROP CONSTRAINT IF EXISTS news_article_exclusions_source_id_fkey,
  ADD CONSTRAINT news_article_exclusions_source_id_fkey
    FOREIGN KEY (source_id) REFERENCES public.sources (id)
    ON DELETE CASCADE;

COMMIT;
