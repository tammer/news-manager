"""Discover source homepages from a plain-English intent."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from news_manager.discovery_prompts import (
    DISCOVERY_CLASSIFICATION_ALLOWED,
    DISCOVERY_CLASSIFICATION_PROMPT,
    build_discovery_classification_user_prompt,
)
from news_manager.source_resolve import (
    _chat_json,
    _scrub_url,
    ddg_text_search,
    fetch_html_limited,
    url_fetch_allowed,
)

logger = logging.getLogger(__name__)

_MAX_SEARCH_RESULTS = 30
_MAX_BODY_TEXT_FOR_LLM = 12000
_MAX_VISITED_PAGES = 60
_MAX_CHILD_LINKS = 12


@dataclass(frozen=True)
class _TraversalCandidate:
    url: str
    depth: int


@dataclass(frozen=True)
class _ClassifiedPage:
    title: str
    url: str
    base_domain: str
    content: str
    classification: str
    reason: str


def _domain_name(url: str) -> str:
    host = (urlparse(url).hostname or "").strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _build_seed_query(intent: str) -> str:
    return f"blogs or news sites about {intent.strip()}"


def _extract_title(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    title = " ".join(((soup.title.string or "").split())) if soup.title and soup.title.string else ""
    return title


def _extract_body_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    body = soup.body if soup.body is not None else soup
    text = " ".join(body.get_text(separator=" ", strip=True).split())
    return text[:_MAX_BODY_TEXT_FOR_LLM]


def _extract_child_urls(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    out: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        raw_href = href.strip()
        if not raw_href or raw_href.startswith("#"):
            continue
        if raw_href.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        candidate_url = _scrub_url(urljoin(base_url, raw_href))
        if not url_fetch_allowed(candidate_url):
            continue
        key = candidate_url.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate_url)
        if len(out) >= _MAX_CHILD_LINKS:
            break
    return out


def _classify_url(url: str, intent: str) -> _ClassifiedPage | None:
    try:
        html, final_url, _err = fetch_html_limited(url)
    except Exception:
        logger.debug("classification fetch failed for %s", url, exc_info=True)
        return None
    if not html or not final_url:
        return None
    final_scrubbed = _scrub_url(final_url)
    if not url_fetch_allowed(final_scrubbed):
        return None
    page_title = _extract_title(html)
    body_text = _extract_body_text(html)
    user_prompt = build_discovery_classification_user_prompt(
        intent=intent,
        url=final_scrubbed,
        page_title=page_title,
        body_text=body_text,
    )
    data = _chat_json(DISCOVERY_CLASSIFICATION_PROMPT, user_prompt)
    if not isinstance(data, dict):
        return None
    classification = str(data.get("classification") or "").strip().lower()
    reason = str(data.get("reason") or "").strip()
    if classification not in DISCOVERY_CLASSIFICATION_ALLOWED:
        return None
    return _ClassifiedPage(
        title=page_title,
        url=final_scrubbed,
        base_domain=_domain_name(final_scrubbed),
        content=html,
        classification=classification,
        reason=reason or "No reason provided.",
    )


def discover_sources(
    query: str,
    *,
    locale: str | None = None,
    max_results: int = 5,
    excluded_source_urls: set[str] | None = None,
) -> dict[str, Any]:
    intent = query.strip()
    if not intent:
        raise ValueError("'query' must be a non-empty string.")
    if max_results < 1:
        raise ValueError("'max_results' must be at least 1.")

    capped_max_results = min(max_results, 5)
    excluded_source_urls = excluded_source_urls or set()
    excluded_urls = {_scrub_url(u).lower() for u in excluded_source_urls if isinstance(u, str) and u.strip()}
    excluded_hosts = {_domain_name(u) for u in excluded_source_urls if isinstance(u, str) and u.strip()}
    excluded_hosts.discard("")

    search_query = _build_seed_query(intent)
    rows = ddg_text_search(search_query, max_results=_MAX_SEARCH_RESULTS, region=locale)

    stack: list[_TraversalCandidate] = []
    seed_seen: set[str] = set()
    for row in reversed(rows):
        href = _scrub_url((row.get("href") or "").strip())
        if not href or not url_fetch_allowed(href):
            continue
        key = href.lower()
        if key in seed_seen:
            continue
        seed_seen.add(key)
        stack.append(_TraversalCandidate(url=href, depth=0))

    suggestions: list[dict[str, str]] = []
    seen_suggestion_urls: set[str] = set()
    seen_suggestion_domains: set[str] = set()
    visited_urls: set[str] = set()
    classified_count = 0

    while stack and len(suggestions) < capped_max_results and classified_count < _MAX_VISITED_PAGES:
        node = stack.pop()
        url_key = node.url.lower()
        if url_key in visited_urls:
            continue
        visited_urls.add(url_key)

        classified = _classify_url(node.url, intent)
        classified_count += 1
        if classified is None:
            continue

        final_url_key = classified.url.lower()
        if final_url_key in visited_urls:
            visited_urls.add(final_url_key)

        if classified.classification == "follow" and node.depth == 0:
            child_urls = _extract_child_urls(classified.content, classified.url)
            for child in reversed(child_urls):
                child_key = child.lower()
                if child_key in visited_urls:
                    continue
                stack.append(_TraversalCandidate(url=child, depth=1))
            continue

        if classified.classification != "is_index":
            continue

        if final_url_key in excluded_urls:
            continue
        if classified.base_domain and classified.base_domain in excluded_hosts:
            continue
        if final_url_key in seen_suggestion_urls:
            continue
        if classified.base_domain and classified.base_domain in seen_suggestion_domains:
            continue
        suggestions.append(
            {
                "title": classified.title or classified.base_domain or "Unknown source",
                "url": classified.url,
                "base_domain": classified.base_domain,
                "classification": classified.classification,
                "reason": classified.reason,
            }
        )
        seen_suggestion_urls.add(final_url_key)
        if classified.base_domain:
            seen_suggestion_domains.add(classified.base_domain)

    return {
        "ok": True,
        "suggestions": suggestions,
        "meta": {
            "query": intent,
            "search_query": search_query,
            "candidates_considered": classified_count,
            "max_results": capped_max_results,
            "excluded_existing": len(excluded_urls),
        },
    }
