# Pipeline run API — client integration spec

This document describes the async pipeline endpoints for running `news-manager` over HTTP.

## Purpose

Expose the same run controls available in `news-manager` CLI (`--category`, `--source`, `--max-articles`, `--timeout`, `--content-max-chars`, `--user-id`) through an authenticated API.

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

On `succeeded`, `result` contains the same per-user/per-category output shape as the pipeline internals.
On `failed`, `error` contains a failure message.

## Errors

- `400` invalid JSON or field types.
- `401` missing/invalid token.
- `403` user mismatch (`user_id` does not match JWT `sub`) or accessing another user’s job.
- `404` unknown job id.

## Operational limitation

Job state is stored in process memory only:
- Not durable across process restarts.
- Not shared across multiple API instances.
