"""Experiment 2: RAM-only CLI to add a source via chat-style prompts.

Uses the running resolve-api (discovery + resolve + catalog import) and Supabase
REST with the user's access token. See experiment_2/README.md for behaviour.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any

import httpx

from news_manager.config import groq_model, load_dotenv_if_present
from news_manager.llm import get_client


# POC: fixed account for password grant (override sign-in with NEWS_MANAGER_ACCESS_TOKEN).
DEFAULT_SUPABASE_EMAIL = "tammer@tammer.com"
# POC: Supabase publishable (anon) key for Auth + PostgREST `apikey` header.
HARDCODED_SUPABASE_ANON_KEY = "sb_publishable_DSZ2FtoAtzUbMitch1yaMA_L_P1CsPK"


# ---------------------------------------------------------------------------
# HTTP / auth
# ---------------------------------------------------------------------------


def _resolve_api_base() -> str:
    return os.environ.get("RESOLVE_API_URL", "http://127.0.0.1:8080").rstrip("/")


def _supabase_url() -> str:
    u = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    if not u:
        raise ValueError("SUPABASE_URL must be set.")
    return u


def _supabase_anon_key() -> str:
    return HARDCODED_SUPABASE_ANON_KEY


def _http_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    r = client.request(method, url, headers=headers, json=json_body, timeout=120.0)
    try:
        data = r.json() if r.content else {}
    except json.JSONDecodeError:
        data = {"_raw": r.text[:2000]}
    return r.status_code, data


def get_access_token(client: httpx.Client, *, password: str | None) -> str:
    token = os.environ.get("NEWS_MANAGER_ACCESS_TOKEN", "").strip()
    if token:
        return token
    pw = (password or "").strip()
    if not pw:
        raise ValueError(
            "Usage: python experiment_2/add_source_chat.py <supabase_password>\n"
            "Or set NEWS_MANAGER_ACCESS_TOKEN to skip password auth."
        )

    url = f"{_supabase_url()}/auth/v1/token?grant_type=password"
    headers = {
        "apikey": _supabase_anon_key(),
        "Content-Type": "application/json",
    }
    status, body = _http_json(
        client,
        "POST",
        url,
        headers=headers,
        json_body={"email": DEFAULT_SUPABASE_EMAIL, "password": pw},
    )
    if status != 200:
        raise RuntimeError(f"Sign-in failed HTTP {status}: {body}")
    if not isinstance(body, dict):
        raise RuntimeError(f"Unexpected sign-in response: {body!r}")
    at = body.get("access_token")
    if not isinstance(at, str) or not at:
        raise RuntimeError(f"Sign-in response missing access_token: {body}")
    return at


def _bearer_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def fetch_categories(client: httpx.Client, access_token: str) -> list[dict[str, Any]]:
    url = f"{_supabase_url()}/rest/v1/categories?select=id,name,instruction&order=name.asc"
    headers = {
        "apikey": _supabase_anon_key(),
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    status, body = _http_json(client, "GET", url, headers=headers)
    if status != 200:
        raise RuntimeError(f"Failed to load categories HTTP {status}: {body}")
    if not isinstance(body, list):
        raise RuntimeError(f"Unexpected categories response: {body!r}")
    return [x for x in body if isinstance(x, dict)]


# ---------------------------------------------------------------------------
# Resolve API: discover, resolve, import
# ---------------------------------------------------------------------------


def start_discover(client: httpx.Client, access_token: str, query: str) -> str:
    base = _resolve_api_base()
    status, body = _http_json(
        client,
        "POST",
        f"{base}/api/sources/discover",
        headers=_bearer_headers(access_token),
        json_body={"query": query},
    )
    if status not in (200, 202):
        raise RuntimeError(f"Discovery start failed HTTP {status}: {body}")
    if not isinstance(body, dict) or not body.get("ok"):
        raise RuntimeError(f"Discovery start rejected: {body}")
    job_id = body.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise RuntimeError(f"Missing job_id: {body}")
    return job_id


def poll_discover(client: httpx.Client, access_token: str, job_id: str) -> dict[str, Any]:
    base = _resolve_api_base()
    url = f"{base}/api/sources/discover/{job_id}"
    headers = _bearer_headers(access_token)
    while True:
        status, body = _http_json(client, "GET", url, headers=headers)
        if status != 200:
            raise RuntimeError(f"Discovery poll HTTP {status}: {body}")
        if not isinstance(body, dict):
            raise RuntimeError(f"Unexpected poll body: {body!r}")
        st = body.get("status")
        if st == "succeeded":
            return body
        if st == "failed":
            raise RuntimeError(f"Discovery job failed: {body.get('error')!r}")
        if st not in {"queued", "running"}:
            raise RuntimeError(f"Unexpected discovery status: {st!r} in {body}")
        time.sleep(2)


def discover_suggestions(client: httpx.Client, access_token: str, query: str) -> list[dict[str, Any]]:
    job_id = start_discover(client, access_token, query)
    final = poll_discover(client, access_token, job_id)
    result = final.get("result")
    if not isinstance(result, dict):
        return []
    sugs = result.get("suggestions")
    if not isinstance(sugs, list):
        return []
    out: list[dict[str, Any]] = []
    for s in sugs:
        if isinstance(s, dict) and isinstance(s.get("url"), str) and s["url"].strip():
            out.append(s)
    return out


def resolve_homepage(
    client: httpx.Client, access_token: str, query: str
) -> tuple[str, bool]:
    """Return (homepage_url, use_rss) for catalog insert."""
    base = _resolve_api_base()
    status, body = _http_json(
        client,
        "POST",
        f"{base}/api/sources/resolve",
        headers=_bearer_headers(access_token),
        json_body={"query": query},
    )
    if status != 200 or not isinstance(body, dict):
        return query.strip(), False
    if not body.get("ok"):
        return query.strip(), False
    hp = body.get("homepage_url")
    url = hp if isinstance(hp, str) and hp.strip() else query.strip()
    ur = body.get("use_rss")
    use_rss = bool(ur) if isinstance(ur, bool) else False
    # Product: prefer RSS when resolver found feeds but kept HTML listing (use_rss false).
    rss_found = body.get("rss_found")
    if rss_found is True and not use_rss:
        use_rss = True
    return url.strip(), use_rss


def import_catalog(
    client: httpx.Client, access_token: str, catalog: dict[str, Any]
) -> dict[str, Any]:
    base = _resolve_api_base()
    status, body = _http_json(
        client,
        "POST",
        f"{base}/api/user/sources/import",
        headers=_bearer_headers(access_token),
        json_body=catalog,
    )
    if status != 200:
        raise RuntimeError(f"Import failed HTTP {status}: {body}")
    if not isinstance(body, dict) or not body.get("ok"):
        raise RuntimeError(f"Import rejected: {body}")
    return body


# ---------------------------------------------------------------------------
# LLM: intent + new category suggestion
# ---------------------------------------------------------------------------


def _strip_code_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9]*\s*", "", s)
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def _chat_json(system: str, user: str) -> dict[str, Any]:
    model = groq_model()
    c = get_client()
    resp = c.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    raw = (resp.choices[0].message.content or "").strip()
    return json.loads(_strip_code_fences(raw))


CLASSIFY_SYSTEM = """You classify a single user message for a CLI whose ONLY job is
helping the user ADD a news/blog SOURCE (a website) to their reading list.

