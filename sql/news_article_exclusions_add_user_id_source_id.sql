-- Gistprism v2 — add user_id and source_id to news_article_exclusions.
-- Apply after sql/news_article_exclusions_v2.sql (and news_article_exclusions_add_why.sql if used).
-- Natural key remains (category_id, url); user_id supports RLS; source_id records lineage.

BEGIN;

-- 1. user_id (backfill from categories)
ALTER TABLE public.news_article_exclusions
  ADD COLUMN IF NOT EXISTS user_id uuid;

UPDATE public.news_article_exclusions e
SET user_id = c.user_id
FROM public.categories c
WHERE c.id = e.category_id
  AND e.user_id IS NULL;

ALTER TABLE public.news_article_exclusions
  ALTER COLUMN user_id SET NOT NULL;

ALTER TABLE public.news_article_exclusions
  ADD CONSTRAINT news_article_exclusions_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES auth.users (id);

-- 2. source_id (backfill: deterministic source per category+user via min(id))
ALTER TABLE public.news_article_exclusions
  ADD COLUMN IF NOT EXISTS source_id uuid;

UPDATE public.news_article_exclusions e
SET source_id = (
  SELECT s.id
  FROM public.sources s
  WHERE s.category_id = e.category_id
    AND s.user_id = e.user_id
  ORDER BY s.id
  LIMIT 1
);

-- Orphan exclusions with no matching source cannot satisfy NOT NULL + FK.
DELETE FROM public.news_article_exclusions
WHERE source_id IS NULL;

ALTER TABLE public.news_article_exclusions
  ALTER COLUMN source_id SET NOT NULL;

ALTER TABLE public.news_article_exclusions
  ADD CONSTRAINT news_article_exclusions_source_id_fkey
    FOREIGN KEY (source_id) REFERENCES public.sources (id)
    ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS news_article_exclusions_user_category_idx
  ON public.news_article_exclusions (user_id, category_id);

-- 3. RLS: owner match on denormalized user_id (replaces join-only policy)
DROP POLICY IF EXISTS news_article_exclusions_select ON public.news_article_exclusions;

CREATE POLICY news_article_exclusions_select ON public.news_article_exclusions
  FOR SELECT TO authenticated
  USING (user_id = (SELECT auth.uid()));

COMMIT;
