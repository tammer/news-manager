-- One-sentence LLM explanation when an article is filtered out (reject).
alter table public.news_article_exclusions
  add column if not exists why text;

comment on column public.news_article_exclusions.why is
  'When present, a short LLM explanation of why the article was excluded for this category.';
