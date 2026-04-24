# API overview (for humans)

Server entrypoint: run **`resolve-api`** (Flask). Default URL **`http://localhost:5000`** unless **`RESOLVE_API_PORT`** is set.

All routes below support **`OPTIONS`** for browser CORS preflight when the request **`Origin`** is allowed (see server config / **`RESOLVE_CORS_ORIGIN`**).

| Endpoint | What it does |
|----------|----------------|
| **`POST /api/sources/resolve`** | Turn a loose **`query`** (URL fragment or site name) into a suggested homepage or RSS URL for ingest—lookup only; does not write to your database. Failures may include a `details` object (stage/reason/url/status/headers/body preview) to aid troubleshooting. |
| **`POST /api/user/sources/import`** | Apply a **categories + sources JSON** catalog to the signed-in user (JWT **`sub`**), merging into **`public.categories`** / **`public.sources`** (service-role writes on the server). |
| **`POST /api/pipeline/run`** | Queue an **async** full **ingest** run (fetch → filter/summarize → Supabase) for the authenticated user, optionally scoped by category/source. JSON body may set **`html_discovery_llm: true`** to enable Groq-assisted link picking on **HTML** homepages (same behavior as CLI **`--html-discovery-llm`**; see **[for_agents.md](for_agents.md)**). |
| **`GET /api/pipeline/run/<job_id>`** | Return **status and result** for a pipeline job you started (**`job_id`** from the 202 response); only the job owner may read it. |
| **`POST /api/pipeline/evaluate-article`** | Run the **single-article** pipeline for one URL or existing **`article_id`** in a category—returns summaries and whether the article would be included; optional **persist** to Supabase. |

For machine-readable request/response fields and errors, see **[for_agents.md](for_agents.md)**.
