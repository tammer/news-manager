# Supabase persistence — specification

This document specifies optional persistence of summarized articles to **Supabase** (managed PostgreSQL). It extends the existing pipeline output (`output.json` / `OutputArticle`) without changing default behavior when the database option is off.

---

## 1. Goals

- After a successful run (or when syncing from an existing `output.json`), **upsert** each included article into a single Supabase table.
- Preserve **user state** columns (`read`, `like`) across re-runs: re-processing the same URL must **not** reset those flags unless explicitly designed (see §6).
- **CLI-gated**: writing to the database is opt-in via a flag; credentials come from environment (and optionally `.env` via existing `load_dotenv` behavior).

## 2. Non-goals (v1)

- No Supabase **Auth** integration (single shared table; access controlled by API keys / RLS as you choose).
- No storage of full **article body** (`content`) unless you add a column later (keeps rows smaller; summaries are the product).
- No real-time subscriptions or Edge Functions in scope for this spec.

## 3. Source data mapping

Pipeline / `output.json` shape today (per article):

| JSON / `OutputArticle` | DB column        | Notes |
|------------------------|------------------|--------|
| Parent `category` (`CategoryResult.category`) | `category` | Same string as in `output.json` (e.g. `"News"`, `"Technology"`). **Part of the natural key** with `url` (see §4). |
| `title`                | `headline`       | Required string; empty titles should be stored as empty or `"(no title)"` — pick one in implementation and document. |
| `date`                 | `article_date`   | `timestamptz` if parseable ISO 8601; else `NULL`. Store original string in a comment or omit (v1: parse or null only). |
| `url`                  | `url`            | Canonical article URL; with `category`, forms the upsert key. |
| `source`               | `source`         | Hostname label (e.g. `nextbigthing.substack.com`). |
| `short_summary`        | `short_summary`  | Text. |
| `full_summary`         | `full_summary`   | Text. |
| *(none in plan)*       | `read`           | `NOT NULL`, default `false`. |
| *(none in plan)*       | `like`           | Nullable boolean; `NULL` = “unset / no opinion”. |

The same **URL** may appear under **two categories** in configuration or across runs; those are **two rows** (`(url, category)` distinct), each with its own summaries and user flags.

---

## 4. Table schema (logical)

- **Table name:** `news_articles` (single public name; adjust if you prefer `article_summaries`.)
- **Primary key:** `id` `uuid`, default `gen_random_uuid()`.
- **Unique constraint:** **`(url, category)`** — drives upsert (one logical row per article **within** a category).
- **Timestamps:** `inserted_at`, `updated_at` — both `timestamptz`, default `now()`; `updated_at` maintained on each upsert (application or trigger).

### 4.1 Reserved SQL keyword: `like`

In PostgreSQL, `LIKE` is a reserved keyword. The column can be named:

- **`"like"`** (quoted identifier) — matches the product name exactly, or  
- **`liked`** — avoids quoting everywhere.

This spec’s SQL uses **`liked`** for ergonomics; application code may expose it as “like” in JSON APIs if needed.

### 4.2 Column list (final)

| Column           | Type           | Nullable | Default   | Description |
|------------------|----------------|----------|-----------|-------------|
| `id`             | `uuid`         | no       | `gen_random_uuid()` | Surrogate PK. |
| `category`       | `text`         | no       | —         | Category from `sources.json` / `output.json` parent object. |
| `url`            | `text`         | no       | —         | Article URL; unique together with `category`. |
| `headline`       | `text`         | no       | —         | Article title. |
| `article_date`   | `timestamptz`  | yes      | `NULL`    | Parsed publication date. |
| `source`         | `text`         | no       | `''`      | Source hostname label. |
| `short_summary`  | `text`         | no       | `''`      | Short summary. |
| `full_summary`   | `text`         | no       | `''`      | Full summary. |
| `read`           | `boolean`      | no       | `false`   | User read flag. |
| `liked`          | `boolean`      | yes      | `NULL`    | User like / favorite; `NULL` = unset. |
| `inserted_at`    | `timestamptz`  | no       | `now()`   | First insert. |
| `updated_at`     | `timestamptz`  | no       | `now()`   | Last upsert. |

---

## 5. SQL — create table (Supabase / PostgreSQL)

Run in the Supabase SQL editor or via migration tool.

