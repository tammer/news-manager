# Experiment 1: DDG + LLM site discovery

Standalone, throwaway program that takes a theme and tries to surface quality
news outlets and blogs about it, using DuckDuckGo for retrieval and the
project's existing Groq LLM (via `news_manager.llm.get_client`) for judgment.

This is an **experiment**, not production code. It does not modify anything in
`news_manager/` and does not persist anything to Supabase. DRY does not matter
here on purpose: small helpers (e.g. `extract_base_domain`, slug) are copied
locally instead of refactoring shared code.

## Pipeline

Single round, easy to read in the logs:

1. User provides a theme.
2. **LLM step A** — expand the theme into 4–6 diverse DDG queries (JSON output).
3. **DDG retrieval** — run each query, collect title/url/snippet hits.
3b. **Hub crawl (optional, on by default)** — DDG hits whose title/snippet/path look
   like a list or roundup (“10 best … blogs”, “top newsletters”, etc.) are fetched
   once; outbound `http(s)` links to **other** domains become extra synthetic hits
   (tagged `hit_source: hub_link`, with `hub_referrer`). Same URL is not crawled twice
   in one run. Bounded: `--max-hubs`, `--hub-max-links`; disable with `--no-hub-crawl`
   or `--max-hubs 0`.
4. **Domain rollup** — dedupe by registrable host (strips `www.`), keep the
   best title/snippet and the hit count per domain.
5. **LLM step B** — judge each domain (`keep` / `maybe` / `drop`, with `score`,
   `kind`, and a short `reason`).
6. Print a sorted table to stdout and write a structured JSON record + a full
   DEBUG log under `experiment_1/runs/`.
7. Log **total LLM calls** for the run (each `chat.completions.create`, including
   JSON parse retries). The same number is stored as `llm_call_count` in the JSON
   record.

## Run it

From the repo root, with the project installed in your env (the project already
depends on `duckduckgo-search` and `openai`):

```bash
python experiment_1/discover.py "indie game development blogs"
```

Or interactively (you'll be prompted for the theme):

```bash
python experiment_1/discover.py
```

### Useful flags

- `--max-queries 6` — cap the number of DDG queries the LLM may generate.
- `--per-query 10` — DDG `max_results` per query.
- `--top 50` — how many of the top rolled-up domains to send to the judge LLM.
- `--model llama-3.3-70b-versatile` — override the Groq model.
- `--no-llm-judge` — skip the judging step (useful when you only want to inspect
  retrieval and rollup).
- `--no-hub-crawl` — do not fetch curation/list pages to harvest outbound links.
- `--max-hubs 4` — cap how many distinct hub URLs to fetch (default 4; `0` turns hub crawl off).
- `--hub-max-links 45` — max distinct external domains to take per hub page.

## Environment

Same env vars as the rest of the project (loaded via
`news_manager.config.load_dotenv_if_present`):

- `GROQ_API_KEY` — required.
- `GROQ_MODEL` — optional override; default is `llama-3.3-70b-versatile`.

DuckDuckGo does not need an API key.

## Logs and outputs

Each run writes two files under `experiment_1/runs/`:

- `<timestamp>__<theme-slug>.log` — full DEBUG log: every generated query, every
  raw DDG hit (title/url/snippet), the full domain rollup, the raw LLM prompts
  and responses for both LLM steps, and a results table at the end.
- `<timestamp>__<theme-slug>.json` — structured record of the same run: theme,
  model, generated queries, raw hits (DDG rows plus optional `hub_link` rows),
  `hub_crawl_events` (per hub: URL, timing, success, link count), rollup, verdicts.
  Easy to diff between runs or to feed back into the LLM for follow-up.

Console output is at INFO level; the file mirror is at DEBUG and includes raw
LLM responses.

## Out of scope (intentionally)

- Hub crawl only parses list-style pages surfaced by DDG; no robots.txt engine,
  no JavaScript rendering, no deep multi-hop crawling beyond one fetch per hub URL.
- No per-shortlist homepage meta inspection (see `news_manager/discovery_prompts.py`
  in production for that idea).
- No multi-round refinement loop. If results are weak, just rerun with a more
  specific theme or more queries.
- No persistence to Supabase or the rest of the discovery pipeline.