Valid intents:
- "help" — user wants capabilities, greetings without an add, or "what can you do".
- "clarify" — user seems to want to add something but the target is too vague
  (e.g. "something cool", "idk", "blogs") with no topic or site hint. Ask them to name
  a topic, niche, or specific site/person.
- "add_source" — user wants to find and add a publication/site. Set discovery_query
  to a short English phrase for site discovery (theme OR named site/blog).
- "invalid" — not related to adding sources (math homework, jokes with no site,
  generic chit-chat with no add intent).

Output strict JSON only:
{"intent":"help|clarify|add_source|invalid","message":"short user-facing reply","discovery_query":""}

Rules:
- discovery_query must be non-empty ONLY when intent is add_source.
- message should be concise (1-3 sentences). For invalid, explain this tool only adds sources.
"""


def classify_user_line(line: str) -> dict[str, Any]:
    data = _chat_json(CLASSIFY_SYSTEM, f"User message:\n{line}")
    if not isinstance(data, dict):
        return {"intent": "invalid", "message": "Could not interpret that.", "discovery_query": ""}
    intent = str(data.get("intent", "invalid")).lower()
    if intent not in {"help", "clarify", "add_source", "invalid"}:
        intent = "invalid"
    msg = data.get("message")
    dq = data.get("discovery_query")
    return {
        "intent": intent,
        "message": msg if isinstance(msg, str) else "",
        "discovery_query": dq.strip() if isinstance(dq, str) else "",
    }


NEW_CAT_SYSTEM = """You propose a category NAME and INSTRUCTION for a news reader app.
The instruction tells the classifier what articles belong in this category (tone, topics, geography, exclusions).

