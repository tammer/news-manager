# Plan: API to resolve news sources (lookup only)

## Goal

Expose an HTTP endpoint so a **client** (already signed in with Supabase) can **discover** a site or feed from loose user input (URL fragment or site name) and receive a structured JSON result. **This service does not write to the database.** Persisting rows (for example in `public.sources` when using `news-manager --from-db`) is entirely the client’s responsibility (e.g. Supabase from the app with the user’s session).

**Every request** must include a valid Supabase **JWT** (`Authorization: Bearer …`). Reject unauthenticated calls with **401**; there is no API-key or anonymous bypass. The server does not need to persist `user_id` for this feature, but verification still limits abuse.

## Stack

- **Flask** for the HTTP layer.
- **Supabase**: the client holds the user session; the server validates the **JWT** on **every** call before running resolution.
- **Existing project pieces**: Groq/OpenAI-compatible LLM ([`news_manager/llm.py`](news_manager/llm.py)), patterns from the ingest pipeline for “is this a listing page?” heuristics if you want to reuse rather than re-prompt.

## How the response maps to ingest (informative only)

If the client later stores a source for **`news-manager --from-db`**, v2 rows use **`public.sources`** with `url` and `use_rss` as in [`20260411.md`](20260411.md). The resolve API only **suggests** values the client may copy into that table (or into `sources.json`); it does not perform inserts or validate **`category_id`**.

## Endpoint

### `POST /api/sources/resolve`

**Body (JSON):**

- `query` (string, required): partial URL, hostname, or natural language (“NYTimes tech”, `theguardian.com/books`).
- Optional: `locale` or `max_results` for search tuning.

**Behavior (server):**

1. **Normalize intent**: use **DuckDuckGo** (or another allowed search API) to map `query` to one or more candidate site URLs; use the **LLM** (or ranked heuristics) to pick a **single best canonical site homepage** when several results are plausible — **always commit to one guess**, do not fail or pause for disambiguation (strip tracking params, prefer `https`, resolve redirects once).
2. **Listing check**: use the **LLM** (and optionally a light HTML fetch of the candidate homepage) to decide whether the page looks like a **story index** (many article links) vs a one-off page, paywall interstitial, or app shell with no crawlable links.
3. **Feed discovery**: search (DDG + `site:` queries like `feed`, `rss`, `atom`) and/or probe common paths (`/feed`, `/rss`, `/atom.xml`, WordPress defaults). Prefer **Atom/RSS** when clearly the publication’s main feed.
4. **Response**: structured JSON only — **no Supabase writes** from this route.

## Response shape

Use consistent keys; example **success**:

```json
{
  "ok": true,
  "website_title": "The Guardian",
  "homepage_url": "https://www.theguardian.com/international",
  "resolved_url": "https://www.theguardian.com/international/rss",
  "use_rss": true,
  "rss_found": true,
  "confidence": "high",
  "notes": "Main international RSS; homepage also suitable as html source."
}
```

- **`resolved_url`**: recommended URL for ingestion (feed if RSS is the best option, otherwise homepage).
- **`use_rss`**: `true` iff **`resolved_url`** is a feed.
- **`rss_found`**: whether a feed was discovered (even if the recommended mode is HTML).
- **`confidence`**: `high` | `medium` | `low` — use **`low`** or **`medium`** when the query was vague or search returned several plausible sites; the server still returns one **best guess**.

**Failure** (no usable listing / blocked site / no search results):

```json
{
  "ok": false,
  "error": "not_a_listing",
  "message": "Could not find a homepage that looks like a story listing."
}
```

Use a small closed set of **`error`** codes (`no_results`, `not_a_listing`, `no_feed_and_no_html`, `upstream_timeout`) so the client can branch without parsing free text. **Do not** use a dedicated “ambiguous” error: multiple candidates are resolved by best guess instead.

## Edge cases and policy

- **No article index**: return **`ok: false`** as above.
- **Multiple plausible sites**: still return **`ok: true`** with a **single** **`resolved_url`** / **`homepage_url`**; set **`confidence`** to **`medium`** or **`low`** and use **`notes`** to mention uncertainty or runner-up sites if useful. Never require the client to disambiguate.
- **RSS vs HTML**: if RSS exists, default **`use_rss: true`** and `resolved_url` = feed (matches README guidance for JS-heavy sites). If only HTML works, set **`use_rss: false`** and use homepage.
- **Paywalls / cookies**: discovery does not need to solve paywall cookies; optional **`notes`** can mention paywalled full text (ingest already supports cookies via file config for file-based runs; DB-backed flow may need a follow-up if you store cookie paths in metadata later).
- **Security**: never follow `file:` or internal IPs; validate URLs before server-side fetch; strip credentials from URLs in logs.

## Implementation checklist

- [ ] JWT verification middleware (Supabase JWKS or shared secret per Supabase docs) — **401** on missing, expired, or invalid tokens; **no** unauthenticated access.
- [ ] DDG client (respect ToS; consider official alternatives if needed).
- [ ] LLM prompts: (a) pick best URL from search snippets, (b) classify listing vs non-listing with strict JSON output.
- [ ] Feed detection: HEAD/GET with size limit, parse `<link rel="alternate" type="application/rss+xml">` when fetching homepage.
- [ ] Tests: mocked search + LLM; golden cases for major sites; error paths.

## Resolved product choices

- **Ambiguity**: always a **best guess** (`ok: true`) with honest **`confidence`** and optional **`notes`**; no `ambiguous_query` / `needs_disambiguation` / client pick list.
- **Auth**: **JWT required on every request**; no API-key or anonymous path.

For v2 schema field meanings when the **client** saves sources, see [`20260411.md`](20260411.md) and [`gistprism_v2_implementation_plan.md`](gistprism_v2_implementation_plan.md); this resolve API stays out of that path.
