# Supabase Scheduled Cleanup Guide

This document is a permanent reference for deleting old rows from:

- `public.news_articles`
- `public.news_article_exclusions`

using Supabase Cron (`pg_cron`).

## Important schema notes

From the schema provided:

- `public.news_articles` does **not** have `created_at`; it has `inserted_at` and `updated_at`.
- `public.news_article_exclusions` uses `excluded_at` for timestamping.

If your live DB has `created_at`, replace `inserted_at` with `created_at` in the article cleanup query.

## Cleanup rules

### 1) Delete from `news_articles` when all are true

- older than 10 days
- `read = true`
- `saved = false`

```sql
DELETE FROM public.news_articles
WHERE "read" = true
  AND saved = false
  AND inserted_at < now() - interval '10 days';
```

### 2) Delete from `news_article_exclusions` when older than 10 days

```sql
DELETE FROM public.news_article_exclusions
WHERE excluded_at < now() - interval '10 days';
```

## Safe testing workflow

## 1. Preview row counts

```sql
SELECT count(*) AS articles_to_delete
FROM public.news_articles
WHERE "read" = true
  AND saved = false
  AND inserted_at < now() - interval '10 days';

SELECT count(*) AS exclusions_to_delete
FROM public.news_article_exclusions
WHERE excluded_at < now() - interval '10 days';
```

## 2. Dry-run inside a transaction

Use `ROLLBACK` first so nothing is permanently deleted while testing.

```sql
BEGIN;

DELETE FROM public.news_article_exclusions
WHERE excluded_at < now() - interval '10 days';

DELETE FROM public.news_articles
WHERE "read" = true
  AND saved = false
  AND inserted_at < now() - interval '10 days';

ROLLBACK;  -- switch to COMMIT when ready
```

## Supabase Cron setup

1. In Supabase dashboard, enable `pg_cron`:
   - `Integrations -> Cron` (or `Database -> Extensions`)
2. In SQL Editor, run setup SQL to ensure the extension is installed:

```sql
create extension if not exists pg_cron with schema pg_catalog;
```

3. Verify the extension is installed:

```sql
select extname, extnamespace::regnamespace as schema_name
from pg_extension
where extname = 'pg_cron';
```

4. Open SQL Editor and create a daily job:

```sql
SELECT cron.schedule(
  'daily_news_cleanup',
  '0 4 * * *',  -- daily at 04:00 UTC
  $$
  DELETE FROM public.news_article_exclusions
  WHERE excluded_at < now() - interval '10 days';

  DELETE FROM public.news_articles
  WHERE "read" = true
    AND saved = false
    AND inserted_at < now() - interval '10 days';
  $$
);
```

## Verify cron job

```sql
SELECT jobid, jobname, schedule, command
FROM cron.job
WHERE jobname = 'daily_news_cleanup';
```

Inspect run history using `cron.job_run_details` (filter by `jobid`).

## Remove cron job if needed

```sql
SELECT cron.unschedule('daily_news_cleanup');
```

## Optional production hardening

If deletions become large, move cleanup into a batched function (delete in chunks) and have cron call that function to reduce lock time and long-running jobs.

