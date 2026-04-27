# CLI — full reference (for agents)

This document describes console entry points declared in **`pyproject.toml`** under **`[project.scripts]`** and implemented in **`news_manager`**.

## Shared conventions

- **`.env`:** `news_manager.config.load_dotenv_if_present()` loads **`.env`** from the current working directory when each tool starts (does not override existing environment variables).
- **Working directory:** Relative paths (e.g. cookie files) resolve against the **process cwd**.
- **Python:** Project requires **Python ≥ 3.11** (see `pyproject.toml`).

---

## `news-manager`

**Entry:** `news_manager.cli:main`  
**Program name:** `news-manager` (argparse `prog`)

### Subcommand routing and backward compatibility

`main()` normalizes argv before parsing:

- If argv is **empty** → treated as **`["ingest"]`**.
- If the first token is **`ingest`**, **`user-sources`**, **`--help`**, or **`-h`** → argv is unchanged.
- Otherwise → **`["ingest", *argv]`** so legacy invocations like `news-manager --category X` run **`ingest`**.

Top-level subcommands (required after normalization):

1. **`ingest`**
2. **`user-sources`** (requires a nested subcommand: **`export`** or **`import`**)

### `news-manager ingest`

**Behavior:** Loads **`SUPABASE_URL`** + **`SUPABASE_SERVICE_ROLE_KEY`**, **`GROQ_API_KEY`**, creates a Supabase service-role client, and runs **`run_pipeline_from_db`** with the given selectors and limits.

**Source URLs (`public.sources`):** Ingest performs a **single GET** per source, then classifies the response. **`use_rss = false`** (**auto**): try RSS/Atom entries, else URL sitemap **`<loc>`** URLs (Sitemap 0.9 `urlset`), else same-site HTML links. **`use_rss = true`** (**force feed/XML**): only RSS/Atom or sitemap on that URL (no HTML link crawl). Sitemap **index** documents (`<sitemapindex>`) are detected but not followed (empty discovery until a leaf sitemap URL is configured).

**Environment (required):**

| Variable | Role |
|----------|------|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Service role key (REST + same process behavior as server-side jobs) |
| `GROQ_API_KEY` | Groq API key for LLM calls |

**Optional environment:**

