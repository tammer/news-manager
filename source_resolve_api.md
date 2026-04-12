# Source resolve API — client integration spec

This document describes `POST /api/sources/resolve` for client apps (e.g. mobile or web) that call the `resolve-api` service.

## Purpose

The endpoint turns a **free-text hint** (site name, partial URL, or full URL) into a **recommended ingest URL** for news: either an **RSS/Atom feed** (preferred when found) or the **HTML listing homepage**.

The API is **read-only**: it does **not** create or update rows in Supabase. The client should persist `resolved_url` and `use_rss` (and `category_id`, etc.) using the normal Supabase client and the user’s session.

## Base URL

Deployment-specific. For local development, the server defaults to `http://127.0.0.1:5000` unless `RESOLVE_API_PORT` is set (for example `8080`). The path is always:

`POST /api/sources/resolve`

## Authentication (required)

- **Header:** `Authorization: Bearer <access_token>`
- **`access_token`** must be the Supabase **user session access JWT** (the same token the app receives after sign-in).
- The server verifies tokens using the project’s **JWT signing keys (JWKS)** when `SUPABASE_URL` is configured on the server, or legacy **HS256** with `SUPABASE_JWT_SECRET`.
- **401** if the header is missing, malformed, or the token is invalid or expired. The response body is still JSON with `ok: false` (see below).

## Request

- **Method:** `POST`
- **Headers:**
  - `Authorization: Bearer <access_token>`
  - `Content-Type: application/json`
- **Body (JSON object):**

| Field | Type | Required | Notes |
|--------|------|----------|--------|
| `query` | string | **Yes** | Non-empty after trim. URL fragment, hostname, or natural language (e.g. `"the guardian"`, `"https://example.com/"`). |
| `locale` | string | No | Passed through to search (e.g. region). |
| `max_results` | integer | No | Search breadth; clamped **1–25**, default **10**. |

## HTTP status codes

| Code | Meaning |
|------|--------|
| **200** | Request accepted; inspect **`ok`** in the JSON body for business success or failure. |
| **400** | Invalid JSON, wrong field types, or missing / empty `query`. |
| **401** | Missing or invalid Bearer token. |
| **500** | Unexpected server error during resolution. |

## Response body (JSON)

Every response is a **single JSON object**. The client should use **`ok` (boolean)** and, when `ok` is false, **`error`**, and should treat **HTTP status** separately (especially for auth).

### Success (`ok: true`, usually HTTP 200)

| Field | Type | Description |
|--------|------|-------------|
| `ok` | boolean | `true` |
| `website_title` | string | Human-readable site or publication name (best effort). |
| `homepage_url` | string | Canonical homepage after redirects (article index context). |
| `resolved_url` | string | **URL to store** for ingest: feed URL if RSS was chosen, otherwise `homepage_url`. |
| `use_rss` | boolean | `true` if and only if `resolved_url` is a feed. Maps to the source / DB flag **`use_rss`**. |
| `rss_found` | boolean | `true` if any RSS/Atom candidate was discovered. |
| `confidence` | string | `"high"` \| `"medium"` \| `"low"` — how confident the server is when search was ambiguous. |
| `notes` | string | Short explanation (e.g. feed found vs HTML-only; subscribe / JS-heavy homepages when a feed exists). |

**Persistence hint:** create or update a `sources`-style row with `url = resolved_url`, `use_rss = use_rss`, plus your `category_id` / `user_id` per schema.

### Failure (`ok: false`)

| Field | Type | Description |
|--------|------|-------------|
| `ok` | boolean | `false` |
| `error` | string | Machine-readable code (see below). |
| `message` | string | Human-readable detail (do not rely on exact wording for branching). |

**`error` values:**

- **`no_results`** — Empty or bad query, invalid JSON, no usable search results, resolved URL not allowed, or (on **401**) auth failure messages such as `"Invalid or expired token"` or `"Authorization Bearer token required."`
- **`not_a_listing`** — No usable feed was found **and** the homepage did not look like an article index (HTML-only path).
- **`upstream_timeout`** — Fetch, search, or internal failure; also used for unexpected errors with HTTP **500**.

Branch on **`error`** and **HTTP status** (for example treat **401** as re-authentication, not “no results”).

## Behavior notes (for UX)

- The server **prefers a working `/feed` (and similar paths)** when present, including many **Substack-style** sites where the HTML homepage is a subscribe or script-heavy shell.
- On ambiguity, the server returns **one** best guess (no candidate list). Use **`confidence`** and **`notes`** for UI hints.

## Example

```http
POST /api/sources/resolve HTTP/1.1
Host: api.example.com
Authorization: Bearer eyJhbG...
Content-Type: application/json

{"query":"https://www.thealgorithmicbridge.com/","max_results":10}
```

Example success body:

```json
{
  "ok": true,
  "website_title": "The Algorithmic Bridge",
  "homepage_url": "https://www.thealgorithmicbridge.com/",
  "resolved_url": "https://www.thealgorithmicbridge.com/feed",
  "use_rss": true,
  "rss_found": true,
  "confidence": "high",
  "notes": "RSS/Atom feed found; using feed URL for ingest (skips subscribe/JS-only homepages)."
}
```

## Server implementation reference

- Flask route: `news_manager/resolve_app.py`
- Resolution logic: `news_manager/source_resolve.py`
