"""Discover source homepages from a plain-English intent."""

from __future__ import annotations

import logging
import json
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
_MAX_LINK_LINES = 200
_MAX_ANCHOR_TEXT = 240


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


def _extract_meta_tags(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    out: list[dict[str, str]] = []
    for node in soup.find_all("meta"):
        entry: dict[str, str] = {}
        for key in ("name", "property", "http-equiv", "content"):
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                entry[key] = value.strip()
        if entry:
            out.append(entry)
    return out


def _extract_body_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    body = soup.body if soup.body is not None else soup
    for tag in body.find_all(["script", "style", "noscript"]):
        tag.decompose()
    text = " ".join(body.get_text(separator=" ", strip=True).split())
    return text[:_MAX_BODY_TEXT_FOR_LLM]


def _extract_article_link_lines(html: str, page_url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    body = soup.body if soup.body is not None else soup
    seen_href: set[str] = set()
    lines: list[str] = []
    for anchor in body.find_all("a", href=True):
        if len(lines) >= _MAX_LINK_LINES:
            break
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        raw = href.strip()
        if not raw or raw.startswith("#"):
            continue
        if raw.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        absolute = _scrub_url(urljoin(page_url, raw))
        if not url_fetch_allowed(absolute):
            continue
        key = absolute.lower()
        if key in seen_href:
            continue
        seen_href.add(key)
        label = anchor.get_text(separator=" ", strip=True)
        if len(label) > _MAX_ANCHOR_TEXT:
            label = label[: _MAX_ANCHOR_TEXT - 3] + "..."
        lines.append(f"{absolute}\t{label.replace(chr(9), ' ')}")
    return lines


def _article_recommendations(url: str, html: str) -> list[str]:
    payload = {
        "page_url": url,
        "body_text": _extract_body_text(html),
        "outbound_links_tab_separated": _extract_article_link_lines(html, url),
    }
    system_prompt = (
        "You analyze a single article page and list recommended blogs or news sites. "
        "Return JSON only in this exact shape: "
        '{"recommended":[{"name":"short label","url":"https://... or null"}],"reasoning":"brief explanation"}. '
        "Use only absolute URLs from the provided outbound links when available. "
        "recommended may be empty."
    )
    data = _chat_json(system_prompt, json.dumps(payload, ensure_ascii=False))
    if not isinstance(data, dict):
        return []
    recommended = data.get("recommended")
    if not isinstance(recommended, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in recommended:
        if not isinstance(item, dict):
            continue
        url_val = item.get("url")
        if not isinstance(url_val, str):
            continue
        candidate = _scrub_url(url_val.strip())
        if not candidate or not url_fetch_allowed(candidate):
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def _classify_page_meta(url: str, intent: str) -> _ClassifiedPage | None:
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
    meta_tags = _extract_meta_tags(html)
    user_prompt = build_discovery_classification_user_prompt(
        intent=intent,
        url=final_scrubbed,
        page_title=page_title,
        meta_tags=meta_tags,
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

    suggestions: list[dict[str, str]] = []
    seen_suggestion_urls: set[str] = set()
    seen_suggestion_domains: set[str] = set()
    seen_classification_urls: set[str] = set()
    classified_count = 0

    def _try_add_suggestion(classified: _ClassifiedPage) -> None:
        final_url_key = classified.url.lower()
        if final_url_key in excluded_urls:
            return
        if classified.base_domain and classified.base_domain in excluded_hosts:
            return
        if final_url_key in seen_suggestion_urls:
            return
        if classified.base_domain and classified.base_domain in seen_suggestion_domains:
            return
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

    seed_seen: set[str] = set()
    for row in rows:
        if len(suggestions) >= capped_max_results:
            break
        href = _scrub_url((row.get("href") or "").strip())
        if not href or not url_fetch_allowed(href):
            continue
        seed_key = href.lower()
        if seed_key in seed_seen:
            continue
        seed_seen.add(seed_key)
        if seed_key in seen_classification_urls:
            continue
        seen_classification_urls.add(seed_key)

        classified = _classify_page_meta(href, intent)
        classified_count += 1
        if classified is None:
            continue
        if classified.classification in {"blog home", "news home"}:
            _try_add_suggestion(classified)
            continue
        if classified.classification == "other":
            continue
        if classified.classification != "article":
            continue
        for rec_url in _article_recommendations(classified.url, classified.content):
            if len(suggestions) >= capped_max_results:
                break
            rec_key = rec_url.lower()
            if rec_key in seen_classification_urls:
                continue
            seen_classification_urls.add(rec_key)
            rec_classified = _classify_page_meta(rec_url, intent)
            classified_count += 1
            if rec_classified is None:
                continue
            if rec_classified.classification in {"blog home", "news home"}:
                _try_add_suggestion(rec_classified)

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
