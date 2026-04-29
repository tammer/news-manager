# Resolve API — full reference (for agents)

This document describes the HTTP API implemented by **`news_manager.resolve_app:create_app`** (CLI: **`resolve-api`**).

## Base URL and process

- **Default bind:** `0.0.0.0` on port **`5000`**, overridable with **`RESOLVE_API_PORT`**.
- **No path prefix** beyond the routes listed below (e.g. base `http://localhost:5000`).
- **Startup requirements** (see `resolve_app.main`): **`GROQ_API_KEY`**; and for JWT verification at least one of **`SUPABASE_URL`** (JWKS / ES256|RS256) or **`SUPABASE_JWT_SECRET`** (legacy HS256). Individual routes may additionally require **`SUPABASE_URL`** + **`SUPABASE_SERVICE_ROLE_KEY`** where noted.

## CORS

After each response, if **`Origin`** is present and matches an allowed origin, the server sets:

- `Access-Control-Allow-Origin: <Origin>`
- `Vary: Origin`
- `Access-Control-Allow-Headers: Authorization, Content-Type`
- `Access-Control-Allow-Methods: POST, GET, OPTIONS`
- `Access-Control-Max-Age: 86400`

Allowed origins default to **`http://localhost:5173`** and **`https://gistprism.tammer.com`**, plus any extra comma-separated origins in **`RESOLVE_CORS_ORIGIN`**. Origins are compared with a trailing slash stripped.

## Authentication (Supabase JWT)

Most routes require:

```http
Authorization: Bearer <access_token>
```

The token must be a valid Supabase **access token** for the **`authenticated`** audience (and issuer/algorithm per `news_manager.auth_supabase.verify_supabase_jwt`). Missing/invalid tokens yield **401** with JSON bodies as described per route.

Claims used:

- **`sub`**: authenticated user id (UUID string). Required where the route acts on behalf of a user.

---

## `OPTIONS` (preflight)

For each route that supports **`OPTIONS`**, the handler returns **204** with an empty body. CORS headers are applied by **`after_request`** when **`Origin`** is allowed.

Supported **`OPTIONS`** paths:

- `/api/sources/resolve`
- `/api/sources/discover`
- `/api/sources/discover/<job_id>`
- `/api/user/sources/import`
- `/api/pipeline/run`
- `/api/pipeline/run/<job_id>`
- `/api/pipeline/evaluate-article`

---

## `POST /api/sources/resolve`

**Purpose:** Resolve a user’s natural-language or partial URL **`query`** into a suggested **`resolved_url`** (HTML listing or RSS feed). **Read-only** with respect to Supabase—no database writes in this handler.

**Auth:** **Required** — same **`Authorization: Bearer`** as above. Unauthenticated requests → **401** with `error: "no_results"` and message about Bearer token.

**Request body:** raw JSON object (UTF-8), **`Content-Type: application/json`** recommended.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | **yes** | Non-empty search string (site name, partial URL, etc.). |
| `locale` | string | no | Passed through to resolution if present. |
| `max_results` | integer | no | Clamped to **1–25**; default **10** if omitted. |

**Success response:** **200** — JSON object. Typical success shape when `ok: true`:

| Field | Type | Description |
|-------|------|-------------|
| `ok` | boolean | `true` |
| `website_title` | string | Human-readable site title. |
| `homepage_url` | string | Canonical homepage URL. |
| `resolved_url` | string | URL to store as a source (feed or HTML listing). |
| `use_rss` | boolean | Hint for **`sources.use_rss`**: **`true`** = force feed/XML discovery only; **`false`** = auto-detect (RSS, sitemap, or HTML) on ingest. |
| `rss_found` | boolean | Whether any feed was discovered. |
| `confidence` | string | Often `"high"`, `"medium"`, or `"low"`. |
| `notes` | string | Free-text explanation. |

**Failure response:** **200** or **4xx/5xx** depending on implementation — resolver may return **200** with `ok: false` and fields such as:

| Field | Meaning (examples) |
|-------|---------------------|
| `error` | e.g. `no_results`, `not_a_listing`, `upstream_timeout` |
| `message` | Human-readable reason |
| `details` | Optional object with troubleshooting context for upstream failures (for example: `stage`, `reason`, `url`, and when available `status_code`/`final_url`/`bytes_read`/`response_headers`). For HTTP status failures, `body_preview` may include a truncated upstream body sample. |

