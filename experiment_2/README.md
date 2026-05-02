# Experiment 2: Chat-based “add source” (CLI POC)

Design notes for a **RAM-only** CLI flow: validate intent → discover (homepages) → user picks a candidate → resolve category → confirm instructions where needed → **final confirm** → transactional DB write.

## Behaviour (agreed)

- **Help**: Handle turns like “what can you do?” without running discovery.
- **Vague adds**: e.g. “do something cool” → ask for specifics before discovery.
- **Discovery target**: **Site homepages** only (`sources.url` is a homepage).
- **`use_rss`**: The system sets this automatically. **Prefer RSS** when a stable feed is available; otherwise fall back as the implementation defines. **Do not mention RSS** in user-facing copy, including the **final confirm** before write.
- **Instructions**: Live on **`categories`**. Adding to an **existing** category → keep existing instructions unless the flow explicitly revisits them. **New** category → suggest name (and instruction as needed); user can overrule.
- **Duplicates**: Discovery layer marks dups; **do not offer** dup candidates to the user.
- **Persistence**: Use **transactions** for multi-step writes; show a **final confirm** (homepage URL, category name, new vs existing category; for new category include instruction text as agreed—**not** `use_rss`) before committing.
- **Session**: **In-memory only**; no resume across process restarts.

This experiment is a POC; it does not need to match production UI yet.

## Run

From the repo root (with the package installed / `PYTHONPATH` including the repo):

```bash
python experiment_2/add_source_chat.py '<supabase_password>'
```

Or skip password sign-in with **`NEWS_MANAGER_ACCESS_TOKEN`** (Supabase access JWT); then no CLI password is required.

**Requires**

- **`resolve-api`** running (default `http://127.0.0.1:8080`, override with **`RESOLVE_API_URL`**).
- **`GROQ_API_KEY`** (intent + new-category suggestions use Groq).
- **`SUPABASE_URL`** (REST + password grant). The script embeds the project **anon / publishable** key for this POC.
- Auth: password grant uses a **hardcoded POC email** (`tammer@tammer.com`) plus the password from **argv[1]**, unless **`NEWS_MANAGER_ACCESS_TOKEN`** is set.

**Flow**

The script calls **`POST /api/sources/discover`** (poll until done), **`POST /api/sources/resolve`** on the chosen suggestion to pick homepage + `use_rss` (not shown to the user), then **`POST /api/user/sources/import`** after a final `yes` confirm. Writes are whatever the server implements for import (single request; not a client-side Postgres transaction).

Type **`help`**, **`quit`**, or describe what to add at the **`>`** prompt.
