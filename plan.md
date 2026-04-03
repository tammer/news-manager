# News Manager — Product and implementation spec

## Purpose

The program ingests news from configured domains, filters articles according to natural-language preferences, and produces structured summaries. Articles the user would not care about are excluded before summarization.

## Invocation (CLI)

The **default interface is a command-line program** (not a library-only or GUI-only entry point). After installation, the user runs a single CLI command from the terminal; exact name and flags are documented in code and `README` (e.g. `news-manager` or `python -m news_manager`).

Typical flags (names are illustrative; finalize in implementation):

- Paths to `sources.json`, `instructions.md`, and output JSON.
- Optional overrides for limits/timeouts where exposed.

## Inputs

### 1. `sources.json` (required)

Valid JSON array. Each object has:

| Field | Type | Meaning |
|-------|------|---------|
| `category` | string | Logical bucket (e.g. `"News"`, `"Science"`). Must match a section the user describes in `instructions.md` (see below). |
| `sources` | string[] | Domain names or origin URLs treated as “home” entry points (e.g. `"cnn.com"` or `"https://www.bbc.co.uk/news"`). No trailing requirement beyond what the fetcher can normalize. |

Example:

```json
[
  {
    "category": "News",
    "sources": ["cnn.com", "bbc.co.uk"]
  },
  {
    "category": "Science",
    "sources": ["science.com"]
  }
]
```

### 2. `instructions.md` (required)

Plain English. Describes, per **category** (or globally with category-specific paragraphs), what to include and exclude. The summarizing/filtering step must use this file as the sole source of “user interest” rules.

Example line:

> For news, I am interested in local news for Toronto and Ontario and domestic Canadian news. I am not interested in geopolitical news or US politics.

**Convention:** Either one block per category with a clear heading (e.g. `## News`) or explicit phrases like “For News, …” so the implementation can associate rules with `sources.json` categories. The implementation should document the exact parsing strategy (e.g. whole file as one prompt with category labels, or split by headings).

## Outputs

### Primary artifact

JSON written to a configurable path (default: `output.json` in the project root or cwd—pick one and document in code). Shape:

```json
[
  {
    "category": "News",
    "articles": [
      {
        "title": "string",
        "date": "ISO 8601 string or null if unknown",
        "content": "full article text as fetched",
        "url": "canonical article URL",
        "short_summary": "at most ~25 words",
        "full_summary": "target ~200 words; a bit over is acceptable if noted in code"
      }
    ]
  }
]
```

- Categories appear in the same order as in `sources.json`.
- `articles` lists only items that passed the filter for that category.
- **Empty categories:** If nothing passes the filter for a category, that category must still appear with `"articles": []`.

### Optional

- Human-readable report (e.g. Markdown) — only if needed later; not required for v1.

## Architecture (modules)

### A. Config loader

- Read and validate `sources.json` (schema: array of `{ category, sources }`).
- Read `instructions.md` as UTF-8 text.
- Load a **`.env`** file if present (e.g. via `python-dotenv`) so local development can set `GROQ_API_KEY` and `GROQ_MODEL` without exporting variables in the shell. Values from the process environment still take precedence over `.env` where the library allows.
- Load environment-based secrets for **Groq** (see [LLM provider (Groq)](#llm-provider-groq)) — never commit keys or commit `.env`.

### B. Fetching module

**Input:** One base URL or domain (from `sources.json`).

**Assumption:** The input is a listing/home page that links to individual articles (typical news site front page).

**URL normalization:** Each entry in `sources` is normalized to a fetchable home URL before the first HTTP request: accept bare hostnames (e.g. `cnn.com`) or full URLs; use `https` by default when no scheme is given; resolve to a single canonical form (document in code, e.g. `https://` + host + optional path, strip fragment). `www` handling follows normal URL parsing (preserve or normalize consistently—pick one rule and apply everywhere).

**Deduplication:** No product requirement—the same article URL may appear more than once if the pipeline produces duplicates; the implementation need not dedupe unless desired.

**Output:** Array of article objects (internal shape before summarization):

| Field | Type |
|-------|------|
| `title` | string |
| `date` | string or null |
| `content` | string (main body text) |
| `url` | string (absolute URL to the article) |

**Processing (recommended order):**

1. Fetch the HTML of the home URL (reasonable timeout, user-agent identifying the app).
2. Extract candidate links (`<a href>`), resolve relative URLs, dedupe, filter to same-site article-like paths (heuristic or allowlist—document the approach).
3. Cap how many articles to fetch per source per run (configurable default, e.g. 10–20) to control cost and latency.
4. For each selected article URL, fetch HTML and extract readable text (readability-style extraction or main-content heuristic).
5. If the plan relies on an LLM to classify links or extract content, keep prompts and model choice in one place; prefer deterministic HTML parsing where possible and use the LLM for ambiguous link selection only if needed.

**Errors:** Failed fetches should log and skip; one bad URL must not abort the whole category.

### C. Summarizing and filtering module

**Input:**

- List of articles from the fetching module (for one category batch).
- Full text of `instructions.md`.
- The **category name** this batch belongs to.

**Behavior:**

1. For each article, decide **include** vs **exclude** according to `instructions.md` for that category (and global rules if any).
2. For included articles only: produce `short_summary` (~25 words) and `full_summary` (~200 words). Excluded articles are omitted from output (not listed as rejected unless debug mode is added later).

**Output:** Same article objects as input, plus `short_summary` and `full_summary`, only for included items.

**Implementation note:** One LLM call per article (filter + summarize) is simple; batching is an optimization. Document token limits and truncation of `content` if necessary.

### D. Main program

1. Load config and instructions.
2. For each entry in `sources.json`:
   - For each source URL/domain in `sources`:
     - Run fetching → list of raw articles.
     - Run summarizing/filtering with `category` and `instructions.md`.
     - Append filtered+summarized articles to that category’s list.
3. Merge into the final output structure and write JSON.
4. Exit with non-zero status if configuration is invalid or no output file could be written; partial success (some sources failed) should still produce output with whatever succeeded, with stderr logging for failures.

## LLM provider (Groq)

- **Provider:** [Groq](https://console.groq.com/) (OpenAI-compatible chat completions API).
- **API key:** `GROQ_API_KEY` (env; never committed).
- **Model:** `GROQ_MODEL` — default `llama-3.3-70b-versatile` (override via env if needed).

```text
GROQ_MODEL=llama-3.3-70b-versatile
```

Use one shared Groq client for any LLM steps (link classification if used, filtering, summarization). Keep base URL and model name centralized in config.

## Non-functional requirements

- **Language/runtime:** use Python.
- **Dependencies:** Pin versions; use the Groq/OpenAI-compatible client with `GROQ_API_KEY` and `GROQ_MODEL`; include `python-dotenv` (or equivalent) for `.env` loading.
- **Testing:** At least unit tests for JSON parsing and for merging category output; mock HTTP/LLM in tests.
- **Security:** Do not log full API keys; sanitize file paths if inputs ever become user-supplied.

## Open decisions (resolve when coding)

1. Default output file path (e.g. `output.json` relative to cwd) and exact CLI flag names.
2. Max articles per source per run and HTTP timeout defaults.

## Glossary

- **Category:** Label from `sources.json` that groups sources and ties to filtering rules in `instructions.md`.
- **Source:** A single domain or home URL contributing article links.