Output strict JSON only:
{"name":"short category name","instruction":"1-4 sentences, practical for filtering articles"}

Rules:
- name: 2-6 words, Title Case, no quotes.
- instruction: concrete, not marketing fluff.
"""


def suggest_category_block(
    *, discovery_query: str, site_title: str, site_url: str
) -> tuple[str, str]:
    user = (
        f"The user asked to discover sources about:\n{discovery_query}\n\n"
        f"They chose this site:\nTitle: {site_title}\nURL: {site_url}\n\n"
        "Propose name + instruction for a NEW category that fits this site."
    )
    data = _chat_json(NEW_CAT_SYSTEM, user)
    if not isinstance(data, dict):
        return ("New feeds", "")
    name = data.get("name")
    inst = data.get("instruction")
    n = name.strip() if isinstance(name, str) else "New feeds"
    i = inst.strip() if isinstance(inst, str) else ""
    return (n, i)


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------


def print_help() -> None:
    print(
        """
This CLI adds a website source to your account (Supabase).

You can:
  • Describe a topic — e.g. add blogs about indie games
  • Name a site or author — e.g. add Gary Marcus blog

Commands:  help  |  quit / exit

Needs: resolve-api running, GROQ_API_KEY, SUPABASE_URL,
       and your Supabase password as the first CLI argument (or NEWS_MANAGER_ACCESS_TOKEN).
