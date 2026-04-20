# CLI overview (for humans)

Installed commands come from **`pyproject.toml`** (`[project.scripts]`). Run from a venv where the package is installed (`pip install -e ".[dev]"`).

| Command | What it does |
|---------|----------------|
| **`news-manager ingest`** | Runs the **Supabase-backed ingest pipeline** (load sources from the DB, fetch articles, Groq filter/summarize, write **`news_articles`** / exclusions). With **`use_rss: false`**, each source URL is **auto-classified** (RSS/Atom, XML sitemap, or HTML listing) from one GET. Optional **`--html-discovery-llm`** applies when the listing is HTML (see **[for_agents.md](for_agents.md)**). |
| **`news-manager user-sources export`** | Prints one user’s **categories + sources** as JSON to **stdout** (looks up user by **email** via Auth admin API; needs **service role**).Example `news-manager user-sources export --email tammer@tammer.com`|
| **`news-manager user-sources import`** | Reads a **catalog JSON** from a **file** or **stdin** and merges it into **`categories`** / **`sources`** for the user resolved from **email**. |
| **`fetch-test`** | Fetches **one article URL** once (optional cookie jar) to verify paywall / extraction—prints **OK** or **FAIL** and a short preview. |
| **`resolve-api`** | Starts the **Flask HTTP API** (resolve, pipeline, catalog import, etc.)—not a batch job; keep it running behind a process manager in production. |

**Shorthand:** if you omit the first word **`ingest`**, `news-manager` assumes it—e.g. **`news-manager --category Tech`** is treated as **`news-manager ingest --category Tech`**.

Details, flags, env vars, and exit codes: **[for_agents.md](for_agents.md)**.
