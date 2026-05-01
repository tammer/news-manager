# API overview (for humans)

Server entrypoint: run **`resolve-api`** (Flask). Default URL **`http://localhost:5000`** unless **`RESOLVE_API_PORT`** is set.

All routes below support **`OPTIONS`** for browser CORS preflight when the request **`Origin`** is allowed (see server config / **`RESOLVE_CORS_ORIGIN`**).

| Endpoint | What it does |
|----------|----------------|
| **`POST /api/sources/resolve`** | Turn a loose **`query`** (URL fragment or site name) into a suggested homepage or RSS URL for ingest—lookup only; does not write to your database. Homepage fetching uses direct HTTP first with optional Scrapingdog fallback when enabled. Failures may include a `details` object (stage/reason/url/status/headers/body preview) to aid troubleshooting. |
| **`POST /api/sources/discover`** | Start an **async** source-discovery job from plain-English intent. Discovery runs from DDG query `blogs or news sites about <intent>`. Each result URL is classified from page title/meta as `blog home`, `news home`, `article`, or `other`. Home pages become suggestions, `other` is skipped, and `article` pages are analyzed for recommended links; each recommended URL is then reclassified and accepted only if it is a `blog home` or `news home`. Stops after 5 suggestions (or exhaustion). Existing user sources are excluded by URL/domain. Returns `job_id`; use status endpoint for results. |
| **`GET /api/sources/discover/<job_id>`** | Get status/result for a source-discovery job you started. Only the job owner may read it. |
| **`POST /api/user/sources/import`** | Apply a **categories + sources JSON** catalog to the signed-in user (JWT **`sub`**), merging into **`public.categories`** / **`public.sources`** (service-role writes on the server). |
| **`POST /api/pipeline/run`** | Queue an **async** full **ingest** run (fetch → filter/summarize → Supabase) for the authenticated user, optionally scoped by category/source. JSON body may set **`html_discovery_llm: true`** to enable Groq-assisted link picking on **HTML** homepages (same behavior as CLI **`--html-discovery-llm`**; see **[for_agents.md](for_agents.md)**). Source listing pages and article pages now use **direct HTTP first**, then optional **Scrapingdog fallback** when enabled. |
| **`GET /api/pipeline/run/<job_id>`** | Return **status and result** for a pipeline job you started (**`job_id`** from the 202 response); only the job owner may read it. |
| **`POST /api/pipeline/evaluate-article`** | Run the **single-article** pipeline for one URL or existing **`article_id`** in a category—returns summaries and whether the article would be included; optional **persist** to Supabase. |

For machine-readable request/response fields and errors, see **[for_agents.md](for_agents.md)**.

## Optional fetch fallback (Scrapingdog)

When enabled, ingest/evaluate fetch paths can retry via Scrapingdog for hard-to-fetch sites.

- Applies to both listing/home/feed/sitemap fetches and per-article page fetches.
- Default behavior is unchanged unless explicitly enabled.
- Key env vars:
  - **`SCRAPINGDOG_ENABLED`** (`true`/`1`/`yes`/`on`)
  - **`SCRAPINGDOG_API_KEY`**
  - **`SCRAPINGDOG_TIMEOUT`** (seconds, default `60`, clamped `1..120`)
  - **`SCRAPINGDOG_FALLBACK_ON`** (comma-separated status codes; default `403,429`)
