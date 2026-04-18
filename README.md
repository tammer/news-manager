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

The CLI has two top-level commands: **`ingest`** (fetch, summarize, sync) and **`user-sources`** (export/import per-user categories and sources as JSON). If you omit the subcommand name, **`ingest` is assumed**—so `news-manager --from-db` is the same as `news-manager ingest --from-db`.

### File-based (v1 schema)

Create `sources.json` and `instructions.md` (see [plan.md](plan.md)), then:

```bash
news-manager ingest --sources sources.json --instructions instructions.md
```

Same thing without the word `ingest`:

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

When **`public.sources`** are populated and each source’s **`category_id`** points at an existing **`public.categories`** row for that user (with optional **`categories.instruction`** text for the LLM), run ingest without local JSON/Markdown:

```bash
news-manager ingest --from-db
# or: news-manager --from-db
```

Do **not** pass **`--sources`** or **`--instructions`** with **`--from-db`**.

Apply SQL in order: schema in [`20260411.md`](20260411.md), then [`sql/news_articles_v2_unique_user_category_url.sql`](sql/news_articles_v2_unique_user_category_url.sql), then [`sql/news_article_exclusions_v2.sql`](sql/news_article_exclusions_v2.sql). Ingest uses **`user_id`**, **`category_id`**, and upsert key **`(user_id, category_id, url)`** on **`news_articles`**, and **`(category_id, url)`** on exclusions.

Filtering/summarization instructions for **`--from-db`** come from **`public.categories.instruction`** (one text per category). All sources sharing that **`category_id`** use the same instruction (see [`new_instructions_plan.md`](new_instructions_plan.md) for the schema migration).

Only users with at least one **`sources`** row are processed. Set **`SUPABASE_URL`** and **`SUPABASE_SERVICE_ROLE_KEY`** as above.

### User catalog JSON (export / import, v2)

Operators can **dump** or **apply** a user’s **`public.categories`** + **`public.sources`** as portable JSON (names, instructions, URLs—no database UUIDs in the payload except optional echo fields). This uses the **service role** client and the Auth **admin** API to resolve **`--email`** to a **`user_id`**. **You do not need `GROQ_API_KEY`** for these commands.

**Export** (pretty-printed JSON on stdout):

```bash
news-manager user-sources export --email 'you@example.com' > catalog.json
```

One line on stdout:

```bash
news-manager user-sources export --email 'you@example.com' --compact
```

**Import** (merge semantics: existing category **name** → reuse row, do **not** update `instruction`; existing **normalized URL** for that user in any category → skip source insert):

```bash
news-manager user-sources import --email 'you@example.com' --file catalog.json
```

Or read JSON from stdin:

```bash
cat catalog.json | news-manager user-sources import --email 'you@example.com'
```

On success, import prints a **one-line summary** to stderr (`categories_created`, `categories_reused`, `sources_inserted`, `sources_skipped`).

**JSON shape** (`schema_version` must be **`1`**):

```json
{
  "schema_version": 1,
  "user_id": "uuid-of-user",
  "email": "you@example.com",
  "categories": [
    {
      "category": "Technology",
      "instruction": "Text sent to the LLM for this category.",
      "sources": [
        { "url": "https://example.com/", "use_rss": false },
        { "url": "https://example.com/feed", "use_rss": true }
      ]
    }
  ]
}
```

- **`email`** on export is optional metadata (echo of the `--email` flag).
- Each **`sources`** entry must include **`url`** (non-empty string) and **`use_rss`** (boolean).

From Python (for example after creating a user), reuse **`import_user_sources_catalog`** from **`news_manager.user_sources_catalog`** with **`create_supabase_client()`** and the new user’s UUID.

The **`resolve-api`** Flask app also exposes **`POST /api/user/sources/import`**: send the same JSON body with **`Authorization: Bearer <access_token>`**; the server uses the token’s **`sub`** as **`user_id`**. The process must have **`SUPABASE_URL`** and **`SUPABASE_SERVICE_ROLE_KEY`** so the server can write with the service role. Response: **`{ "ok": true, "summary": { ... } }`** on success.

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

**`news-manager ingest`** (and the shorthand without `ingest`):

| Flag | Description |
|------|-------------|
| `--from-db` | Load sources and instructions from Supabase (v2); omit `--sources` / `--instructions` |
| `--sources` | Path to `sources.json` (required unless `--from-db`) |
| `--instructions` | Path to `instructions.md` (required unless `--from-db`) |
| `--max-articles` | Max articles to fetch per source (default: 15) |
| `--timeout` | HTTP timeout in seconds (default: 30) |
| `--content-max-chars` | Max characters of article body sent to the LLM (default: 12000) |
| `-v`, `--verbose` | INFO logging to stderr |

**`news-manager user-sources export`**: `--email` (required), `--compact` (single-line JSON).

**`news-manager user-sources import`**: `--email` (required), `--file` (optional; default is stdin).

### Resolve API: admin create user

The **`resolve-api`** Flask app (entrypoint `resolve-api` in `pyproject.toml`) exposes **`POST /api/admin/users`**, which creates a Supabase Auth user and then seeds **`public.categories`** and **`public.sources`** for that user using the same **v1 catalog JSON** shape as **`news-manager user-sources import`**. By default it reads [`news_manager/default_user_catalog.json`](news_manager/default_user_catalog.json); override with **`DEFAULT_USER_CATALOG_PATH`**. The loader returns only **`schema_version`** and **`categories`**, so export files that still contain **`user_id`** / **`email`** at the top level work without passing those fields into the importer.

Provisioning requires **`NEWS_MANAGER_ADMIN_API_KEY`** (sent as `Authorization: Bearer …`); **`SUPABASE_URL`** and **`SUPABASE_SERVICE_ROLE_KEY`**; and JWT settings for the other resolve routes (see [`source_resolve_api.md`](source_resolve_api.md)).

## Testing

```bash
pytest
```

Tests mock HTTP and Groq; no API key is required for the default test run.

### Optional integration test

With `GROQ_API_KEY` set in the environment, you can run a live call (not included by default in CI). See `tests/test_summarize.py` for patterns using `pytest.mark.integration` if you add one later.

## Product spec

See [plan.md](plan.md) for input/output formats and behavior.
