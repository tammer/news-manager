"""Discover candidate sources from a plain-English query."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from news_manager.source_resolve import (
    _chat_json,
    _collect_candidates_from_query,
    _scrub_url,
    fetch_html_limited,
    url_fetch_allowed,
)

logger = logging.getLogger(__name__)

_MAX_SEARCH_RESULTS = 25
_MAX_CANDIDATES_FOR_LLM = 12
_MAX_FETCHED_PAGES = 5
_MAX_FETCH_WORKERS = 3
_MAX_HTML_SNIPPET = 1200


def _domain_name(url: str) -> str:
    host = (urlparse(url).hostname or "").strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _extract_page_hint(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    title = " ".join(((soup.title.string or "").split())) if soup.title and soup.title.string else ""
    desc = ""
    m = soup.find("meta", attrs={"name": "description"})
    if m and isinstance(m.get("content"), str):
        desc = " ".join(str(m["content"]).split())
    joined = " | ".join(x for x in (title, desc) if x)
    return joined[:_MAX_HTML_SNIPPET]


def _dedupe_candidates(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        href = _scrub_url((row.get("href") or "").strip())
        if not href or not url_fetch_allowed(href):
            continue
        key = href.lower()
        host = _domain_name(href)
        if key in seen or (host and host in seen):
            continue
        seen.add(key)
        if host:
            seen.add(host)
        out.append(
            {
                "title": (row.get("title") or "").strip(),
                "href": href,
                "body": (row.get("body") or "").strip(),
            }
        )
    return out


def _llm_rank_suggestions(query: str, candidates: list[dict[str, str]], max_results: int) -> list[dict[str, str]]:
    lines: list[str] = []
    for i, c in enumerate(candidates[:_MAX_CANDIDATES_FOR_LLM]):
        lines.append(
            (
                f"{i + 1}. url={c.get('href')!r} "
                f"title={c.get('title', '')!r} "
                f"snippet={c.get('body', '')!r} "
                f"page_hint={c.get('page_hint', '')!r}"
            )
        )
    if not lines:
        return []

    system = (
        "You recommend reputable sources that match a user's desired feed interests. "
        "Return JSON only with this exact shape: "
        '{"suggestions":[{"name":"string","url":"https://...","why":"short reason"}]}. '
        "Pick unique domains only. Use only URLs from candidates. "
        "Keep 'why' concise and concrete."
    )
    user = (
        f"User intent: {query!r}\n"
        f"Return at most {max_results} suggestions.\n\n"
        "Candidates:\n" + "\n".join(lines)
    )
    data = _chat_json(system, user)
    if not isinstance(data, dict):
        return []
    raw = data.get("suggestions")
    if not isinstance(raw, list):
        return []

    allowed_urls = {c["href"] for c in candidates if c.get("href")}
    out: list[dict[str, str]] = []
    seen_hosts: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        url = _scrub_url(str(item.get("url") or "").strip())
        why = str(item.get("why") or "").strip()
        if not url or url not in allowed_urls or not url_fetch_allowed(url):
            continue
        host = _domain_name(url)
        if host and host in seen_hosts:
            continue
        if host:
            seen_hosts.add(host)
        out.append(
            {
                "name": name or host or "Unknown source",
                "url": url,
                "why": why or "Matches your requested topic and appears to publish relevant articles.",
            }
        )
        if len(out) >= max_results:
            break
    return out


def _fetch_candidate_hint(url: str) -> tuple[str | None, str | None]:
    try:
        html, final_url, _err = fetch_html_limited(url)
    except Exception:
        logger.debug("candidate fetch failed for %s", url, exc_info=True)
        return None, None
    if not html or not final_url:
        return None, None
    return _scrub_url(final_url), _extract_page_hint(html)


def discover_sources(
    query: str,
    *,
    locale: str | None = None,
    max_results: int = 5,
) -> dict[str, Any]:
    q = query.strip()
    if not q:
        raise ValueError("'query' must be a non-empty string.")
    if max_results < 1:
        raise ValueError("'max_results' must be at least 1.")

    search_cap = max(5, min(_MAX_SEARCH_RESULTS, max_results * 4))
    rows = _collect_candidates_from_query(q, max_results=search_cap, region=locale)
    candidates = _dedupe_candidates(rows)
    if not candidates:
        return {"ok": True, "suggestions": [], "meta": {"candidates_considered": 0}}

    candidates_to_fetch = candidates[:_MAX_FETCHED_PAGES]
    worker_count = min(_MAX_FETCH_WORKERS, len(candidates_to_fetch))
    if worker_count > 0:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            fetched = executor.map(_fetch_candidate_hint, (c["href"] for c in candidates_to_fetch))
            for candidate, (final_url, page_hint) in zip(candidates_to_fetch, fetched):
                if not final_url or not page_hint:
                    continue
                candidate["href"] = final_url
                candidate["page_hint"] = page_hint

    suggestions = _llm_rank_suggestions(q, candidates, max_results=max_results)
    if not suggestions:
        suggestions = []
        for c in candidates[:max_results]:
            host = _domain_name(c["href"])
            suggestions.append(
                {
                    "name": c["title"] or host or "Unknown source",
                    "url": c["href"],
                    "why": "Appears relevant to your query based on search context.",
                }
            )

    return {
        "ok": True,
        "suggestions": suggestions,
        "meta": {
            "query": q,
            "candidates_considered": len(candidates),
            "max_results": max_results,
        },
    }
