"""Experiment 1: discover quality news/blog sites for a theme.

Standalone program. Single round. Verbose logging is the point.

Pipeline:
  1. User gives a theme.
  2. LLM expands the theme into several DuckDuckGo queries.
  3. We run each query through DuckDuckGo.
  3b. Heuristic "curation list" DDG hits are fetched; outbound links become extra hits.
  4. Hits are rolled up by domain (best title / snippet / hit count).
  5. LLM judges each domain: keep / maybe / drop, with kind + reason.
  6. Pretty table on stdout; full structured record + log file in runs/.

Imports `news_manager.config`, `news_manager.llm`, and URL safety helpers from
`news_manager.source_resolve` for hub fetches only.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS

from news_manager.config import DEFAULT_HTTP_TIMEOUT, GROQ_BASE_URL, groq_model, load_dotenv_if_present
from news_manager.llm import get_client
from news_manager.source_resolve import _scrub_url, url_fetch_allowed


EXPERIMENT_DIR = Path(__file__).resolve().parent
RUNS_DIR = EXPERIMENT_DIR / "runs"

DEFAULT_MAX_QUERIES = 6
DEFAULT_PER_QUERY = 10
DEFAULT_TOP_DOMAINS_FOR_JUDGE = 50
DEFAULT_MAX_HUB_PAGES = 4
DEFAULT_HUB_MAX_LINKS_PER_PAGE = 45
_MAX_HUB_HTML_BYTES = 512_000
_HUB_USER_AGENT = "news-manager-experiment-1-hub-crawl/1.0"
# Skip outbound targets that are almost never publication homepages for this task.
_SKIP_HUB_LINK_TARGET_DOMAINS = frozenset(
    {
        "twitter.com",
        "x.com",
        "facebook.com",
        "instagram.com",
        "linkedin.com",
        "reddit.com",
        "pinterest.com",
        "tiktok.com",
        "youtube.com",
        "youtu.be",
        "t.co",
        "discord.gg",
        "threads.net",
        "bsky.app",
        "wikipedia.org",
        "amazon.com",
        "amzn.to",
    }
)

logger = logging.getLogger("experiment")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return cleaned[:60] or "theme"


def setup_logging(theme: str) -> tuple[Path, Path]:
    """Configure the experiment logger; return (log_path, json_path)."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"{stamp}__{_slugify(theme)}"
    log_path = RUNS_DIR / f"{base}.log"
    json_path = RUNS_DIR / f"{base}.json"

    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(console)

    file_h = logging.FileHandler(log_path, encoding="utf-8")
    file_h.setLevel(logging.DEBUG)
    file_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(message)s"))
    logger.addHandler(file_h)

    return log_path, json_path


def log_section(title: str) -> None:
    bar = "=" * 72
    logger.info(bar)
    logger.info(title)
    logger.info(bar)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def extract_base_domain(raw_url: str) -> str:
    """Lowercased host with `www.` stripped. Mirrors discover_experiment.py."""
    candidate = (raw_url or "").strip()
    if not candidate:
        return ""
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urllib.parse.urlparse(candidate)
    host = (parsed.netloc or "").lower()
    if "@" in host:
        host = host.split("@", 1)[1]
    if ":" in host:
        host = host.split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def curation_page_score(*, title: str, snippet: str, url: str) -> int:
    """Heuristic strength that a DDG hit points at a list/roundup of blogs or sites."""
    text = f"{title} {snippet}".lower()
    path = (urllib.parse.urlparse(url).path or "").lower()
    score = 0
    if re.search(r"\b\d+\s+(best|top|greatest|essential)\b", text):
        score += 2
    if re.search(r"\b(best|top|greatest|essential)\s+\d+\b", text):
        score += 2
    phrases = (
        "best blogs",
        "top blogs",
        "blogs to read",
        "blogs you",
        "bloggers to",
        "must-read",
        "must read",
        "blog roundup",
        "blogs every",
        "newsletters to",
        "newsletter roundup",
        "favorite blogs",
        "great blogs",
        "blogs for ",
        "sites to follow",
        "websites to follow",
    )
    if any(p in text for p in phrases):
        score += 2
    path_bits = ("best-", "top-", "roundup", "list-of", "blogs-to", "newsletters")
    if any(b in path for b in path_bits):
        score += 1
    return score


