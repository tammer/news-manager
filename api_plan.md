# HTTP API — trigger news-manager runs (plan)

## Purpose

Expose a **small Flask HTTP service** with one job: **kick off** the same work the CLI does—fetch sources, summarize with Groq, write `output.json`, and **upsert to Supabase**—then **return right away**. The client does **not** wait for completion, poll status, or receive a `job_id`. When the run finishes (success or failure), the server **logs** the outcome; there is **no HTTP API** for progress or results.

This document is the **product / behavior spec** for that API. Implementation should **reuse** the same Python entrypoints the CLI uses ([`news_manager/cli.py`](news_manager/cli.py), pipeline, `write_output`, [`news_manager/supabase_sync.py`](news_manager/supabase_sync.py)), not spawn a subprocess, unless you have a strong reason otherwise.

---

## Equivalence to the CLI

The background run should match:

```bash
news-manager \
  --sources sources.json \
  --instructions instructions.md \
  --output output.json \
  --write-supabase
```

Notes:

- The installed command is **`news-manager`** (see [`pyproject.toml`](pyproject.toml)); equivalent via `python -m news_manager` with the same arguments.
- **`--write-supabase`** requires `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, and the optional **`supabase`** extra ([`database_plan.md`](database_plan.md)).
- **`GROQ_API_KEY`** (and optional `GROQ_MODEL`) are required for the pipeline, same as the CLI.

Paths (`sources.json`, `instructions.md`, `output.json`) can be **fixed in config/env** for v1.

---

## Why Flask

- **Flask** is enough for two routes, minimal boilerplate, and running behind gunicorn/waitress in production.
- Alternatives (FastAPI, Starlette) are out of scope unless you explicitly change this plan.

---

## Runtime behavior (fire-and-forget)

A full run can take **many minutes**. The API **must not** block the HTTP response on that work.

1. **`POST /run`** checks auth (and optional lightweight validation).
2. It **starts the pipeline in a background thread or worker** (implementation choice: `threading`, `concurrent.futures`, etc.) and **returns immediately**.
3. Success or failure of the run is handled **only in server logs** (and side effects: `output.json`, Supabase). The client is not notified over HTTP.

**Response:** **`202 Accepted`** with a minimal body, e.g. `{}` or `{ "accepted": true }`. No `job_id`, no polling contract.

**Concurrency:** Document whether only **one run at a time** is allowed (recommended for v1—avoids overlapping Groq load and clobbering `output.json`) or overlapping runs are permitted.

---

## Security

- **`POST /run`** must require authentication (**Bearer** or **`X-Api-Key`** vs an env var). Never commit the token.
- **`GET /health`** may stay unauthenticated for load balancers.
- Do not log secrets.

---

## Suggested HTTP surface (v1)

| Method | Path | Purpose |
|--------|------|--------|
| `GET` | `/health` | Liveness: 200 if the process is up (no Groq call). |
| `POST` | `/run` | Start the full pipeline + Supabase sync in the background; **202** immediately if accepted. |

**`POST /run`:**

- **`202 Accepted`** — Run was scheduled (not “finished”).
- **`401` / `403`** — Bad or missing auth.
- **`409 Conflict`** (optional) — A run is already in progress and single-flight is enforced.

Pipeline errors after **202** are **out of band** (logs only).

---

## Configuration (environment / app config)

Same as the CLI where applicable:

- `GROQ_API_KEY`, optional `GROQ_MODEL`
- Supabase: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`
- Paths and flags: `--sources`, `--instructions`, `--output`, `--cache`, `--no-cache`, `--max-articles`, etc., via env or config file

---

## Non-goals (v1)

- **Synchronous** `POST /run` waiting until the pipeline completes.
- **Job IDs, status endpoints, polling, or webhooks** for run outcome.
- **Multi-tenant** configs unless added later.
- Replacing the CLI.

---

## Related docs

- [database_plan.md](database_plan.md) — Supabase table and upsert semantics.
- [README.md](README.md) — CLI flags and setup.

---

## Checklist (implementation)

- [ ] Flask app + production WSGI notes (e.g. gunicorn).
- [ ] Auth on **`POST /run`** only.
- [ ] Background execution: return **202** before pipeline starts or as soon as the worker is scheduled; **log** completion and errors.
- [ ] Reuse pipeline + `write_output` + `sync_category_results_to_supabase` inside the worker.
- [ ] Document env vars, single-flight vs overlapping runs, and **fire-and-forget** semantics in README.
- [ ] Optional dependency group if Flask is not part of the default install.