**Resolver fetch fallback (optional):**

- `resolve_source` homepage retrieval (`fetch_html_limited`) attempts direct HTTP first.
- When enabled, it can fallback to Scrapingdog for configured status failures, empty body, or request exceptions.
- Uses the same environment knobs as ingest/evaluate fallback:
  - `SCRAPINGDOG_ENABLED`
  - `SCRAPINGDOG_API_KEY`
  - `SCRAPINGDOG_TIMEOUT`
  - `SCRAPINGDOG_FALLBACK_ON`

Malformed JSON or missing `query` → **400** with `ok: false`, `error: "no_results"`, and a descriptive `message`. Unexpected server errors during resolution → **500** with `ok: false`, `error: "upstream_timeout"`, `message: "Resolution failed unexpectedly."`

---

## `POST /api/sources/discover`

**Purpose:** Start an async source-discovery job that transforms a plain-English user intent into a ranked list of source suggestions.

**Auth:** **Required** — same **`Authorization: Bearer`** behavior as other protected routes.

**Request body:** JSON object.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `query` | string | **yes** | — | Non-empty natural-language request. |
| `locale` | string | no | `null` | Optional locale/region hint for search provider. |
| `max_results` | integer | no | `5` | Number of suggestions requested; clamped to **1–10**. |

**Success:** **202 Accepted**

```json
{
  "ok": true,
  "job_id": "<uuid>",
  "status": "queued"
}
```

**Errors:**

| Status | `error` | When |
|--------|---------|------|
| 400 | `no_results` | Body missing/invalid fields (for example non-string `query`, non-integer `max_results`). |
| 401 | `no_results` | Missing/invalid token, or missing required `sub`. |

---

## `GET /api/sources/discover/<job_id>`

**Purpose:** Return status and (when complete) result for a source-discovery job.

**Auth:** **Required** — Bearer token. Only the job owner may read the job.

**Success:** **200**

```json
{
  "ok": true,
  "job_id": "<uuid>",
  "status": "queued|running|succeeded|failed",
  "started_at": "2026-04-28T00:00:00Z",
  "finished_at": "2026-04-28T00:00:02Z",
  "params": {
    "user_id": "<jwt_sub>",
    "query": "privacy and security newsletters",
    "locale": null,
    "max_results": 5
  },
  "result": {
    "ok": true,
    "suggestions": [
      {
        "name": "Example Source",
        "url": "https://example.com/",
        "why": "Relevant to your requested topic."
      }
    ],
    "meta": {
      "query": "privacy and security newsletters",
      "candidates_considered": 12,
      "max_results": 5
    }
  },
  "error": null
}
```

If `status` is `queued` or `running`, `result` is `null`. If `status` is `failed`, `error` contains the failure message.

Suggestion field semantics:

| Field | Type | Meaning |
|-------|------|---------|
| `name` | string | Display name for the source. |
| `url` | string | Primary discovered site URL selected by discovery ranking. |
| `why` | string | Short rationale for why the source matches user intent. |

**Errors:**

| Status | `error` | When |
|--------|---------|------|
| 401 | `no_results` | Missing/invalid token. |
| 403 | `forbidden` | Authenticated user is not the job owner. |
| 404 | `not_found` | Unknown job id. |

---

## `POST /api/user/sources/import`

**Purpose:** Merge a **portable catalog** of categories and sources into **`public.categories`** and **`public.sources`** for the user identified by JWT **`sub`**. Inserts use the **service role** Supabase client on the server (bypasses RLS).

**Auth:** **Required** — **`Authorization: Bearer`**. Same rules as other JWT routes: missing/invalid token → **401** with `error: "no_results"` (see messages below). Missing or empty **`sub`** → **401** with `error: "no_results"` and message **`Token missing required 'sub' claim.`**

**Server env:** **`SUPABASE_URL`** and **`SUPABASE_SERVICE_ROLE_KEY`** must be set; otherwise **503** with `error: "server_misconfigured"` and `message` from configuration validation.

**Client integration:** Use the **same JSON body** as **`news-manager user-sources import`** (file or stdin): a catalog object with **`categories`** (and optional **`schema_version`**). Replace **`ACCESS_TOKEN`** with the user’s Supabase **access** JWT (same token you would send to **`POST /api/sources/resolve`**).

