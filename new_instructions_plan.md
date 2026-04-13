# Category-scoped instructions (v2 schema change)

## Goal

- Store **one instruction text per category** (`public.categories.instruction`).
- **Remove** `public.user_instructions` (no global user-wide instructions).
- **Remove** `public.sources.instruction` (no per-URL overrides).
- **Data loss is acceptable** for this migration (drop column / drop table).

Ingest (`--from-db`) then always sends **only** that category’s instruction string to the LLM for every source in that category.

## Target schema (relevant tables)

```sql
-- categories: add instruction
create table public.categories (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  name text not null,
  instruction text not null default '',
  unique (user_id, name)
);

-- sources: no instruction column
create table public.sources (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  url text not null,
  use_rss boolean not null default false,
  category_id uuid not null references public.categories (id) on delete restrict
);

-- user_instructions table removed entirely
```

## Executable migration (existing Supabase project)

Run in the **SQL Editor** (service role / owner). Order matters.

```sql
-- 1) Drop RLS policies on user_instructions, then the table
drop policy if exists user_instructions_select on public.user_instructions;
drop policy if exists user_instructions_insert on public.user_instructions;
drop policy if exists user_instructions_update on public.user_instructions;
drop policy if exists user_instructions_delete on public.user_instructions;

drop table if exists public.user_instructions;

-- 2) Remove per-source instruction
alter table public.sources drop column if exists instruction;

-- 3) Add per-category instruction
alter table public.categories
  add column if not exists instruction text not null default '';
```

**Greenfield:** If you create a new project from scratch, use a single DDL script that matches the target schema above (see also `20260411.md` for the rest of v2 — you would omit `user_instructions`, omit `sources.instruction`, and include `categories.instruction`).

## Application (this repo)

Implemented in:

- `news_manager/supabase_sync.py` — `fetch_sources_with_categories` loads `categories.instruction`; `fetch_user_instructions` removed.
- `news_manager/pipeline.py` — `run_pipeline_from_db` uses one instruction per `category_id`; `resolve_llm_ingest_instructions` removed.
- `news_manager/models.py` — `IngestSource` no longer carries per-source instruction.

CLI behavior unchanged: **`--from-db`** still means “load sources from Supabase”; instructions for the model come from **categories** only.

## Verification checklist

1. Run the SQL migration on your Supabase project.
2. For each user, ensure every `sources.category_id` references a `categories` row that has the desired `instruction` text.
3. `pytest` passes locally.
4. Run ingest: `news-manager --from-db` (with env pointing at the migrated DB).

## Operator notes

- **Authenticated apps** that inserted into `user_instructions` or `sources.instruction` must be updated to read/write **`categories.instruction`** instead.
- Ingest continues to use the **service role**; RLS on `categories` applies to `authenticated`, not to the service role client.
