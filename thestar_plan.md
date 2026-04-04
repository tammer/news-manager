# Subscriber cookies for paywalled sites (plan)

## Purpose

Allow **news-manager** to fetch **full article HTML** for sites where the user has a **paid subscription**, by attaching **session cookies** exported from a logged-in browser. The first target is **thestar.com** (TownNews / `tncms-*` cookies), but the design should work for **any hostname** that uses cookie-based entitlement.

This is **legitimate use**: cookies represent **your** session—the same access you already have in the browser—not a paywall bypass for non-subscribers.

---

## Goals

- Load cookies from a **local JSON file** (never committed to git).
- When fetching URLs whose **hostname** matches a configured cookie profile, use an **`httpx` client** that sends those cookies on **every request** for that source (feed discovery, homepage HTML, and individual article pages).
- Keep the default code path unchanged when **no cookie file** exists for a host.
- Provide a **small verification command or script** that fetches a known article URL and reports success (extractable body) vs paywall/teaser.

## Non-goals (v1)

- Automating login or storing passwords (user exports cookies after manual login).
- Playwright / headless browser inside the main pipeline (optional follow-up if cookie-only proves insufficient).
- Per-URL cookie rules; matching is **by host / source** only.
- Sharing or syncing cookie files across machines (user-managed secret).

---

## Cookie file format

Support the **browser extension export** shape already used in the repo: a **JSON array** of objects with at least:

| Field | Use |
|-------|-----|
| `domain` | Cookie domain (e.g. `.thestar.com`, `www.thestar.com`) |
| `name` | Cookie name |
| `value` | Cookie value |
| `path` | Usually `/` |
| `secure` | If true, only send on HTTPS |
| `httpOnly` | Informational; still store and send the cookie from our client |

Ignore unknown fields (`storeId`, `sameSite`, etc.). Skip cookies with empty `name` or `value`.

**Loader behavior:** build an **`httpx.Cookies`** instance (or equivalent) by setting each cookie for its `domain` + `path` + `name`. Respect **`secure`**: for `https://` requests, include; for hypothetical `http://` article URLs, follow httpx rules (prefer HTTPS everywhere).

**Expiry:** If the export includes `expirationDate` (Unix seconds), optionally drop expired cookies at load time to avoid sending dead tokens (implementation choice; document either way).

---

## File location and discovery

**Recommended layout (v1):**

```text
cookies/
  thestar.com.json    # or www.thestar.com.json — see matching below
```

- Directory **`cookies/`** and any stray **`cookies.json`** at the repo root should be **gitignored** (add patterns to [`.gitignore`](.gitignore) when implementing).
- **Do not** commit real cookie files.

**Host matching:**

1. Derive a **registry key** from the **source URL** (feed or homepage), e.g. `source_base_label()` / `urlparse(...).hostname` after normalization, lowercased, strip leading `www.` → e.g. `thestar.com`.
2. Look for **`cookies/<key>.json`**. If missing, optionally try **`cookies/www.<key>.json`** or vice versa—pick **one** documented rule to avoid ambiguity.
3. Optional extension: explicit path in **`sources.json`** per source object, e.g. `"cookies": "cookies/thestar.com.json"`, overriding the convention.

**Environment override (optional):** `NEWS_MANAGER_COOKIES_DIR` defaulting to `cookies` relative to cwd.

---

## Integration points (code)

Today the pipeline opens a fresh **`httpx.Client`** per source in [`news_manager/pipeline.py`](news_manager/pipeline.py) and passes it to [`discover_article_targets`](news_manager/fetch.py) / [`fetch_single_raw_article`](news_manager/fetch.py). [`fetch_articles_for_source`](news_manager/fetch.py) also constructs its own client (used by tests or other entrypoints).

**Required changes:**

1. **`news_manager/cookies_loader.py`** (or under `fetch.py`): `load_cookie_jar_for_host(hostname: str) -> httpx.Cookies | None`.
2. **`build_httpx_client(..., cookies: httpx.Cookies | None)`** helper used by **pipeline** (and **`fetch_articles_for_source`** if kept in sync) so the same jar is used for **all** requests in that source’s `with httpx.Client(...)`.
3. **`Source` model** ([`news_manager/models.py`](news_manager/models.py)): optional `cookies_path: Path | None` or `cookies: str | None` if using explicit `sources.json` override; otherwise derive path from hostname only.
4. **Logging:** log only *that* cookies were loaded for host `X`, never cookie **names/values**.

No change to summarization, cache keys, or Supabase—only HTTP fetch entitlement.

---

## Verification / test harness

Add a **CLI entry** or **`python -m news_manager.tools.fetch_test`** that:

1. Takes **`--url`** (default: a stable The Star article URL used for smoke tests—use **HTTPS**).
2. Loads cookies for that URL’s hostname via the same loader as the pipeline.
3. Performs **one GET** with the same **User-Agent** as [`USER_AGENT`](news_manager/fetch.py).
4. Prints **success** if trafilatura (or existing `_extract_body_title_date` / `fetch_single_raw_article` logic) returns **non-empty article text**; otherwise prints **failure** and a short hint (e.g. refresh cookies, check expiry).

**Automated test:** mock httpx or use a recorded response; do **not** require real cookies in CI.

**Manual acceptance:** with a fresh export, the harness shows success on:

`https://www.thestar.com/.../f75a09f7-3a33-414e-a10b-0ca67483b815`  
(or the current canonical article URL if that asset moves—update the plan/README when changed).

---

## Security and hygiene

- Treat cookie files like **passwords**: **`.gitignore`**, no Slack/git, rotate if leaked.
- **HttpOnly** cookies (e.g. `tncms-auth`) **do not** appear in `document.cookie`; exports from **DevTools “Application → Cookies”** or extensions that save **all** cookies are preferred.
- Sessions **expire**; document that users must **re-export** periodically.

---

## Legal / ToS

User is responsible for complying with **publisher terms** and using only **their** subscription. This feature only automates requests the user could make manually while logged in.

---

## Related files

- [`news_manager/fetch.py`](news_manager/fetch.py) — `fetch_html`, `fetch_single_raw_article`, `discover_article_targets`
- [`news_manager/pipeline.py`](news_manager/pipeline.py) — per-source `httpx.Client`
- [`news_manager/config.py`](news_manager/config.py) — optional `read_sources_json` extension for `cookies` field

---

## Implementation checklist

- [ ] Add `cookies/` to `.gitignore`; document in README.
- [ ] Implement JSON → `httpx.Cookies` loader + host → file path resolution.
- [ ] Extend `Source` / `sources.json` parsing if explicit `cookies` path is desired.
- [ ] Wire cookie jar into pipeline (and `fetch_articles_for_source` for parity).
- [ ] Add `fetch_test` (or similar) CLI and document export steps for The Star.
- [ ] Unit tests for loader (sample redacted JSON fixture).
- [ ] README subsection: “Subscriber cookies (paywalled sites)”.

---

## Revision history

| Version | Notes |
|---------|--------|
| 1.0 | Expanded from scratch notes into implementation spec. |