def looks_like_curation_page(*, title: str, snippet: str, url: str) -> bool:
    return curation_page_score(title=title, snippet=snippet, url=url) >= 2


def _fetch_hub_page_html(url: str) -> tuple[str | None, str | None, str | None]:
    """GET ``url`` and return ``(html, error_message, final_url)``.

    ``final_url`` is the last URL after redirects (for resolving relative links and
    same-host filtering). On failure, ``html`` and ``final_url`` are None.
    """
    cleaned = _scrub_url(url.strip())
    if not cleaned or not url_fetch_allowed(cleaned):
        return None, "url_not_allowed", None
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=DEFAULT_HTTP_TIMEOUT,
            headers={"User-Agent": _HUB_USER_AGENT},
        ) as client:
            resp = client.get(cleaned)
            resp.raise_for_status()
    except Exception as exc:
        return None, str(exc), None
    final = _scrub_url(str(resp.url))
    ctype = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if ctype and "html" not in ctype and "text/plain" not in ctype:
        return None, f"unexpected_content_type:{ctype or 'empty'}", final
    raw = resp.content[:_MAX_HUB_HTML_BYTES]
    try:
        html = raw.decode(resp.encoding or "utf-8", errors="replace")
    except Exception:
        html = raw.decode("utf-8", errors="replace")
    return html, None, final


def _extract_external_domains_from_html(
    *,
    html: str,
    page_url: str,
    hub_domain: str,
    max_links: int,
) -> list[tuple[str, str]]:
    """Return up to ``max_links`` (absolute_url, anchor_text) for external http(s) hosts."""
    soup = BeautifulSoup(html, "html.parser")
    seen_target_domains: set[str] = set()
    out: list[tuple[str, str]] = []
    base = _scrub_url(page_url)
    for tag in soup.find_all("a", href=True):
        if len(out) >= max_links:
            break
        raw_href = (tag.get("href") or "").strip()
        if not raw_href or raw_href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urllib.parse.urljoin(base, raw_href)
        scrubbed = _scrub_url(absolute)
        if not scrubbed or not url_fetch_allowed(scrubbed):
            continue
        parsed = urllib.parse.urlparse(scrubbed)
        if parsed.scheme not in ("http", "https"):
            continue
        target_domain = extract_base_domain(scrubbed)
        if not target_domain or target_domain == hub_domain:
            continue
        if target_domain in _SKIP_HUB_LINK_TARGET_DOMAINS:
            continue
        if target_domain in seen_target_domains:
            continue
        seen_target_domains.add(target_domain)
        anchor = " ".join(tag.get_text(" ", strip=True).split())[:200]
        out.append((scrubbed, anchor))
    return out


