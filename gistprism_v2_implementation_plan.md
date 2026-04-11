# Gistprism v2 — implementation plan

This document expands [20260411.md](20260411.md) into an actionable engineering plan for the `news-manager` codebase. It assumes the Supabase project URL and SQL schema in that file are authoritative.

## 1. Objectives

- **Multi-tenant by user**: Each run processes work for **all users** (or all users with pending work), not a single implicit tenant.
- **Database as source of truth** for **sources** and **instructions** (replacing or deprecating `sources.json` and `instructions.md` for runtime).
- **Two instruction layers**: **global** and **per-source** instructions are **combined** in the model prompt; where they **conflict**, **per-source takes precedence** (state this explicitly in the prompt text — see [§5](#5-instructions-global--per-source-precedence)).
- **Align with v2 schema**: `categories`, `news_articles`, `sources`, `user_instructions`, RLS, and indexes as defined in 20260411.md.

## 2. Environment

| Item | Action |
|------|--------|
| Supabase URL | Point clients at `https://uaizrqhyomcgaowjetyd.supabase.co` (e.g. `.env` / deployment secrets). |
| Keys | **Service role** key for ingest/sync (insert/update bypasses RLS where needed). **Anon + user JWT** only if you add an authenticated app UI later. |
| Schema | Apply the SQL from 20260411.md on a **greenfield** project, then apply [sql/news_article_exclusions_v2.sql](sql/news_article_exclusions_v2.sql) for exclusions. |

## 3. Schema mapping vs current code

Today (`news_manager/supabase_sync.py`, `config.py`) the code assumes roughly:

- `news_articles` rows keyed by **`category` string** and `url`, plus `news_article_exclusions` (see `cache_change_plan.md` / tests).

v2 changes:

| Concept | v1 (approx.) | v2 |
|---------|----------------|-----|
| Category | String column on `news_articles` | `categories` table: `(id, user_id, name)`; `news_articles.category_id` FK |
| Article ownership | Implicit single user | `news_articles.user_id` |
| Sources | `sources.json` on disk | `public.sources`: `user_id`, `url`, `use_rss`, **`category_id`** (FK to `categories.id`), `instruction` |
| Filtering / summarizer rules | `instructions.md` + per-source hints in JSON | `user_instructions.instruction` (global) + `sources.instruction` (per URL) |
| RLS | (prior plan may differ) | Authenticated: categories/sources/user_instructions full CRUD on own rows; `news_articles` **select + update only** — **ingest uses service role** |

**Engineering implication**: `output_article_to_upsert_row`, prefetch/dedup queries, and any `.eq("category", ...)` logic must become **`user_id` + `category_id`**. **`sources.category` is the category row id** (UUID), not a name — the ingest job does not infer categories from strings; rows in `categories` must already exist and be referenced by sources.

**Note**: If the live Supabase schema still has `sources.category` as `text`, migrate that column to `uuid` references `categories(id)` so it matches this plan.

## 4. Batch model: “process everything for all users”

1. **List users** to process: **only `user_id`s that have at least one row in `public.sources`** (distinct `user_id` from `sources`).
2. For each **user**:
   - Load **`user_instructions`** (0 or 1 row per user per schema) → **global instruction** text.
   - Load **`sources`** for `user_id`.
   - For each source, use **`sources.category_id`** as the FK to `public.categories` (no name resolution or auto-create from this plan).
3. Run the existing fetch → filter → summarize pipeline **per source** (or batched), passing the **combined instructions** (§5).
4. **Upsert / insert** `news_articles` with `user_id`, `category_id`, and v2 columns (`headline`, `article_date`, `source`, `short_summary`, `full_summary`, `read`, `saved`, timestamps). Use **service role** client so inserts are allowed under RLS design.
5. **`news_article_exclusions`**: record excluded URLs keyed by **`category_id` + `url`** (see [sql/news_article_exclusions_v2.sql](sql/news_article_exclusions_v2.sql)); prefetch exclusions per category id like today’s per-category string flow.

## 5. Instructions: global + per-source, precedence

- **Global**: `user_instructions.instruction` for that `user_id` (default `''`).
- **Per source**: `sources.instruction` for that row (default `''`).

**Resolved rule**: Include **both** in the prompt. **Combine** them so the model sees the full policy set; add explicit instruction text that **if global and per-source conflict, follow the per-source rules** (and optionally that per-source **adds** constraints on top of global where they do not conflict). The summarizer/filter builder in `summarize.py` / pipeline should accept `(global_instruction, source_instruction)` and format a single system or user block accordingly (no “only one or the other” unless both are empty).

## 6. Workstreams (files likely touched)

| Area | Tasks |
|------|--------|
| **Config / settings** | `news_manager/config.py`: Supabase URL/key; optional flags for “read sources from DB”; remove or gate file-based paths when in v2 mode. |
| **Models** | `news_manager/models.py`: represent DB source rows, category ids, RSS flag; deprecate or split `Source` / `SourceCategory` if JSON is legacy-only. |
| **Supabase layer** | `news_manager/supabase_sync.py`: new queries (distinct users from `sources`, sources, user_instructions); row shapes for v2 `news_articles`; `news_article_exclusions` keyed by `category_id`; dedupe by `(user_id, category_id, url)` or project-defined unique constraint (add if needed). |
| **Pipeline** | `news_manager/pipeline.py`: loop users → sources → categories; thread effective instructions into fetch/filter/summarize. |
| **CLI** | `news_manager/cli.py`: entrypoint for “full multi-user run”; env validation (service role present). |
| **Tests** | `tests/test_supabase_sync.py`, `tests/test_pipeline.py`, etc.: fixtures with v2 table shapes; mock Supabase or use local test DB if available. |
| **Docs / ops** | `README.md`, `.env.example`: document v2-only flow, service role, deprecation of `sources.json` / `instructions.md` for production runs. |
| **RSS** | Honor `sources.use_rss` in fetch logic where `kind` / HTML vs RSS was file-driven. |

## 7. Resolved decisions (product answers)

| Topic | Decision |
|--------|-----------|
| Instruction conflict | **Combine** global and per-source in the prompt; **per-source takes precedence** when they conflict (say so explicitly to the model). |
| `sources.category` | It is a **category id**: **`category_id` referencing `public.categories.id`** (not a free-text name). |
| Who to process | **Only users that have at least one `sources` row** (distinct `user_id`). |
| Cutover | **Greenfield** — no migration of old v1 data into v2. |
| Exclusions | **Keep** `news_article_exclusions`; create on the new DB. v2 shape: **`(category_id, url)`** PK, RLS so owners can `select` via their category; ingest uses **service role** for writes. DDL: [sql/news_article_exclusions_v2.sql](sql/news_article_exclusions_v2.sql). |

## 8. Suggested implementation order

1. Apply 20260411.md SQL, then `sql/news_article_exclusions_v2.sql`. If `sources` still uses a text `category` column in Supabase, alter it to `uuid` → `categories(id)`.
2. Add v2 Supabase helpers: distinct users from `sources`, load sources + `user_instructions`; prefetch articles + exclusions by `category_id`.
3. Implement combined instruction prompt builder (§5).
4. Rewrite sync/upsert paths for `news_articles` v2 and exclusions v2; service role client.
5. Switch pipeline orchestration to multi-user + DB sources; keep file-based mode behind a flag until validated.
6. Update tests and run CI.

---

*Derived from 20260411.md; §7 locked to product answers on 2026-04-11.*
