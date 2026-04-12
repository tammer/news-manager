# news-manager

Fetches configured sources (HTML homepages or **RSS/Atom feeds**), discovers article URLs, filters and summarizes articles using your preferences in `instructions.md` and the **Groq** API (OpenAI-compatible).

Use **RSS feeds** for sites that load listings with JavaScript (for example **Substack**: `https://<publication>.substack.com/feed` instead of the homepage).

### Subscriber cookies (paywalled sites)

Some sites only return full article HTML when the request carries a logged-in session. Export cookies from your browser (for example with a “cookies” browser extension that saves a **JSON array** of cookie objects) and either:

- Put a file at **`cookies/<hostname>.json`** (or `cookies/www.<hostname>.json`) matching the source’s host, for example `cookies/thestar.com.json`, or
- Set **`"cookies": "path/to/file.json"`** on that source object in `sources.json` (path is relative to the current working directory unless absolute).

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

### File-based (v1 schema)

Create `sources.json` and `instructions.md` (see [plan.md](plan.md)), then:

```bash
news-manager --sources sources.json --instructions instructions.md
```

Or:

```bash
python -m news_manager --sources sources.json --instructions instructions.md
```

1. In the Supabase SQL editor, run [`sql/news_articles.sql`](sql/news_articles.sql) and [`sql/news_article_exclusions.sql`](sql/news_article_exclusions.sql).
2. Set **`SUPABASE_URL`** and **`SUPABASE_SERVICE_ROLE_KEY`** in `.env` (see [`.env.example`](.env.example)).

Every run **requires** Supabase: URLs already present in **`news_articles`** or **`news_article_exclusions`** for that category are skipped (no fetch, no LLM). New work is written **incrementally** (one upsert per included article or excluded URL). Failed upserts are reported on stdout and are **not** retried.

Included rows use natural key **`(url, category)`**; summary fields are refreshed on conflict. **`read`** is not sent so existing values stay intact. Excluded URLs are stored in **`news_article_exclusions`** so repeat runs skip them without calling the LLM.

### Supabase-backed sources (Gistprism v2)

When **`public.sources`** and **`public.user_instructions`** are populated (and each source’s **`category_id`** points at an existing **`categories`** row for that user), run ingest without local JSON/Markdown:

```bash
news-manager --from-db
```

Do **not** pass **`--sources`** or **`--instructions`** with **`--from-db`**.

Apply SQL in order: schema in [`20260411.md`](20260411.md), then [`sql/news_articles_v2_unique_user_category_url.sql`](sql/news_articles_v2_unique_user_category_url.sql), then [`sql/news_article_exclusions_v2.sql`](sql/news_article_exclusions_v2.sql). Ingest uses **`user_id`**, **`category_id`**, and upsert key **`(user_id, category_id, url)`** on **`news_articles`**, and **`(category_id, url)`** on exclusions.

Global instructions come from **`user_instructions`**; each source may set **`sources.instruction`**. If that field is non-empty for a source, **only** those instructions are sent to the model for that source; otherwise the global instructions are used (see [`news_manager/pipeline.py`](news_manager/pipeline.py) **`resolve_llm_ingest_instructions`**).

Only users with at least one **`sources`** row are processed. Set **`SUPABASE_URL`** and **`SUPABASE_SERVICE_ROLE_KEY`** as above.

### Stdout progress lines

For each article URL the tool prints a short **multi-line block** to **stdout** (see [`news_manager/run_report.py`](news_manager/run_report.py)):

- **Already in `news_articles`:** URL, then `Already in database`.
- **Already in exclusions:** URL, then `Already excluded`.
- **Processed this run:** URL, then category, then `success included`, `success excluded`, or `failure: …` (for example a Supabase error or LLM/parse failure).

With **`-v`**, INFO logs still go to **stderr**.

### `sources.json` format

Each category has a `sources` array. Each entry can be:

- A **string**: treated as an **HTML** homepage with **`filter` implicitly `true`**; the tool scrapes `<a href>` links on that page.
- An **object** with **`url`** and optional **`kind`** (`html` or `rss`), optional **`filter`** (boolean, default **`true`**):
  - **`filter: true`**: the LLM may **exclude** articles that do not match `instructions.md`.
  - **`filter: false`**: every article from **that source** is **summarized and included** (no exclusion step).

Use `"kind": "rss"` for **RSS or Atom feed URLs** (recommended for Substack, many blogs, and podcasts with feeds). To set **`filter`** on a source, use the object form (not a bare string).

Example:

```json
{
  "category": "Technology",
  "sources": [
    "https://www.example.com/news",
    {
      "url": "https://author.substack.com/feed",
      "kind": "rss",
      "filter": false
    }
  ]
}
```

### Options

| Flag | Description |
|------|-------------|
| `--from-db` | Load sources and instructions from Supabase (v2); omit `--sources` / `--instructions` |
| `--sources` | Path to `sources.json` (required unless `--from-db`) |
| `--instructions` | Path to `instructions.md` (required unless `--from-db`) |
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