def expand_raw_hits_via_hub_crawls(
    raw_hits: list[dict[str, Any]],
    *,
    max_hubs: int,
    max_links_per_hub: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Append synthetic hits from outbound links on detected curation/list pages.

    Returns ``(combined_hits, hub_events)`` where ``combined_hits`` is ``raw_hits`` plus
    new rows with ``hit_source`` ``hub_link``, and ``hub_events`` logs each hub attempt.
    """
    log_section("STEP 3b: hub pages (crawl outbound links)")
    if max_hubs <= 0:
        logger.info("[hub] disabled (max_hubs=%d)", max_hubs)
        return list(raw_hits), []

    candidates: list[tuple[int, str, dict[str, Any]]] = []
    seen_hub_urls: set[str] = set()
    for h in raw_hits:
        title = str(h.get("title", "") or "")
        snippet = str(h.get("snippet", "") or "")
        url = str(h.get("url", "") or "").strip()
        if not url or not looks_like_curation_page(title=title, snippet=snippet, url=url):
            continue
        cleaned = _scrub_url(url)
        if not cleaned or not url_fetch_allowed(cleaned):
            continue
        key = cleaned.lower()
        if key in seen_hub_urls:
            continue
        seen_hub_urls.add(key)
        score = curation_page_score(title=title, snippet=snippet, url=cleaned)
        candidates.append((score, cleaned, h))

    candidates.sort(key=lambda t: (-t[0], t[1]))
    hub_events: list[dict[str, Any]] = []
    synthetic: list[dict[str, Any]] = []

    for idx, (_score, hub_url, source_hit) in enumerate(candidates[:max_hubs], start=1):
        hub_title = str(source_hit.get("title", "") or "")[:200]
        logger.info("[hub] (%d/%d) fetch %s", idx, min(max_hubs, len(candidates)), hub_url)
        t0 = time.monotonic()
        html, err, final_url = _fetch_hub_page_html(hub_url)
        dt_ms = (time.monotonic() - t0) * 1000
        page_base = final_url or hub_url
        hub_domain_resolved = extract_base_domain(page_base)
        event: dict[str, Any] = {
            "hub_url": hub_url,
            "hub_domain": hub_domain_resolved,
            "final_url": final_url,
            "ok": html is not None,
            "error": err,
            "ms": round(dt_ms, 1),
            "links_emitted": 0,
        }
        if html is None:
            logger.warning("[hub] fetch failed %s: %s", hub_url, err)
            hub_events.append(event)
            time.sleep(0.35)
            continue
        pairs = _extract_external_domains_from_html(
            html=html,
            page_url=page_base,
            hub_domain=hub_domain_resolved,
            max_links=max_links_per_hub,
        )
        event["links_emitted"] = len(pairs)
        hub_events.append(event)
        logger.info("[hub]   -> %d external link targets in %.0f ms", len(pairs), dt_ms)
        for link_url, anchor in pairs:
            synthetic.append(
                {
                    "query": f"[hub:{hub_domain_resolved}]",
                    "title": anchor or link_url,
                    "url": link_url,
                    "snippet": f"Linked from curation page: {hub_title or hub_url}"[:500],
                    "hit_source": "hub_link",
                    "hub_referrer": page_base,
                }
            )
        time.sleep(0.35)

    if not synthetic:
        logger.info("[hub] no synthetic hits added (no qualifying hubs or no links)")
    else:
        logger.info("[hub] added %d synthetic hits from %d hub pages", len(synthetic), len(hub_events))

    combined = list(raw_hits)
    for row in synthetic:
        combined.append(row)
    return combined, hub_events


def _strip_code_fences(text: str) -> str:
    """Strip leading/trailing ```...``` fences if the model added them."""
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9]*\s*", "", s)
        if s.endswith("```"):
            s = s[: -3]
    return s.strip()


def _parse_json_obj(text: str) -> Any:
    return json.loads(_strip_code_fences(text))


# ---------------------------------------------------------------------------
# LLM chat helper
# ---------------------------------------------------------------------------


def llm_chat(
    *,
    model: str,
    system: str,
    user: str,
    response_format_json: bool,
    label: str,
    llm_calls: list[int] | None = None,
) -> str:
    """Call the chat endpoint and log prompt size + raw response.

    If ``llm_calls`` is a one-element list of ints, increment it by one after each
    successful ``chat.completions.create`` (used for run totals at the end).
    """
    client = get_client()
    logger.debug("[%s] system prompt (%d chars):\n%s", label, len(system), system)
    logger.debug("[%s] user prompt   (%d chars):\n%s", label, len(user), user)

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }
    if response_format_json:
        kwargs["response_format"] = {"type": "json_object"}

    t0 = time.monotonic()
    completion = client.chat.completions.create(**kwargs)
    if llm_calls is not None:
        llm_calls[0] += 1
    dt_ms = (time.monotonic() - t0) * 1000
    content = (completion.choices[0].message.content or "").strip()
    usage = getattr(completion, "usage", None)
    logger.info(
        "[%s] LLM done in %.0f ms (model=%s, response=%d chars, usage=%s)",
        label,
        dt_ms,
        model,
        len(content),
        getattr(usage, "model_dump", lambda: usage)() if usage is not None else None,
    )
    logger.debug("[%s] raw response:\n%s", label, content)
    return content


# ---------------------------------------------------------------------------
# Step: query generation
# ---------------------------------------------------------------------------


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


def generate_queries(
    *,
    theme: str,
    model: str,
    max_queries: int,
    llm_calls: list[int] | None = None,
) -> list[str]:
    log_section("STEP 2: LLM generates DDG queries")
    user = f"Theme: {theme}\nReturn between 4 and {max_queries} queries."

    raw = llm_chat(
        model=model,
        system=QUERY_GEN_SYSTEM,
        user=user,
        response_format_json=True,
        label="query-gen",
        llm_calls=llm_calls,
    )

    queries: list[str] = []
    for attempt in (1, 2):
        try:
            data = _parse_json_obj(raw)
            qs = data.get("queries") if isinstance(data, dict) else None
            if not isinstance(qs, list):
                raise ValueError("'queries' missing or not a list")
            queries = [str(q).strip() for q in qs if str(q).strip()]
            break
        except Exception as exc:
            logger.warning("[query-gen] parse attempt %d failed: %s", attempt, exc)
            if attempt == 2:
                raise
            raw = llm_chat(
                model=model,
                system=QUERY_GEN_SYSTEM,
                user=user + "\n\nReturn ONLY valid JSON. Last attempt failed to parse.",
                response_format_json=True,
                label="query-gen-retry",
                llm_calls=llm_calls,
            )

    queries = queries[:max_queries]
    logger.info("[query-gen] %d queries:", len(queries))
    for i, q in enumerate(queries, start=1):
        logger.info("  %d. %s", i, q)
    return queries


# ---------------------------------------------------------------------------
# Step: DuckDuckGo retrieval
# ---------------------------------------------------------------------------


def ddg_fetch(*, queries: list[str], per_query: int) -> list[dict[str, Any]]:
    log_section("STEP 3: DuckDuckGo retrieval")
    all_hits: list[dict[str, Any]] = []
    for idx, query in enumerate(queries, start=1):
        logger.info("[ddg] (%d/%d) querying: %s", idx, len(queries), query)
        t0 = time.monotonic()
        try:
            with DDGS() as ddgs:
                results = list(
                    ddgs.text(
                        query,
                        max_results=per_query,
                        safesearch="moderate",
                        region="wt-wt",
                    )
                )
        except Exception as exc:
            logger.error("[ddg] query failed (%s): %s", query, exc)
            continue
        dt_ms = (time.monotonic() - t0) * 1000
        logger.info("[ddg]   -> %d results in %.0f ms", len(results), dt_ms)
        for hit_index, r in enumerate(results, start=1):
            title = (r.get("title") or "").strip()
            href = (r.get("href") or r.get("url") or "").strip()
            body = (r.get("body") or "").strip()
            logger.debug(
                "[ddg]   %d) title=%r href=%r body=%r", hit_index, title, href, body
            )
            all_hits.append(
                {
                    "query": query,
                    "title": title,
                    "url": href,
                    "snippet": body,
                    "hit_source": "ddg",
                }
            )
    logger.info("[ddg] total raw hits across all queries: %d", len(all_hits))
    return all_hits


# ---------------------------------------------------------------------------
# Step: domain rollup
# ---------------------------------------------------------------------------


def rollup_by_domain(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    log_section("STEP 4: rolling up hits by domain")
    by_domain: dict[str, dict[str, Any]] = {}
    for h in hits:
        domain = extract_base_domain(h["url"])
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
                "sample_url": "",
            },
        )
        entry["hit_count"] += 1
        if h["query"] not in entry["queries"]:
            entry["queries"].append(h["query"])
        if len(h["title"]) > len(entry["title"]):
            entry["title"] = h["title"]
        if len(h["snippet"]) > len(entry["snippet"]):
            entry["snippet"] = h["snippet"]
        if not entry["sample_url"]:
            entry["sample_url"] = h["url"]

    rolled = sorted(by_domain.values(), key=lambda e: e["hit_count"], reverse=True)
    logger.info("[rollup] %d distinct domains", len(rolled))
    top_to_show = min(20, len(rolled))
    if top_to_show:
        logger.info("[rollup] top %d domains by hit count:", top_to_show)
        for e in rolled[:top_to_show]:
            logger.info(
                "  %3d  %-40s  %s",
                e["hit_count"],
                e["domain"][:40],
                (e["title"] or "")[:60],
            )
    logger.debug("[rollup] full rollup:\n%s", json.dumps(rolled, indent=2))
    return rolled


# ---------------------------------------------------------------------------
# Step: LLM judge
# ---------------------------------------------------------------------------


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

Output strict JSON only, no prose, in this shape:
{"verdicts": [{"domain": "...", "verdict": "keep|maybe|drop", "score": 1-5,
"kind": "news|blog|newsletter|aggregator|forum|other", "reason": "short reason"}]}

Return one entry per input domain. Do not invent domains.
"""


def judge_domains(
    *,
    theme: str,
    rolled: list[dict[str, Any]],
    model: str,
    top_n: int,
    llm_calls: list[int] | None = None,
) -> list[dict[str, Any]]:
    log_section("STEP 5: LLM judges domains")
    if not rolled:
        logger.info("[judge] no domains to judge")
        return []

    candidates = rolled[:top_n]
    payload = [
        {
            "domain": e["domain"],
            "title": e["title"],
            "snippet": e["snippet"],
            "hit_count": e["hit_count"],
            "sample_url": e["sample_url"],
        }
        for e in candidates
    ]
    user = (
        f"Theme: {theme}\n\n"
        f"Domains to evaluate ({len(payload)}):\n"
        + json.dumps(payload, indent=2)
    )
    logger.info("[judge] sending %d domains; user prompt %d chars", len(payload), len(user))

    raw = llm_chat(
        model=model,
        system=JUDGE_SYSTEM,
        user=user,
        response_format_json=True,
        label="judge",
        llm_calls=llm_calls,
    )

    verdicts: list[dict[str, Any]] = []
    for attempt in (1, 2):
        try:
            data = _parse_json_obj(raw)
            vs = data.get("verdicts") if isinstance(data, dict) else None
            if not isinstance(vs, list):
                raise ValueError("'verdicts' missing or not a list")
            verdicts = [v for v in vs if isinstance(v, dict) and v.get("domain")]
            break
        except Exception as exc:
            logger.warning("[judge] parse attempt %d failed: %s", attempt, exc)
            if attempt == 2:
                logger.error("[judge] giving up; returning empty verdicts")
                return []
            raw = llm_chat(
                model=model,
                system=JUDGE_SYSTEM,
                user=user + "\n\nReturn ONLY valid JSON. Last attempt failed to parse.",
                response_format_json=True,
                label="judge-retry",
                llm_calls=llm_calls,
            )

    by_domain = {e["domain"]: e for e in candidates}
    enriched: list[dict[str, Any]] = []
    for v in verdicts:
        d = str(v.get("domain", "")).strip().lower()
        meta = by_domain.get(d, {})
        enriched.append(
            {
                "domain": d,
                "verdict": str(v.get("verdict", "")).lower(),
                "score": v.get("score"),
                "kind": str(v.get("kind", "")).lower(),
                "reason": str(v.get("reason", "")),
                "hit_count": meta.get("hit_count", 0),
                "title": meta.get("title", ""),
                "snippet": meta.get("snippet", ""),
                "sample_url": meta.get("sample_url", ""),
            }
        )

    counts = {"keep": 0, "maybe": 0, "drop": 0, "other": 0}
    for v in enriched:
        counts[v["verdict"] if v["verdict"] in counts else "other"] += 1
    logger.info(
        "[judge] verdicts: keep=%d maybe=%d drop=%d other=%d (of %d candidates)",
        counts["keep"],
        counts["maybe"],
        counts["drop"],
        counts["other"],
        len(candidates),
    )
    return enriched


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


VERDICT_RANK = {"keep": 0, "maybe": 1, "drop": 2}


def print_results_table(verdicts: list[dict[str, Any]]) -> None:
    log_section("STEP 6: results")
    if not verdicts:
        logger.info("(no verdicts)")
        return
    ordered = sorted(
        verdicts,
        key=lambda v: (
            VERDICT_RANK.get(v.get("verdict", ""), 9),
            -(v.get("score") or 0),
            v.get("domain", ""),
        ),
    )
    header = f"{'verdict':7}  {'score':5}  {'kind':11}  {'domain':40}  reason"
    logger.info(header)
    logger.info("-" * len(header))
    for v in ordered:
        logger.info(
            "%-7s  %-5s  %-11s  %-40s  %s",
            v.get("verdict", "?"),
            str(v.get("score", "?")),
            v.get("kind", "?"),
            (v.get("domain") or "")[:40],
            (v.get("reason") or "")[:120],
        )


def write_run_record(
    *,
    json_path: Path,
    log_path: Path,
    theme: str,
    model: str,
    queries: list[str],
    raw_hits: list[dict[str, Any]],
    rolled: list[dict[str, Any]],
    verdicts: list[dict[str, Any]],
    llm_call_count: int,
    hub_crawl_events: list[dict[str, Any]] | None = None,
) -> None:
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "theme": theme,
        "model": model,
        "groq_base_url": GROQ_BASE_URL,
        "log_file": str(log_path),
        "llm_call_count": llm_call_count,
        "queries": queries,
        "raw_hits": raw_hits,
        "domain_rollup": rolled,
        "verdicts": verdicts,
        "hub_crawl_events": hub_crawl_events or [],
    }
    json_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    logger.info("[output] wrote %s", json_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Find quality news/blog sites for a theme via DDG + LLM.")
    p.add_argument("theme", nargs="?", help="Theme to discover sites about. If omitted, you'll be prompted.")
    p.add_argument("--max-queries", type=int, default=DEFAULT_MAX_QUERIES, help="Max DDG queries to generate.")
    p.add_argument("--per-query", type=int, default=DEFAULT_PER_QUERY, help="Max DDG results per query.")
    p.add_argument("--top", type=int, default=DEFAULT_TOP_DOMAINS_FOR_JUDGE, help="Top N rolled-up domains sent to the judge LLM.")
    p.add_argument("--model", default=None, help="Override Groq model name (defaults to GROQ_MODEL / project default).")
    p.add_argument("--no-llm-judge", action="store_true", help="Skip the judging step (debugging the retrieval phase).")
    p.add_argument(
        "--no-hub-crawl",
        action="store_true",
        help="Do not fetch curation/list-style DDG URLs to harvest outbound blog links.",
    )
    p.add_argument(
        "--max-hubs",
        type=int,
        default=DEFAULT_MAX_HUB_PAGES,
        help="Max curation-style DDG result pages to fetch (0 disables hub crawl).",
    )
    p.add_argument(
        "--hub-max-links",
        type=int,
        default=DEFAULT_HUB_MAX_LINKS_PER_PAGE,
        help="Max distinct external domains to collect per hub page.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    load_dotenv_if_present()

    theme = (args.theme or "").strip()
    if not theme:
        try:
            theme = input("Theme: ").strip()
        except EOFError:
            theme = ""
    if not theme:
        print("ERROR: no theme provided", file=sys.stderr)
        return 2

    log_path, json_path = setup_logging(theme)
    model = (args.model or groq_model()).strip()
    llm_calls = [0]

    log_section("STEP 1: configuration")
    logger.info("theme:       %s", theme)
    logger.info("model:       %s", model)
    logger.info("groq base:   %s", GROQ_BASE_URL)
    logger.info("max queries: %d", args.max_queries)
    logger.info("per query:   %d", args.per_query)
    logger.info("judge top N: %d", args.top)
    if args.no_hub_crawl or args.max_hubs <= 0:
        logger.info("hub crawl:   off")
    else:
        logger.info("hub crawl:   on (max_hubs=%d, links/hub<=%d)", args.max_hubs, args.hub_max_links)
    logger.info("log file:    %s", log_path)
    logger.info("json file:   %s", json_path)

    try:
        queries = generate_queries(
            theme=theme,
            model=model,
            max_queries=args.max_queries,
            llm_calls=llm_calls,
        )
    except Exception as exc:
        logger.exception("query generation failed: %s", exc)
        logger.info("Total LLM calls this run: %d", llm_calls[0])
        return 1

    if not queries:
        logger.error("no queries produced; aborting")
        logger.info("Total LLM calls this run: %d", llm_calls[0])
        return 1

    raw_hits_ddg = ddg_fetch(queries=queries, per_query=args.per_query)
    hub_crawl_events: list[dict[str, Any]] = []
    if args.no_hub_crawl or args.max_hubs <= 0:
        raw_hits = raw_hits_ddg
    else:
        raw_hits, hub_crawl_events = expand_raw_hits_via_hub_crawls(
            raw_hits_ddg,
            max_hubs=args.max_hubs,
            max_links_per_hub=args.hub_max_links,
        )
    rolled = rollup_by_domain(raw_hits)

    if args.no_llm_judge:
        logger.info("[judge] skipped (--no-llm-judge)")
        verdicts: list[dict[str, Any]] = []
    else:
        try:
            verdicts = judge_domains(
                theme=theme,
                rolled=rolled,
                model=model,
                top_n=args.top,
                llm_calls=llm_calls,
            )
        except Exception as exc:
            logger.exception("judge step failed: %s", exc)
            verdicts = []

    print_results_table(verdicts)
    write_run_record(
        json_path=json_path,
        log_path=log_path,
        theme=theme,
        model=model,
        queries=queries,
        raw_hits=raw_hits,
        rolled=rolled,
        verdicts=verdicts,
        llm_call_count=llm_calls[0],
        hub_crawl_events=hub_crawl_events,
    )
    logger.info("Total LLM calls this run: %d", llm_calls[0])
    logger.info("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