```sql
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
```

### 5.1 Optional: `updated_at` trigger

```sql
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
```

If the client always sends `updated_at` on upsert, the trigger is redundant but still safe.

### 5.2 Alternative: quoted `"like"` column

If you insist on the literal column name `like`:

```sql
  read boolean not null default false,
  "like" boolean null,
```

Use double quotes in all SQL and in PostgREST filters (`"like"=eq.true`).

---

## 6. Upsert semantics

- **Conflict target:** **`(url, category)`**.
- **On insert:** set `category`, `url`, and all content fields from the pipeline; `read` default `false`, `liked` default `NULL`.
- **On conflict (update):** refresh `headline`, `article_date`, `source`, `short_summary`, `full_summary`, and `updated_at`. Do **not** change `category` or `url` on update (they define the row).
- **Preserve on conflict:** **`read`** and **`liked`** — do **not** overwrite with defaults on update. Only new rows get default `read` / `liked`.

PostgreSQL `insert ... on conflict (url, category) do update` must list only the summary columns in the `UPDATE` clause, excluding `read` and `liked`.

Pseudo-SQL:

```sql
insert into public.news_articles (category, url, headline, article_date, source, short_summary, full_summary)
values ($1, $2, $3, $4, $5, $6, $7)
on conflict (url, category) do update set
  headline = excluded.headline,
  article_date = excluded.article_date,
  source = excluded.source,
  short_summary = excluded.short_summary,
  full_summary = excluded.full_summary,
  updated_at = now();
```

(`read` / `liked` absent from `excluded` assignment = preserved.)

---

## 7. CLI specification (current)

### 7.1 Supabase always on

- Every CLI run **requires** `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` (validated before the pipeline starts).
- There is **no** disk cache, **`--output`**, or end-of-run batch sync: the pipeline **prefetches** existing URLs per category from **`news_articles`** and **`news_article_exclusions`**, skips work for those URLs, and performs **one upsert per** newly included article or newly excluded URL. Failed upserts are reported on stdout and are **not** retried automatically.

### 7.2 Environment variables

| Variable | Required | Meaning |
|----------|----------|---------|
| `SUPABASE_URL` | yes | Project URL, e.g. `https://xxxx.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | yes* | Server-side key with rights to bypass RLS for upserts. |
| `SUPABASE_DB_URL` | alternative | Direct Postgres connection string (if using `psycopg` instead of HTTP). |

\*For a **locked-down** project using RLS and anon key only, specify a policy + use anon key with a dedicated role — not required for v1 if service role is acceptable for a trusted local CLI.

### 7.3 Tables

- **`news_articles`**: included articles (SQL in §5 / `sql/news_articles.sql`).
- **`news_article_exclusions`**: URLs the LLM excluded for a category (`sql/news_article_exclusions.sql`), natural key `(url, category)`.

### 7.4 Dependencies

- Official **`supabase-py`** with `service_role` and `.table(...).upsert(...)` (core dependency; see `README.md`).

---

## 8. Security and RLS (recommended reading)

- **Service role key** must never ship in frontend code; CLI + `.env` only.
- If the table is exposed via PostgREST to browsers, enable **RLS** and policies per user. For a **CLI-only, service-role** writer, RLS can be disabled for that table or a policy “service role only” — follow Supabase docs for your threat model.

---

## 9. Testing

- Unit tests: mock HTTP client or DB layer; assert upsert payload includes `category` and `(url, category)` conflict behavior; assert `read`/`liked` are not overwritten on conflict updates.
- No live Supabase in default CI; optional integration test behind `pytest.mark.integration` and env vars.

---

## 10. Checklist for implementation

- [x] Create `news_articles` with SQL in §5 (plus optional trigger).
- [x] Create `news_article_exclusions` (`sql/news_article_exclusions.sql`).
- [x] Require Supabase env vars in `news_manager/cli.py`; incremental sync in `news_manager/supabase_sync.py` + `run_pipeline`.
- [x] Document env vars in `README.md` and `.env.example` (no real secrets).
- [x] Tests with mocked Supabase client.

---

## 11. Revision history

| Version | Notes |
|---------|--------|
| 1.0 | Initial full spec from `database_plan.md` scratch notes. |
| 1.1 | Added `category` column; natural key `(url, category)`; indexes and upsert SQL updated. |