```bash
curl -sS -X POST "http://localhost:5000/api/user/sources/import" \
  -H "Authorization: Bearer ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary @catalog.json
```

```javascript
const res = await fetch("/api/user/sources/import", {
  method: "POST",
  headers: {
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify(catalog),
});
const data = await res.json();
```

**Request body:** JSON object (see `news_manager.user_sources_catalog`).

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema_version` | integer | no | Must be **`1`** if present. |
| `categories` | array | **yes** | List of category blocks. |

Each element of **`categories`**:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `category` | string | **yes** | Non-empty category **name** (matches `public.categories.name`). |
| `instruction` | string | no | Stored on **new** category rows only; existing names are **not** updated. |
| `sources` | array | **yes** | Non-empty list of sources. |

Each source:

| Field | Type | Required |
|-------|------|----------|
| `url` | string | **yes** (non-empty) |
| `use_rss` | boolean | **yes** |

**`use_rss` on each source:** **`false`** = **auto** listing (one GET → RSS/Atom entries, else sitemap **`urlset`** `<loc>` URLs, else HTML links). **`true`** = **force** RSS/Atom or sitemap on that URL only (no HTML crawl). Sitemap index URLs are not expanded.

**Semantics:** Existing category by **`(user_id, name)`** → reuse row, do not change instruction. Existing source URL for that user (**normalized** URL, any category) → skip insert.

**Success:** **200**

```json
{
  "ok": true,
  "summary": {
    "categories_created": 0,
    "categories_reused": 0,
    "sources_inserted": 0,
    "sources_skipped": 0
  }
}
```

**Errors:**

| Status | `error` | When |
|--------|---------|------|
| 400 | `invalid_json` | Body is not valid JSON. |
| 400 | `invalid_body` | Body is empty or not a JSON object. |
| 400 | `validation_error` | Payload failed validation (`message` has detail). |
| 401 | `no_results` | Missing Bearer, empty token, invalid/expired JWT, or missing/non-string `sub` (same shape as **`POST /api/sources/resolve`** / **`POST /api/pipeline/run`**). |
| 500 | `import_failed` | Supabase or runtime error during import (`message` has detail). |
| 503 | `server_misconfigured` | Missing Supabase URL/key for service client. |

---

## `POST /api/pipeline/run`

**Purpose:** Start an **asynchronous** **ingest** job: **`run_pipeline_from_db`** for the authenticated user, with optional filters. Returns immediately with a **`job_id`**.

**Auth:** **Required** — Bearer token; **`sub`** required → **401** if missing.

**Request body:** JSON object (required; **`Content-Type: application/json`**).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `category` | string | no | — | Limit to one category (name/id per pipeline). |
| `source` | string | no | — | Limit to one source (id/name/url per pipeline). |
| `user_id` | string | no | — | If set, **must equal** JWT `sub` or **403** `error: "forbidden"`. |
| `max_articles` | integer | no | `15` | Per-source article cap. |
| `timeout` | number | no | `30` | HTTP client timeout (seconds). |
| `content_max_chars` | integer | no | `12000` | Max article body chars sent to LLM. |
| `reprocess` | boolean | no | `false` | If true, pipeline may clear cached rows and reprocess (see pipeline implementation). |
| `html_discovery_llm` | boolean | no | `false` | If **`true`**, when **auto** discovery uses an **HTML** listing, run the extra Groq step to pick article links from anchors (ignored when **`use_rss: true`** on that source). Invalid model output falls back to heuristics. Same env tuning as CLI: **`GROQ_MODEL_HTML_DISCOVERY`**, **`HTML_DISCOVERY_MAX_CANDIDATES`**. |

**Logging:** With server logging at **INFO**, look for **`discovery:`** lines, **`news_manager.html_discovery`**, and **`Pipeline discover:`** (`force_feed_xml=…`) from **`news_manager.pipeline`** during jobs.

**Fetch fallback provider (optional):**

- Listing discovery (`fetch_listing_body`) and article fetch (`fetch_html`) run **direct HTTP first**.
- If enabled, fallback may call Scrapingdog for configured status/error/content-classification failures.
- Fallback is gated by env in `news_manager.config`:
  - `SCRAPINGDOG_ENABLED` (truthy enables fallback)
  - `SCRAPINGDOG_API_KEY` (required when enabled)
  - `SCRAPINGDOG_TIMEOUT` (default `60`, clamped to `1..120`)
  - `SCRAPINGDOG_FALLBACK_ON` (CSV status-code set; defaults to `403,429`)
- If fallback is disabled or key is missing, behavior remains the prior direct-fetch-only path.

**Success:** **202 Accepted**

```json
{
  "ok": true,
  "job_id": "<uuid>",
  "status": "queued"
}
```

**Errors:**

| Status | Condition |
|--------|-----------|
| 400 | Body not a JSON object; or field type validation failed (`message` in body). |
| 401 | Missing/invalid token; or missing `sub`. |
| 403 | `user_id` in body does not match authenticated user. |

**Job storage:** Jobs are kept **in memory** in the server process (`news_manager.pipeline_jobs`). Restarting the process loses jobs; **`404`** on status if unknown id.

---

## `GET /api/pipeline/run/<job_id>`

**Purpose:** Return the current **pipeline job** record for **`job_id`**.

**Auth:** **Required** — Bearer token. Only the **`owner_user_id`** that started the job may read it; else **403** `error: "forbidden"`. Unknown **`job_id`** → **404** `error: "not_found"`.

**Success:** **200** — JSON object (from `PipelineRunJob.to_json_dict`):

| Field | Type | Description |
|-------|------|-------------|
| `ok` | boolean | `true` |
| `job_id` | string | Same as path parameter. |
| `status` | string | `"queued"` \| `"running"` \| `"succeeded"` \| `"failed"` |
| `started_at` | string \| null | ISO timestamp or null. |
| `finished_at` | string \| null | ISO timestamp or null. |
| `params` | object | Echo of run parameters (`user_id`, `category`, `source`, `max_articles`, `timeout`, `content_max_chars`, `reprocess`, `html_discovery_llm`). |
| `result` | array \| null | On success, list of article decision dicts (pipeline-specific). |
| `error` | string \| null | On failure, error message string. |

---

## `POST /api/pipeline/evaluate-article`

**Purpose:** Run **single-article** evaluation (**`evaluate_single_article_from_db`**) for the authenticated user: fetch, optional filter/summarize, optional **persist** to Supabase.

**Auth:** **Required** — Bearer token; **`sub`** required.

**Request body:** JSON object.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `category_id` | string | **yes** | Category UUID for the article. |
| `url` | string | one of | Article URL (exactly one of `url` **or** `article_id`). |
| `article_id` | string | one of | Existing article row id. |
| `instructions_override` | string | no | Optional override for LLM instructions. |
| `persist` | boolean | no | default `false` — whether to write results to DB. |
| `content_max_chars` | integer | no | default `12000` |
| `timeout` | number | no | default `30` |

**Success:** **200**

```json
{
  "ok": true,
  "included": true,
  "why": "<string reason>",
  "url": "<string>",
  "title": "<string>",
  "date": "<string or null>",
  "source": "<string>",
  "short_summary": "<string>",
  "full_summary": "<string>",
  "persisted": false,
  "instruction_source": "<string>",
  "persist_error": "<optional string if persist failed but handler still returns 200>"
}
```

`persist_error` is only present when non-empty (optional field on success payload).

**Errors:**

| Status | Typical cause |
|--------|----------------|
| 400 | Validation (`message`); or `ValueError` from pipeline. |
| 401 | Auth / missing `sub`. |
| 404 | `LookupError` — e.g. article/category not found (`error: "not_found"`). |
| 500 | `RuntimeError` from pipeline. |

---

## Error shape (common)

Many error responses use:

```json
{
  "ok": false,
  "error": "<code>",
  "message": "<human-readable text>"
}
```

Not all routes use identical `error` codes; **`/api/sources/resolve`** often uses **`no_results`** for auth failures to match existing client behavior.

## Implementation reference

| Concern | Module |
|---------|--------|
| Flask app | `news_manager/resolve_app.py` |
| JWT | `news_manager/auth_supabase.py` |
| Source resolve body | `news_manager/source_resolve.py` (`resolve_source_json_body`, `resolve_source`) |
| Catalog import | `news_manager/user_sources_catalog.py` |
| Pipeline jobs | `news_manager/pipeline_jobs.py` |
| Single-article eval | `news_manager/pipeline.py` (`evaluate_single_article_from_db`) |
