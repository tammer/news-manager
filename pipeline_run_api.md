# Pipeline run API — client integration spec

This document describes the async pipeline endpoints for running `news-manager` over HTTP.

## Purpose

Expose the same run controls available in `news-manager` CLI (`--category`, `--source`, `--max-articles`, `--timeout`, `--content-max-chars`, `--user-id`, `--reprocess`) through an authenticated API.

Runs are asynchronous:
- `POST /api/pipeline/run` starts a run and returns a job id.
- `GET /api/pipeline/run/<job_id>` returns current status and terminal result/error.

## Authentication and user scope (required)

- Header: `Authorization: Bearer <access_token>`
- Token must be a valid Supabase user access JWT.
- The effective pipeline user is always JWT `sub`.
- If request body includes `user_id`, it must match JWT `sub`; otherwise the request is rejected.

## Start run

- Method: `POST`
- Path: `/api/pipeline/run`
- Headers:
  - `Authorization: Bearer <access_token>`
  - `Content-Type: application/json`
- Body (JSON object; all fields optional):

| Field | Type | Maps to |
|---|---|---|
| `user_id` | string | must match JWT `sub` if provided |
| `category` | string | `category_selector` |
| `source` | string | `source_selector` |
| `max_articles` | integer | `max_articles` |
| `timeout` | number | `http_timeout` |
| `content_max_chars` | integer | `content_max_chars` |
| `reprocess` | boolean | default `false`; when `true`, delete existing `news_articles` / `news_article_exclusions` rows for a URL in the prefetched cache for that category, then re-fetch and run the LLM instead of skipping |

### Start response

- HTTP `202 Accepted`
- Body:

```json
{
  "ok": true,
  "job_id": "b312e7dd-9d11-42ea-905a-7fb7ff97d72d",
  "status": "queued"
}
```

## Poll run status

- Method: `GET`
- Path: `/api/pipeline/run/<job_id>`
- Headers:
  - `Authorization: Bearer <access_token>`

### Status response

- HTTP `200 OK`
- Body:

```json
{
  "ok": true,
  "job_id": "b312e7dd-9d11-42ea-905a-7fb7ff97d72d",
  "status": "running",
  "started_at": "2026-04-14T13:00:00Z",
  "finished_at": null,
  "params": {
    "user_id": "11111111-1111-1111-1111-111111111111",
    "category": "technology",
    "source": null,
    "max_articles": 15,
    "timeout": 30.0,
    "content_max_chars": 12000
  },
  "result": null,
  "error": null
}
```

`status` values:
- `queued`
- `running`
- `succeeded`
- `failed`

On `succeeded`, `result` is a **JSON array** of every article URL the run processed (including cache short-circuits, fetch failures, and LLM decisions). Each element is an object with:

| Field | Type | Notes |
|--------|------|--------|
| `date` | string or null | |
| `full_summary` | string or null | Omitted from raw article body; not the full HTML content |
| `short_summary` | string or null | |
| `source` | string | Source hostname/label |
| `title` | string or null | May be null for cache-only rows where only the URL is known |
| `url` | string | |
| `included` | boolean | `true` if the article was included for this run |
| `reason` | string | Why the article was included, excluded, or skipped |

There is **no** `content` field (raw article body is never included).

Example fragment for a succeeded job:

```json
"result": [
  {
    "date": "2026-04-14",
    "full_summary": "...",
    "short_summary": "...",
    "source": "example.com",
    "title": "Headline",
    "url": "https://example.com/a",
    "included": true,
    "reason": "Matches the category instruction focus."
  },
  {
    "date": null,
    "full_summary": null,
    "short_summary": null,
    "source": "example.com",
    "title": null,
    "url": "https://example.com/b",
    "included": false,
    "reason": "Already excluded"
  }
]
```

On `failed`, `error` contains a failure message and `result` is typically null.

## Errors

- `400` invalid JSON or field types.
- `401` missing/invalid token.
- `403` user mismatch (`user_id` does not match JWT `sub`) or accessing another user’s job.
- `404` unknown job id.

## Operational limitation

Job state is stored in process memory only:
- Not durable across process restarts.
- Not shared across multiple API instances.