| Variable | Role |
|----------|------|
| `GROQ_MODEL` | Model for article filter/summarize (default in `news_manager.config`). |
| `GROQ_MODEL_HTML_DISCOVERY` | If set, model used only for **HTML homepage link picking** when **`--html-discovery-llm`** is on; otherwise **`GROQ_MODEL`** is used. |
| `HTML_DISCOVERY_MAX_CANDIDATES` | Max homepage anchor rows sent to that step (default **200**, clamped to **1–500**). |
| `SCRAPINGDOG_ENABLED` | Enable fallback provider for listing/article fetches (`true`/`1`/`yes`/`on`). |
| `SCRAPINGDOG_API_KEY` | Scrapingdog API key used when fallback is enabled. |
| `SCRAPINGDOG_TIMEOUT` | Fallback request timeout in seconds (default **60**, clamped **1–120**). |
| `SCRAPINGDOG_FALLBACK_ON` | Comma-separated HTTP status codes that trigger fallback on direct response (default **403,429,500,502,503,504**). |

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--from-db` | flag | off | **Deprecated no-op.** Present for compatibility; ingest is always DB-backed. |
| `--category` | string | unset | Limit to one category (match by category id or name—see pipeline). |
| `--source` | string | unset | Limit to one source (match by source id or name—see pipeline). |
| `--user-id` | string | unset | Limit to one user (**exact** `user_id` / `auth.users.id`). |
| `--reprocess` | flag | off | When set, pipeline deletes cached **`news_articles`** / **`news_article_exclusions`** rows for matched work and re-fetches + LLM (see help text in `cli.py`). |
| `--html-discovery-llm` | flag | off | When **auto** discovery ends on an **HTML** listing (not RSS/sitemap), optionally call Groq to pick article links (extra cost). Ignored for **`use_rss: true`** sources. On failure, falls back to the usual heuristic ordering. |
| `--max-articles` | int | `15` | Max articles to process per source (pipeline cap). |
| `--timeout` | float | `30.0` | HTTP client timeout (seconds). |
| `--content-max-chars` | int | `12000` | Max article body characters sent to the LLM. |
| `--verbosity` | int (`0`,`1`,`2`) | `1` | Controls ingest operator output and debug logging. `0`: silent ingest progress output. `1`: human-readable progress lines (run start, category/source/article, include/exclude decision reason, and per-source summary). `2`: same as `1` plus process-wide **DEBUG** logging (including detailed discovery diagnostics). |

**Stdout/stderr:** Human-readable ingest progress (verbosity `1`/`2`) is printed to **stdout**. Logger output goes through Python logging config (with **DEBUG** enabled at verbosity `2`; otherwise **WARNING** and above). Errors still surface via stderr/exit codes in command handlers.

**Fallback semantics:** For both source listing fetches and article-page fetches, ingest attempts direct HTTP first. Scrapingdog fallback is then attempted for configured status/error/content-classification failures. With fallback disabled (default), behavior is unchanged.

**Exit codes:**

| Code | Meaning |
|------|---------|
| `0` | Ingest finished without an unhandled exception. |
| `1` | Missing env, `RuntimeError` from pipeline/Supabase, or other handled failure (message on stderr). |
| `2` | Argparse could not dispatch a handler (e.g. help path); uncommon. |

**Implementation:** `news_manager/cli.py` (`_cmd_ingest`, `_build_parser`).

---

### `news-manager user-sources export`

**Behavior:** Resolves **`--email`** to **`auth.users.id`** via GoTrue **admin** HTTP API (`fetch_user_id_by_email`), then exports **`public.categories`** + **`public.sources`** for that user as JSON (**`export_user_sources_catalog`**).

**Environment (required):** Same **`SUPABASE_URL`** + **`SUPABASE_SERVICE_ROLE_KEY`** as ingest. **No `GROQ_API_KEY`** required.

**Flags:**

| Flag | Required | Description |
|------|----------|-------------|
| `--email` | **yes** | Auth user email (trimmed). |
| `--compact` | no | If set, JSON is written **without** indentation (single line). |

**Output:** UTF-8 JSON on **stdout** (pretty-printed with indent `2` unless `--compact`). Trailing newline written.

**Exit codes:** `0` success; `1` on configuration, auth admin, Supabase, or unexpected errors (stderr).

**Implementation:** `news_manager/cli.py` (`_cmd_user_sources_export`), `news_manager/user_sources_catalog.py`.

---

### `news-manager user-sources import`

**Behavior:** Reads a JSON **catalog** from **`--file`** or, if omitted, **stdin** (full read until EOF). Resolves **`--email`** to user id, then **`import_user_sources_catalog`** with the service-role client.

**Environment (required):** **`SUPABASE_URL`**, **`SUPABASE_SERVICE_ROLE_KEY`**. **No `GROQ_API_KEY`** required.

**Flags:**

| Flag | Required | Description |
|------|----------|-------------|
| `--email` | **yes** | Target auth user email. |
| `--file` | no | Path to JSON file; if omitted, body is read from **stdin**. |

**Catalog JSON:** See **`docs/api/for_agents.md`** section **`POST /api/user/sources/import`** (same `schema_version` / `categories` / `sources` shape and merge semantics).

**Stderr:** On success, one line:

```text
import ok: categories_created=… categories_reused=… sources_inserted=… sources_skipped=…
```

**Exit codes:** `0` success; `1` on bad JSON, validation, auth lookup, Supabase, or other errors (stderr).

**Implementation:** `news_manager/cli.py` (`_cmd_user_sources_import`), `news_manager/user_sources_catalog.py`.

---

## `fetch-test`

**Entry:** `news_manager.fetch_test:main`

**Purpose:** Single GET + extraction for **one article URL**, optionally using a **cookie jar** (subscriber sessions). Prints **`OK`** and title/body preview on success.

**Environment:** Optional **`.env`** only; **no** Supabase or Groq required for this tool.

**Flags:**

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--url` | **yes** | — | Article URL to fetch. |
| `--cookies-file` | no | — | Explicit path to browser-exported cookie JSON. Overrides host-based lookup. |
| `--cookies-dir` | no | `NEWS_MANAGER_COOKIES_DIR` or **`cookies/`** in cwd | Directory used when resolving `cookies/<host>.json` from the URL host. |
| `--timeout` | no | `30.0` | HTTP timeout seconds. |

**Cookie resolution:** If `--cookies-file` is omitted, **`resolve_cookie_file_for_home_url(url, cookies_dir)`** is used; if no file, the tool still runs but logs that no cookie file was found (see `fetch_test.py`).

**Stdout/stderr:** On success prints **`OK`**, **`title:`**, **`chars:`**, and full **`raw:`** article body to stdout. Failures print **`FAIL:`** … to stderr.

**Exit codes:**

| Code | Meaning |
|------|---------|
| `0` | Article fetched and non-empty body extracted. |
| `1` | Cookie jar load error (`ValueError`) or unusable cookies. |
| `2` | Network/extraction failure, empty body, or `raw is None`. |

**Implementation:** `news_manager/fetch_test.py`.

---

## `resolve-api`

**Entry:** `news_manager.resolve_app:main`

**Purpose:** Runs **`create_app()`** and **`Flask.run`**, exposing the HTTP API documented in **`docs/api/for_agents.md`**.

**Not** an argparse CLI beyond what Flask provides; there are **no subcommands**.

**Environment (startup):**

| Variable | Role |
|----------|------|
| `GROQ_API_KEY` | Required at startup (`groq_api_key()`). |
| `SUPABASE_URL` and/or `SUPABASE_JWT_SECRET` | At least one required for JWT verification (`assert_resolve_api_supabase_auth_config()`). |
| `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` | Required at **request** time for routes that call `create_supabase_client()` (catalog import, pipeline, evaluate-article). |

**Optional:** `RESOLVE_API_PORT` (default **`5000`**), `RESOLVE_CORS_ORIGIN`, `GROQ_MODEL`, etc.

**Implementation:** `news_manager/resolve_app.py` (`main`).

---

## Cross-reference

| Topic | Doc |
|-------|-----|
| HTTP API | [`docs/api/for_agents.md`](../api/for_agents.md) |
| Human API summary | [`docs/api/for_humans.md`](../api/for_humans.md) |
| Product / DB behavior (high level) | [`README.md`](../../README.md) |
