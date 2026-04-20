"""Groq-assisted selection of article URLs from homepage link candidates (closed set)."""

from __future__ import annotations

import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

from news_manager.config import groq_model_html_discovery, html_discovery_max_candidates
from news_manager.llm import get_client
from news_manager.summarize import _parse_json_response

logger = logging.getLogger("news_manager.html_discovery")

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _strip_json_fence(content: str) -> str:
    content = content.strip()
    m = _JSON_FENCE.search(content)
    if m:
        return m.group(1).strip()
    return content


def _parse_llm_json(content: str) -> dict[str, Any] | None:
    return _parse_json_response(content) or _parse_json_response(_strip_json_fence(content))


def select_article_urls_with_llm(
    home_url: str,
    candidates: list[tuple[str, str]],
    *,
    home_host: str | None = None,
) -> list[str] | None:
    """
    Ask the model to return ``article_urls`` drawn only from the candidate list.

    Returns ``None`` on transport/parse errors or unusable output (caller should
    fall back to heuristics). Returns a possibly empty list only when JSON parsed
    and ``article_urls`` was a list (empty list means caller falls back).
    """
    host = home_host or (urlparse(home_url).hostname or home_url)
    model = groq_model_html_discovery()
    max_k = html_discovery_max_candidates()
    capped = candidates[:max_k]
    if len(candidates) > max_k:
        logger.info(
            "html_discovery_llm: capped candidates for model host=%s total=%s sent=%s",
            host,
            len(candidates),
            max_k,
        )

    allowed: set[str] = {u for u, _ in capped}
    lines = [f"{u}\t{t.replace(chr(9), ' ')}" for u, t in capped]
    user = (
        f"Homepage URL: {home_url}\n"
        f"Same registrable host as the homepage. Candidate links ({len(lines)} lines, "
        f"tab-separated URL then anchor text). Pick story/article pages; omit section hubs, "
        f"tags, authors, video-only, subscribe, search, and chrome.\n\n"
        + "\n".join(lines)
    )

    system = (
        "You pick which URLs from the provided list are likely individual news or blog "
        "articles on this site. Respond with a single JSON object only, no markdown fences, "
        'using exactly this shape: {"article_urls":["<url>",...]}. Every string in '
        '"article_urls" MUST be copied exactly from the candidate list (same characters). '
        "Order URLs roughly as they would appear in a top stories list on the homepage "
        "(most prominent first). If none qualify, use an empty array."
    )

    t0 = time.perf_counter()
    try:
        client = get_client()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
        )
    except Exception:
        logger.exception(
            "html_discovery_llm: Groq request failed host=%s model=%s",
            host,
            model,
        )
        return None

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    usage = getattr(resp, "usage", None)
    if usage is not None:
        logger.info(
            "html_discovery_llm: Groq usage host=%s model=%s prompt_tokens=%s "
            "completion_tokens=%s total_tokens=%s elapsed_ms=%.0f",
            host,
            model,
            getattr(usage, "prompt_tokens", None),
            getattr(usage, "completion_tokens", None),
            getattr(usage, "total_tokens", None),
            elapsed_ms,
        )
    else:
        logger.info(
            "html_discovery_llm: Groq response host=%s model=%s elapsed_ms=%.0f (no usage)",
            host,
            model,
            elapsed_ms,
        )

    raw = resp.choices[0].message.content if resp.choices else None
    if not raw:
        logger.warning("html_discovery_llm: empty message content host=%s", host)
        return None

    data = _parse_llm_json(raw)
    if not isinstance(data, dict):
        logger.warning(
            "html_discovery_llm: JSON parse failed host=%s preview=%r",
            host,
            raw[:500],
        )
        return None

    raw_urls = data.get("article_urls")
    if raw_urls is None:
        logger.warning(
            "html_discovery_llm: missing article_urls key host=%s preview=%r",
            host,
            raw[:500],
        )
        return None
    if not isinstance(raw_urls, list):
        logger.warning(
            "html_discovery_llm: article_urls not a list host=%s type=%s",
            host,
            type(raw_urls).__name__,
        )
        return None

    out: list[str] = []
    rejected = 0
    seen_out: set[str] = set()
    for item in raw_urls:
        if not isinstance(item, str):
            rejected += 1
            continue
        u = item.strip()
        if not u:
            continue
        if u not in allowed:
            rejected += 1
            logger.debug("html_discovery_llm: dropped URL not in candidate set: %s", u[:200])
            continue
        if u in seen_out:
            continue
        seen_out.add(u)
        out.append(u)

    if rejected:
        logger.info(
            "html_discovery_llm: validated host=%s kept=%s rejected_not_in_candidates=%s",
            host,
            len(out),
            rejected,
        )

    return out
