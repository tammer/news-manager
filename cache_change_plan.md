# Cache → Supabase skip list (plan)

## Goal

Use **Supabase** as the source of truth for “we already processed this story in this category,” instead of the **disk cache** (`.news-manager-cache.json`). Simplify **user-visible output** to a small, consistent stream.

---

## Definitions

- **Normalized article URL** — same as today (`normalize_url`); used for lookups and dedupe inside a run.
- **Included row** — `news_articles` keyed by **`(url, category)`** (see [`sql/news_articles.sql`](sql/news_articles.sql)).
- **Excluded row** — separate table (below): **`(url, category)`** meaning “we already ran filter/summarize and excluded this story for this category.”

**Skip processing** when **`(normalized_url, category)`** appears in **either** `news_articles` **or** `news_article_exclusions`.

---

## Supabase schema additions

### `news_article_exclusions` (new)

Stores URLs the LLM **excluded** for a category so later runs do not refetch/re-LLM them.

Suggested shape (adjust names to taste in migration SQL):

```sql
create table public.news_article_exclusions (
  url text not null,
  category text not null,
  excluded_at timestamptz not null default now(),
  -- optional: reason text null,
  primary key (url, category)
);
```

**Write timing:** Insert **immediately** after an **excluded** outcome for that URL+category (same idea as incremental writes for included rows).

**Read:** Before fetch/LLM, if `(url, category)` exists here → treat like “already in database” for output purposes (wording can be `Already in database` or `Already excluded` — pick one for the UX spec).

---

## CLI: Supabase always on

- **`--write-supabase` is removed.** Every run **requires** Supabase env (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`) the same way it requires `GROQ_API_KEY` today — fail fast at startup if missing (after [`supabase_settings()`](news_manager/config.py)).
- Install path stays **`news-manager[supabase]`** (or equivalent) for releases; document in README and [`.github/workflows/news-manager.yml`](.github/workflows/news-manager.yml) (drop the flag from the command line).

---

## Processing decision (per URL)

For each candidate URL in a category (after feed/home discovery and per-category URL dedupe):

1. **If `(normalized_url, category)` exists in `news_articles`**  
   - Do **not** fetch, do **not** LLM.  
   - Emit: URL + `Already in database` (or agreed label).

2. **Else if `(normalized_url, category)` exists in `news_article_exclusions`**  
   - Do **not** fetch, do **not** LLM.  
   - Emit: URL + agreed label (e.g. `Already excluded` or same as above).

3. **Otherwise**  
   - Fetch → filter/summarize as today.  
   - **Included:** upsert `news_articles` **once** (no retries). If the upsert **fails**, **report it clearly** in the new output (e.g. `failure: Supabase upsert: …` with the error detail). No extra tables — we **accept** that the row may be missing until a later run succeeds. A **future run** may therefore **fetch and LLM again** for that URL+category; that is acceptable.  
   - **Excluded:** insert `news_article_exclusions` immediately.

---

## Upsert failures

- **No `news_articles_pending` table** (or similar). Failed upserts are **surfaced in user-visible output** so logs/CI show what went wrong.  
- **No Supabase retries** — one upsert attempt per article; on failure, **report** and continue.  
- **Next run:** if the row never landed, skip logic will **not** match → the pipeline may process the URL again (including LLM). Acceptable tradeoff for simplicity.

---

## Incremental Supabase writes

- **Included:** upsert `news_articles` per article (reuse [`output_article_to_upsert_row`](news_manager/supabase_sync.py)); omit `read` / `liked` as today.  
- **Excluded:** insert into `news_article_exclusions`.  
- **Lookup:** prefetch existing `(url, category)` sets per category from **`news_articles` and `news_article_exclusions`** where practical, to avoid N round-trips.

---

## Removals

- **Disk cache:** remove [`ArticleCache`](news_manager/cache.py) from [`run_pipeline`](news_manager/pipeline.py) / [`cli.py`](news_manager/cli.py); remove **`--cache`**, **`--no-cache`**.  
- **Supabase flag:** remove **`--write-supabase`** (always on).  
- **`output.json`:** remove **`--output`** and all writes of the category/article JSON file.  
- **`to-html`:** remove [`news_manager/to_html.py`](news_manager/to_html.py), the **`to-html`** entry in **`pyproject.toml`**, related tests, and README sections.  
- Update [`README.md`](README.md), tests, and the GitHub Action command line.

---

## Output (replace current stdout/stderr behavior)

**Intent:** One readable stream; remove old stderr progress ([`emit_cached_decision`](news_manager/summarize.py)), noisy logging, and ad hoc prints unless folded into the new format.

**Per URL, after decision:**

| Situation | Emit |
|-----------|------|
| Already in `news_articles` or exclusions (skip) | `url` then line: `Already in database` (or split labels for included vs excluded) |
| Processed this run | `url` then `category` then `success` or `failure: <reason>` (include **Supabase upsert** failures explicitly, with message) |

Pin down exact line format (plain text vs structured). **No JSON artifact** and **no HTML export** — see [Removals](#removals).

---

## Current vs proposed (reference)

| Piece | Today | Proposed |
|-------|--------|----------|
| Skip refetch/summarize | Disk cache (URL-only key) | `news_articles` ∪ `news_article_exclusions` by `(url, category)` |
| Supabase | Optional `--write-supabase`; batch at end | **Always**; incremental writes |
| Excluded URLs | Not stored | `news_article_exclusions` |
| Upsert fail after LLM | N/A (batch); next run may re-LLM | **Single upsert attempt**; **report failure**; no retries; next run may re-LLM if row still missing |
| `output.json` / **to-html** | Written then optional HTML export | **Removed** — not needed |

---

## Implementation checklist (suggested order)

1. Add SQL for **`news_article_exclusions`** (+ RLS/policies if you use them).  
2. Implement exists/prefetch + incremental upsert + exclusion insert; **clear reporting** on upsert failure (**no Supabase retries**).  
3. Wire **pipeline** / **cli**: always Supabase; remove cache and flags; drop **`output.json` / `--output`**.  
4. New **output** spec; strip old emit paths.  
5. Remove **`to-html`** package entry, module, and docs.  
6. Update **README**, **tests**, **GitHub Actions**.  
7. **Exit codes** when Supabase is down vs single-article failure (document).

---

## Open questions

- **RLS:** service role bypasses RLS today; confirm `news_article_exclusions` matches your security model.