Sign-in email and anon API key are embedded for this POC.
""".strip()
    )


def prompt_line(label: str, default: str | None = None) -> str:
    if default:
        raw = input(f"{label} [{default}]: ").strip()
        return raw or default
    return input(f"{label}: ").strip()


def run_add_flow(
    client: httpx.Client,
    access_token: str,
    discovery_query: str,
    categories: list[dict[str, Any]],
) -> None:
    print(f"\nDiscovering… ({discovery_query!r})\n")
    try:
        suggestions = discover_suggestions(client, access_token, discovery_query)
    except Exception as exc:
        print(f"Discovery failed: {exc}")
        return

    if not suggestions:
        print("No suggestions returned (try a different phrasing).")
        return

    print("Suggestions (from your resolve-api):\n")
    for i, s in enumerate(suggestions, start=1):
        title = str(s.get("title") or s.get("domain") or "?")[:72]
        url = str(s.get("url", ""))
        print(f"  {i}. {title}\n      {url}\n")

    pick_raw = input("Pick a number (or c to cancel): ").strip().lower()
    if pick_raw == "c":
        print("Cancelled.")
        return
    try:
        idx = int(pick_raw)
    except ValueError:
        print("Invalid pick.")
        return
    if idx < 1 or idx > len(suggestions):
        print("Out of range.")
        return

    chosen = suggestions[idx - 1]
    chosen_url = str(chosen.get("url", "")).strip()
    chosen_title = str(chosen.get("title") or chosen.get("domain") or "").strip()

    if categories:
        print("\nExisting categories:\n")
        for i, c in enumerate(categories, start=1):
            nm = str(c.get("name", "?"))
            print(f"  {i}. {nm}")
        new_idx = len(categories) + 1
        print(f"  {new_idx}. Create a NEW category\n")
        cr = input(f"Pick 1–{new_idx} (c cancel): ").strip().lower()
        if cr == "c":
            print("Cancelled.")
            return
        try:
            cix = int(cr)
        except ValueError:
            print("Invalid pick.")
            return
        if cix < 1 or cix > new_idx:
            print("Out of range.")
            return

        if cix == new_idx:
            print("\nProposed new category (LLM)…")
            sug_name, sug_inst = suggest_category_block(
                discovery_query=discovery_query,
                site_title=chosen_title,
                site_url=chosen_url,
            )
            cat_name = prompt_line("Category name", sug_name)
            if not cat_name:
                print("Name required.")
                return
            instruction = prompt_line("Category instruction", sug_inst)
            is_new = True
        else:
            row = categories[cix - 1]
            cat_name = str(row.get("name", "")).strip()
            if not cat_name:
                print("Invalid category row.")
                return
            instruction = str(row.get("instruction") or "").strip()
            is_new = False
            print(f"\nUsing category: {cat_name}\n(existing instructions kept as-is)\n")
    else:
        print("You have no categories yet; we'll create one.\n")
        sug_name, sug_inst = suggest_category_block(
            discovery_query=discovery_query,
            site_title=chosen_title,
            site_url=chosen_url,
        )
        cat_name = prompt_line("Category name", sug_name)
        if not cat_name:
            print("Name required.")
            return
        instruction = prompt_line("Category instruction", sug_inst)
        is_new = True

    print("\nResolving homepage / ingest hints…")
    homepage_url, use_rss = resolve_homepage(client, access_token, chosen_url)

    print("\n--- Confirm save ---")
    print(f"  Homepage: {homepage_url}")
    print(f"  Category: {cat_name}")
    print(f"  {'New' if is_new else 'Existing'} category")
    if is_new:
        print(f"  Instruction:\n    {instruction}\n")
    yn = input("Type yes to save (anything else cancels): ").strip().lower()
    if yn != "yes":
        print("Cancelled.")
        return

    catalog = {
        "schema_version": 1,
        "categories": [
            {
                "category": cat_name,
                "instruction": instruction if is_new else "",
                "sources": [{"url": homepage_url, "use_rss": use_rss}],
            }
        ],
    }
    try:
        out = import_catalog(client, access_token, catalog)
    except Exception as exc:
        print(f"Import failed: {exc}")
        return
    summary = out.get("summary", {})
    print("\nSaved.")
    print(json.dumps(summary, indent=2))


def main() -> int:
    load_dotenv_if_present()
    cli_password = sys.argv[1] if len(sys.argv) > 1 else None
    print_help()
    print()

    try:
        with httpx.Client() as client:
            access_token = get_access_token(client, password=cli_password)
            try:
                categories = fetch_categories(client, access_token)
            except Exception as exc:
                print(f"Could not load categories: {exc}", file=sys.stderr)
                return 1

            while True:
                try:
                    line = input("\n> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nBye.")
                    break
                if not line:
                    continue
                low = line.lower()
                if low in {"quit", "exit", "q"}:
                    print("Bye.")
                    break
                if low == "help":
                    print_help()
                    continue

                cls = classify_user_line(line)
                intent = cls["intent"]
                msg = cls.get("message") or ""
                dq = cls.get("discovery_query") or ""

                if intent == "help":
                    print(msg or "Ask me to add a site or topic-based sources.")
                    continue
                if intent == "clarify":
                    print(msg or "What topic or site do you want to add?")
                    continue
                if intent == "invalid":
                    print(msg or "I only help add website sources. Try: add blogs about …")
                    continue
                if intent != "add_source" or not dq:
                    print(msg or "Try describing a site or topic to add.")
                    continue

                run_add_flow(client, access_token, dq, categories)
                try:
                    categories = fetch_categories(client, access_token)
                except Exception:
                    pass

    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
