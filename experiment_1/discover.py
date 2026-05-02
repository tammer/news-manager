"""Experiment 1: discover quality news/blog sites for a theme.

Standalone program. Single round. Verbose logging is the point.

Pipeline:
  1. User gives a theme.
  2. LLM expands the theme into several DuckDuckGo queries.
  3. We run each query through DuckDuckGo.
  4. Hits are rolled up by domain (best title / snippet / hit count).
  5. LLM judges each domain: keep / maybe / drop, with kind + reason.
  6. Pretty table on stdout; full structured record + log file in runs/.

Read-only imports from `news_manager.config` and `news_manager.llm` — no edits
to the rest of the codebase. DRY does not matter here.
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

from duckduckgo_search import DDGS

from news_manager.config import GROQ_BASE_URL, groq_model, load_dotenv_if_present
from news_manager.llm import get_client


EXPERIMENT_DIR = Path(__file__).resolve().parent
RUNS_DIR = EXPERIMENT_DIR / "runs"

DEFAULT_MAX_QUERIES = 6
DEFAULT_PER_QUERY = 10
DEFAULT_TOP_DOMAINS_FOR_JUDGE = 50

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

    raw_hits = ddg_fetch(queries=queries, per_query=args.per_query)
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
    )
    logger.info("Total LLM calls this run: %d", llm_calls[0])
    logger.info("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
