"""Discover news/blog sites from plain-English intent (DDG + batched LLM judge)."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

from duckduckgo_search import DDGS

from news_manager.config import groq_model
from news_manager.llm import get_client
from news_manager.source_resolve import _scrub_url, url_fetch_allowed
from news_manager.summarize import _parse_json_response

logger = logging.getLogger(__name__)

# Search: worldwide region, moderate safesearch (not configurable via API).
_DDGS_REGION = "wt-wt"
_DDGS_SAFESEARCH = "moderate"

_MAX_GENERATED_QUERIES = 6
_PER_QUERY_RESULTS = 10
_JUDGE_DOMAINS_PER_BATCH = 45
# Only suggestions at or above this judge score (1–5) are returned.
_MIN_SUGGESTION_SCORE = 4
# Max distinct DDG result URLs passed to the judge per domain (plus site root).
_MAX_CANDIDATE_URLS_PER_DOMAIN = 12

QUERY_GEN_SYSTEM = """You generate DuckDuckGo search queries that surface high-quality
news outlets and blogs (publication homepages, not random articles) for a given theme.

Rules:
- Mix angles: direct topical queries, "best blogs about X", "top news sites about X",
  "independent journalism X", "RSS feed X site:", "newsletter X", and 1-2 queries
  using likely subtopic vocabulary the user did not name explicitly.
- Prefer queries that return publication-level results, not single articles.
- Avoid queries that obviously surface social platforms only (twitter, reddit,
  facebook) unless the theme is specifically about them.
- Keep each query under 12 words.
- Do not number them.

Output strict JSON only, no prose, in this exact shape:
{"queries": ["...", "...", "..."]}
"""

JUDGE_SYSTEM = """You evaluate whether a domain looks like a quality news outlet
or blog about a specified theme, based ONLY on the domain name and a representative
title and snippet from a search engine.

Definition of "quality" for this task:
- Original reporting OR sustained editorial commentary by an identifiable publication.
- Stable publication identity (not a one-off post).
- On-theme: the publication clearly relates to the user's theme.

Drop (verdict="drop") things like:
- Aggregators that just relist links (e.g. generic news aggregators).
- Content farms / SEO listicles with no editorial identity.
- Generic forums (reddit.com, quora.com, stackexchange) unless the theme is the forum itself.
- Marketplaces, job boards, e-commerce.
- Social platforms (twitter.com, x.com, facebook.com, instagram.com, tiktok.com,
  youtube.com) unless theme requires them.
- Wikipedia, dictionaries, generic encyclopedias.
- Single-author Medium/Substack posts where the publication identity is unclear
  (medium.com root is "drop"; specific.substack.com or specific.medium.com may be
  "keep" or "maybe" if the snippet shows publication identity).

Use "keep" for clear, on-theme publications. Use "maybe" when uncertain (e.g. could
be a real publication but the snippet doesn't confirm theme alignment). Use "drop"
otherwise.

Score 1-5 where 5 is "definitely a quality on-theme publication" and 1 is "clearly not".

Kind values:
- "news": news organization
- "blog": blog or independent commentary
- "newsletter": substack-style newsletter
- "aggregator": link aggregator
- "forum": forum/community
- "other": anything else

Each input row includes "candidate_urls": a list of real search-result URLs on that domain
(same host, http/https only). You MUST set "suggested_url" to exactly one string from that
list — copy it verbatim, character for character. Pick the single URL that is the best
starting point for the user's theme (prefer an on-theme section, hub, reviews index, or
newsletter landing page over the bare site root when the list contains a clearly better
match). If only the site root is appropriate, use the root URL from the list.

Output strict JSON only, no prose, in this shape:
{"verdicts": [{"domain": "...", "verdict": "keep|maybe|drop", "score": 1-5,
"kind": "news|blog|newsletter|aggregator|forum|other", "reason": "short reason",
"suggested_url": "must match one entry from candidate_urls exactly"}]}

