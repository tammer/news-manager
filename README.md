# news-manager

Fetches configured sources (HTML homepages or **RSS/Atom feeds**), discovers article URLs, filters and summarizes articles using your preferences in `instructions.md` and the **Groq** API (OpenAI-compatible).

Use **RSS feeds** for sites that load listings with JavaScript (for example **Substack**: `https://<publication>.substack.com/feed` instead of the homepage).

## Setup

Requires **Python 3.11+**.

```bash
cd news-manager
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

To sync summaries to **Supabase**, also install the extra: `pip install -e ".[supabase]"` (or `news-manager[supabase]`).
```

Copy `.env.example` to `.env` and set your Groq API key from [Groq Console](https://console.groq.com/):

```text
GROQ_API_KEY=your_key_here
GROQ_MODEL=llama-3.3-70b-versatile
```

## Usage

Create `sources.json` and `instructions.md` (see [plan.md](plan.md)), then:

```bash
news-manager --sources sources.json --instructions instructions.md --output output.json
```

Or:

```bash
python -m news_manager --sources sources.json --instructions instructions.md
```

Default output path is `output.json` in the current working directory.

While each article is processed, a line is written to **stderr** (independent of `-v` logging):

| Situation | stderr line shape |
|-----------|-------------------|
| **From disk cache** (no article fetch, no Groq) | `[cached] [included] Title` or `[cached] [excluded] Title` |
| **Fresh run** (fetched page + LLM) | `[included] Title`, `[excluded] Title`, or `[error] Title` if the LLM call failed or the response could not be parsed |

If a line **starts with `[cached]`**, the result was loaded from the cache; otherwise it was just computed.

### Disk cache

By default, processed articles are stored in **`.news-manager-cache.json`** in the current working directory (JSON map keyed by **normalized article URL only**). If you run again and the same URL appears, it is **not** re-fetched or re-summarized.

- Change location: `--cache /path/to/cache.json`
- Disable: `--no-cache`

Changing `instructions.md`, category, or `filter` does **not** change the cache key. To pick up new wording or stricter filtering for URLs already cached, use **`--no-cache`** or delete the cache file.

### Supabase (`--write-supabase`)

Optional: after a successful run, **upsert** every included article into a Supabase table **`news_articles`** (natural key `(url, category)`). Summary fields are refreshed on repeat runs; **`read`** and **`liked`** are not sent in the payload so existing values stay intact.

1. In the Supabase dashboard, run the SQL in [`sql/news_articles.sql`](sql/news_articles.sql).
2. Install the extra: `pip install "news-manager[supabase]"`.
3. Set **`SUPABASE_URL`** and **`SUPABASE_SERVICE_ROLE_KEY`** in `.env` (see [`.env.example`](.env.example)). Treat the service role key like a password — it bypasses Row Level Security.
4. Run with **`--write-supabase`** (after `--output` is written as usual).

Exit code **`2`** means Supabase sync failed (exit **`1`** is used for config / I/O / pipeline errors).

Details: [database_plan.md](database_plan.md).

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
| `--sources` | Path to `sources.json` (required) |
| `--instructions` | Path to `instructions.md` (required) |
| `--output` | Output JSON path (default: `output.json`) |
| `--max-articles` | Max articles to fetch per source (default: 15) |
| `--timeout` | HTTP timeout in seconds (default: 30) |
| `--content-max-chars` | Max characters of article body sent to the LLM (default: 12000) |
| `--cache` | Path to JSON cache file (default: `.news-manager-cache.json`) |
| `--no-cache` | Do not read or write the cache |
| `--write-supabase` | Upsert articles to Supabase after writing JSON (requires `[supabase]` + env vars) |
| `-v`, `--verbose` | INFO logging to stderr |

### Export to HTML

After you have `output.json`, generate static pages (one file per category plus `index.html`):

```bash
to-html --input output.json --output-dir html
```

Open `html/index.html` in a browser. Options: `-i` / `--input` (default `output.json`), `-o` / `--output-dir` (default `html`). Each article’s **`source`** field is the hostname of that row’s configured source (for example `nextbigthing.substack.com`); generated pages show it in the meta line under the title.

## Testing

```bash
pytest
```

Tests mock HTTP and Groq; no API key is required for the default test run.

### Optional integration test

With `GROQ_API_KEY` set in the environment, you can run a live call (not included by default in CI). See `tests/test_summarize.py` for patterns using `pytest.mark.integration` if you add one later.

## Product spec

See [plan.md](plan.md) for input/output formats and behavior.
