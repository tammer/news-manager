# news-manager

Fetches configured sources (HTML homepages or **RSS/Atom feeds**) from Supabase, discovers article URLs, filters and summarizes articles using category instructions, and syncs results back to Supabase via the **Groq** API (OpenAI-compatible).

Use **RSS feeds** for sites that load listings with JavaScript (for example **Substack**: `https://<publication>.substack.com/feed` instead of the homepage).

### Subscriber cookies (paywalled sites)

Some sites only return full article HTML when the request carries a logged-in session. Export cookies from your browser (for example with a “cookies” browser extension that saves a **JSON array** of cookie objects) and put a file at **`cookies/<hostname>.json`** (or `cookies/www.<hostname>.json`) matching the source’s host, for example `cookies/thestar.com.json`.

Set **`NEWS_MANAGER_COOKIES_DIR`** if you want the default directory to be something other than **`cookies/`** in the cwd. Cookie values are never logged; only the **filename** is mentioned in logs.

To sanity-check a single article URL with the same cookie loading rules as the pipeline:

```bash
fetch-test --url 'https://example.com/article'
# optional: --cookies-file path.json --cookies-dir /path/to/dir
```

Do not commit real cookie files; **`cookies/`** and root **`cookies.json`** are listed in `.gitignore`.

## Setup

Requires **Python 3.11+**.

```bash
cd news-manager
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and set your Groq API key from [Groq Console](https://console.groq.com/) plus **Supabase** credentials (see below):

```text
GROQ_API_KEY=your_key_here
GROQ_MODEL=llama-3.3-70b-versatile
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=...
```

## Usage

### Supabase-backed sources (Gistprism v2)

When **`public.sources`** are populated and each source’s **`category_id`** points at an existing **`public.categories`** row for that user (with optional **`categories.instruction`** text for the LLM), run ingest:

```bash
news-manager --from-db
```

To process only one category or one source, pass **`--category`** and/or **`--source`**
with either the row **id** or **name**:

```bash
news-manager --from-db --category "News"
news-manager --from-db --category 9f9f8... --source "The Star"
news-manager --from-db --source 2c7d6...
```

Apply SQL in order: schema in [`20260411.md`](20260411.md), then [`sql/news_articles_v2_unique_user_category_url.sql`](sql/news_articles_v2_unique_user_category_url.sql), then [`sql/news_articles_add_why.sql`](sql/news_articles_add_why.sql), then [`sql/news_article_exclusions_v2.sql`](sql/news_article_exclusions_v2.sql), then [`sql/news_article_exclusions_add_user_id_source_id.sql`](sql/news_article_exclusions_add_user_id_source_id.sql). Ingest uses **`user_id`**, **`category_id`**, and upsert key **`(user_id, category_id, url)`** on **`news_articles`**, and **`(category_id, url)`** on exclusions (with **`user_id`** and **`source_id`** stored for RLS and lineage).

Filtering/summarization instructions for **`--from-db`** come from **`public.categories.instruction`** (one text per category). All sources sharing that **`category_id`** use the same instruction (see [`new_instructions_plan.md`](new_instructions_plan.md) for the schema migration).

Only users with at least one **`sources`** row are processed. Set **`SUPABASE_URL`** and **`SUPABASE_SERVICE_ROLE_KEY`** as above.

### Stdout progress lines

For each article URL the tool prints a short **multi-line block** to **stdout** (see [`news_manager/run_report.py`](news_manager/run_report.py)):

- **Already in `news_articles`:** URL, then `Already in database`.
- **Already in exclusions:** URL, then `Already excluded`.
- **Processed this run:** URL, then category, then `success included`, `success excluded`, or `failure: …` (for example a Supabase error or LLM/parse failure).

With **`-v`**, INFO logs still go to **stderr**.

### Options

| Flag | Description |
|------|-------------|
| `--from-db` | Deprecated no-op; DB ingest is now always used |
| `--category` | With `--from-db`, process only one category (match by category id or name) |
| `--source` | With `--from-db`, process only one source (match by source id or name) |
| `--max-articles` | Max articles to fetch per source (default: 15) |
| `--timeout` | HTTP timeout in seconds (default: 30) |
| `--content-max-chars` | Max characters of article body sent to the LLM (default: 12000) |
| `-v`, `--verbose` | INFO logging to stderr |

## Testing

```bash
pytest
```

Tests mock HTTP and Groq; no API key is required for the default test run.

### Optional integration test

With `GROQ_API_KEY` set in the environment, you can run a live call (not included by default in CI). See `tests/test_summarize.py` for patterns using `pytest.mark.integration` if you add one later.

## Product spec

See [plan.md](plan.md) for input/output formats and behavior.