Return one entry per input domain in this batch. Do not invent domains or URLs.
"""


def _domain_name(url: str) -> str:
    host = (urlparse(url).hostname or "").strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _extract_base_domain_from_href(raw_url: str) -> str:
    candidate = (raw_url or "").strip()
    if not candidate:
        return ""
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    host = (parsed.netloc or "").lower()
    if "@" in host:
        host = host.split("@", 1)[1]
    if ":" in host:
        host = host.split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def _homepage_url(domain: str) -> str:
    d = (domain or "").strip().lower().rstrip(".")
    if not d or "/" in d or " " in d:
        return ""
    return _scrub_url(f"https://{d}/")


def _rollup_candidate_urls(domain: str, urls: list[str]) -> list[str]:
    """Unique scrubbed URLs for ``domain``, order preserved, capped; site root appended last."""
    home = _homepage_url(domain)
    out: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        if len(out) >= _MAX_CANDIDATE_URLS_PER_DOMAIN:
            break
        u = _scrub_url(str(raw).strip())
        if not u or not url_fetch_allowed(u):
            continue
        if _extract_base_domain_from_href(u) != domain:
            continue
        key = u.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
    if home and url_fetch_allowed(home) and home.lower() not in seen:
        out.append(home)
    return out


def _resolve_suggested_url(
    *,
    domain: str,
    candidate_urls: list[str],
    suggested_url: Any,
    fallback_home: str,
) -> str:
    """Return judge ``suggested_url`` if it matches a candidate; else ``fallback_home`` or first candidate."""
    canon: list[str] = []
    seen: set[str] = set()
    for raw in candidate_urls:
        u = _scrub_url(str(raw).strip())
        if not u or not url_fetch_allowed(u):
            continue
        if _extract_base_domain_from_href(u) != domain:
            continue
        key = u.lower()
        if key in seen:
            continue
        seen.add(key)
        canon.append(u)
    home = fallback_home
    if home and url_fetch_allowed(home) and home.lower() not in seen:
        canon.append(home)
    if not canon:
        return home or ""
    if not isinstance(suggested_url, str) or not suggested_url.strip():
        logger.info("discover: missing suggested_url domain=%s using_fallback_home", domain)
        return home or canon[0]
    pick = _scrub_url(suggested_url.strip())
    if not pick or not url_fetch_allowed(pick):
        logger.info("discover: invalid suggested_url domain=%s pick=%r using_fallback_home", domain, pick)
        return home or canon[0]
    pick_key = pick.lower()
    for c in canon:
        if c.lower() == pick_key:
            return c
    stripped = pick.rstrip("/").lower()
    for c in canon:
        if c.rstrip("/").lower() == stripped:
            return c
    logger.info(
        "discover: suggested_url not in candidates domain=%s pick=%r using_fallback_home",
        domain,
        pick,
    )
    return home or canon[0]


def _strip_code_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9]*\s*", "", s)
        if s.endswith("```"):
            s = s[: -3]
    return s.strip()


def _parse_json_obj(text: str) -> Any:
    parsed = _parse_json_response(text)
    if parsed is not None:
        return parsed
    return json.loads(_strip_code_fences(text))


def _llm_chat_json(
    *,
    system: str,
    user: str,
    label: str,
    llm_calls: list[int],
) -> str:
    client = get_client()
    model = groq_model()
    logger.debug("[%s] system (%d chars) user (%d chars)", label, len(system), len(user))
    t0 = time.monotonic()
    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    llm_calls[0] += 1
    dt_ms = (time.monotonic() - t0) * 1000
    content = (completion.choices[0].message.content or "").strip()
    logger.info("[%s] LLM done in %.0f ms model=%s response_chars=%d", label, dt_ms, model, len(content))
    logger.debug("[%s] raw: %s", label, content)
    return content


def _generate_queries(*, intent: str, llm_calls: list[int]) -> list[str]:
    user = f"Theme: {intent}\nReturn between 4 and {_MAX_GENERATED_QUERIES} queries."
    raw = _llm_chat_json(system=QUERY_GEN_SYSTEM, user=user, label="discover.query_gen", llm_calls=llm_calls)
    for attempt in (1, 2):
        try:
            data = _parse_json_obj(raw)
            if not isinstance(data, dict):
                raise ValueError("root not object")
            qs = data.get("queries")
            if not isinstance(qs, list):
                raise ValueError("queries not list")
            out = [str(q).strip() for q in qs if str(q).strip()]
            return out[:_MAX_GENERATED_QUERIES]
        except Exception as exc:
            logger.warning("discover: query_gen parse attempt %s failed: %s", attempt, exc)
            if attempt == 2:
                raise ValueError("LLM query generation returned unparsable JSON.") from exc
            raw = _llm_chat_json(
                system=QUERY_GEN_SYSTEM,
                user=user + "\n\nReturn ONLY valid JSON. Last attempt failed to parse.",
                label="discover.query_gen_retry",
                llm_calls=llm_calls,
            )
    return []


def _ddg_search_worldwide(query: str, *, max_results: int) -> list[dict[str, str]]:
    with DDGS() as ddgs:
        rows = list(
            ddgs.text(
                query,
                max_results=max_results,
                safesearch=_DDGS_SAFESEARCH,
                region=_DDGS_REGION,
            )
        )
    out: list[dict[str, str]] = []
    for r in rows:
        href = (r.get("href") or r.get("url") or "").strip()
        if not href:
            continue
        out.append(
            {
                "title": (r.get("title") or "").strip(),
                "href": href,
                "body": (r.get("body") or "").strip(),
            }
        )
    return out


def _rollup_by_domain(
    hits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_domain: dict[str, dict[str, Any]] = {}
    for h in hits:
        href = _scrub_url(str(h.get("url", "")).strip())
        if not href or not url_fetch_allowed(href):
            continue
        domain = _extract_base_domain_from_href(href)
        if not domain:
            continue
        entry = by_domain.setdefault(
            domain,
            {
                "domain": domain,
                "hit_count": 0,
                "queries": [],
                "title": "",
                "snippet": "",
                "sample_url": href,
                "_hit_urls": [],
            },
        )
        entry["hit_count"] += 1
        urls: list[str] = entry["_hit_urls"]
        if href not in urls:
            urls.append(href)
        q = h.get("query")
        if isinstance(q, str) and q and q not in entry["queries"]:
            entry["queries"].append(q)
        title = str(h.get("title", "") or "")
        snip = str(h.get("snippet", "") or "")
        if len(title) > len(entry["title"]):
            entry["title"] = title
        if len(snip) > len(entry["snippet"]):
            entry["snippet"] = snip
    rolled: list[dict[str, Any]] = []
    for _d, entry in by_domain.items():
        cand = _rollup_candidate_urls(entry["domain"], entry.pop("_hit_urls", []))
        entry["candidate_urls"] = cand
        if cand:
            entry["sample_url"] = cand[0]
        rolled.append(entry)
    rolled.sort(key=lambda e: e["hit_count"], reverse=True)
    return rolled


def _judge_batches(
    *,
    intent: str,
    rolled: list[dict[str, Any]],
    llm_calls: list[int],
) -> list[dict[str, Any]]:
    all_verdicts: list[dict[str, Any]] = []
    batch_size = _JUDGE_DOMAINS_PER_BATCH
    for start in range(0, len(rolled), batch_size):
        chunk = rolled[start : start + batch_size]
        payload = [
            {
                "domain": e["domain"],
                "title": e["title"],
                "snippet": e["snippet"],
                "hit_count": e["hit_count"],
                "sample_url": e.get("sample_url", ""),
                "candidate_urls": e.get("candidate_urls", []),
            }
            for e in chunk
        ]
        user = (
            f"Theme: {intent}\n\nDomains to evaluate ({len(payload)}):\n"
            + json.dumps(payload, indent=2)
        )
        label = f"discover.judge.batch_{start // batch_size + 1}"
        raw = _llm_chat_json(system=JUDGE_SYSTEM, user=user, label=label, llm_calls=llm_calls)
        verdicts: list[dict[str, Any]] = []
        for attempt in (1, 2):
            try:
                data = _parse_json_obj(raw)
                if not isinstance(data, dict):
                    raise ValueError("root not object")
                vs = data.get("verdicts")
                if not isinstance(vs, list):
                    raise ValueError("verdicts not list")
                verdicts = [v for v in vs if isinstance(v, dict) and v.get("domain")]
                break
            except Exception as exc:
                logger.warning("discover: judge parse attempt %s failed: %s", attempt, exc)
                if attempt == 2:
                    logger.error("discover: judge batch failed after retry; skipping batch")
                    verdicts = []
                    break
                raw = _llm_chat_json(
                    system=JUDGE_SYSTEM,
                    user=user + "\n\nReturn ONLY valid JSON. Last attempt failed to parse.",
                    label=label + "_retry",
                    llm_calls=llm_calls,
                )
        by_domain = {e["domain"]: e for e in chunk}
        for v in verdicts:
            d = str(v.get("domain", "")).strip().lower()
            meta = by_domain.get(d, {})
            score_raw = v.get("score")
            try:
                score_i = int(score_raw) if score_raw is not None else 0
            except (TypeError, ValueError):
                score_i = 0
            score_i = max(1, min(5, score_i))
            all_verdicts.append(
                {
                    "domain": d,
                    "verdict": str(v.get("verdict", "")).strip().lower(),
                    "score": score_i,
                    "kind": str(v.get("kind", "") or "").strip().lower() or "other",
                    "reason": str(v.get("reason", "") or "").strip() or "No reason provided.",
                    "title": str(meta.get("title", "") or ""),
                    "snippet": str(meta.get("snippet", "") or ""),
                    "hit_count": int(meta.get("hit_count", 0) or 0),
                    "suggested_url": v.get("suggested_url"),
                    "candidate_urls": list(meta.get("candidate_urls") or []),
                }
            )
    return all_verdicts


_VERDICT_ORDER = {"keep": 0, "maybe": 1, "drop": 2}


def discover_sources(
    query: str,
    *,
    locale: str | None = None,
    excluded_source_urls: set[str] | None = None,
) -> dict[str, Any]:
    """Run multi-query DDG discovery, domain rollup, and batched LLM judging.

    Search uses worldwide DuckDuckGo region (``wt-wt``); ``locale`` is accepted for
    API compatibility but is not passed to the search provider.

    Returns judged suggestions with **score ≥ 4** only. Each ``url`` is the judge's
    ``suggested_url`` chosen from per-domain ``candidate_urls`` (DuckDuckGo hits plus site
    root); if the pick is missing or invalid, the URL falls back to ``https://<domain>/``.
    ``meta`` includes ``min_score`` (4).
    """
    intent = query.strip()
    if not intent:
        raise ValueError("'query' must be a non-empty string.")

    if locale:
        logger.info("discover: locale=%r ignored for DDG (worldwide region)", locale)

    excluded_source_urls = excluded_source_urls or set()
    excluded_urls = {_scrub_url(u).lower() for u in excluded_source_urls if isinstance(u, str) and u.strip()}
    excluded_hosts = {_domain_name(u) for u in excluded_source_urls if isinstance(u, str) and u.strip()}
    excluded_hosts.discard("")

    llm_calls: list[int] = [0]

    logger.info("discover: start intent=%r excluded_urls=%s excluded_hosts=%s", intent, len(excluded_urls), len(excluded_hosts))

    queries = _generate_queries(intent=intent, llm_calls=llm_calls)
    logger.info("discover: generated %d search queries", len(queries))

    raw_hits: list[dict[str, Any]] = []
    for idx, q in enumerate(queries, start=1):
        logger.info("discover: ddg (%d/%d) %r", idx, len(queries), q)
        t0 = time.monotonic()
        try:
            rows = _ddg_search_worldwide(q, max_results=_PER_QUERY_RESULTS)
        except Exception as exc:
            logger.error("discover: ddg query failed %r: %s", q, exc)
            continue
        dt_ms = (time.monotonic() - t0) * 1000
        logger.info("discover: ddg -> %d rows in %.0f ms", len(rows), dt_ms)
        for r in rows:
            href = _scrub_url((r.get("href") or "").strip())
            if not href or not url_fetch_allowed(href):
                continue
            raw_hits.append(
                {
                    "query": q,
                    "title": r.get("title", ""),
                    "url": href,
                    "snippet": r.get("body", ""),
                }
            )

    rolled = _rollup_by_domain(raw_hits)
    logger.info("discover: rollup distinct_domains=%d raw_hits=%d", len(rolled), len(raw_hits))

    if not rolled:
        return {
            "ok": True,
            "suggestions": [],
            "meta": {
                "query": intent,
                "generated_queries": queries,
                "distinct_domains": 0,
                "raw_hits": len(raw_hits),
                "excluded_existing": len(excluded_urls),
                "llm_call_count": llm_calls[0],
                "min_score": _MIN_SUGGESTION_SCORE,
            },
        }

    verdicts = _judge_batches(intent=intent, rolled=rolled, llm_calls=llm_calls)
    rolled_by_domain = {e["domain"]: e for e in rolled}

    suggestions: list[dict[str, Any]] = []
    seen_domains: set[str] = set()
    for v in verdicts:
        domain = v["domain"]
        if not domain:
            continue
        if domain in seen_domains:
            continue
        home = _homepage_url(domain)
        if not home or not url_fetch_allowed(home):
            logger.info("discover: skip domain=%s reason=homepage_not_allowed", domain)
            continue
        home_key = home.lower()
        if home_key in excluded_urls or domain in excluded_hosts:
            logger.info("discover: skip domain=%s reason=excluded_existing", domain)
            continue
        score = int(v.get("score") or 0)
        if score < _MIN_SUGGESTION_SCORE:
            logger.info(
                "discover: skip domain=%s reason=score_below_floor score=%s floor=%s",
                domain,
                score,
                _MIN_SUGGESTION_SCORE,
            )
            continue
        roll = rolled_by_domain.get(domain, {})
        candidates = list(v.get("candidate_urls") or roll.get("candidate_urls") or [])
        picked = _resolve_suggested_url(
            domain=domain,
            candidate_urls=candidates,
            suggested_url=v.get("suggested_url"),
            fallback_home=home,
        )
        if not picked or not url_fetch_allowed(picked):
            logger.info("discover: skip domain=%s reason=picked_url_not_allowed", domain)
            continue
        if picked.lower() in excluded_urls or domain in excluded_hosts:
            logger.info("discover: skip domain=%s reason=picked_or_domain_excluded", domain)
            continue
        seen_domains.add(domain)
        title = (v.get("title") or "").strip() or domain
        verdict = v.get("verdict", "drop")
        suggestions.append(
            {
                "domain": domain,
                "url": picked,
                "title": title,
                "kind": v.get("kind", "other"),
                "score": score,
                "reason": v.get("reason", ""),
                "_verdict": verdict,
            }
        )

    suggestions.sort(
        key=lambda s: (
            _VERDICT_ORDER.get(str(s.get("_verdict", "drop")), 9),
            -(int(s.get("score") or 0)),
            s["domain"],
        )
    )
    for s in suggestions:
        s.pop("_verdict", None)

    logger.info(
        "discover: complete suggestions=%d llm_calls=%d",
        len(suggestions),
        llm_calls[0],
    )

    return {
        "ok": True,
        "suggestions": suggestions,
        "meta": {
            "query": intent,
            "generated_queries": queries,
            "distinct_domains": len(rolled),
            "raw_hits": len(raw_hits),
            "excluded_existing": len(excluded_urls),
            "llm_call_count": llm_calls[0],
            "min_score": _MIN_SUGGESTION_SCORE,
        },
    }
