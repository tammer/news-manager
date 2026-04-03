"""Orchestrate fetch → summarize → category results."""

from __future__ import annotations

import httpx

from news_manager.cache import ArticleCache
from news_manager.config import DEFAULT_HTTP_TIMEOUT, DEFAULT_MAX_ARTICLES
from news_manager.fetch import (
    USER_AGENT,
    discover_article_targets,
    fetch_single_raw_article,
    normalize_url,
)
from news_manager.models import CategoryResult, SourceCategory
from news_manager.summarize import emit_cached_decision, filter_and_summarize_outcome


def run_pipeline(
    categories: list[SourceCategory],
    instructions: str,
    *,
    max_articles: int = DEFAULT_MAX_ARTICLES,
    http_timeout: float = DEFAULT_HTTP_TIMEOUT,
    content_max_chars: int | None = None,
    cache: ArticleCache | None = None,
) -> list[CategoryResult]:
    """
    For each category, for each source: discover URLs, then for each article either
    use cache, or fetch + filter/summarize. Appends to category.articles; empty categories kept.
    """
    from news_manager.config import DEFAULT_CONTENT_MAX_CHARS

    cm = content_max_chars if content_max_chars is not None else DEFAULT_CONTENT_MAX_CHARS
    out: list[CategoryResult] = []
    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)

    for sc in categories:
        bucket: list[OutputArticle] = []
        for src in sc.sources:
            with httpx.Client(
                headers={"User-Agent": USER_AGENT},
                timeout=http_timeout,
                limits=limits,
            ) as client:
                targets = discover_article_targets(client, src.url, kind=src.kind)
                successes = 0
                for url, feed_date, feed_title in targets:
                    if successes >= max_articles:
                        break
                    nu = normalize_url(url)
                    label_for_excluded = feed_title or nu

                    if cache is not None:
                        hit = cache.lookup(nu, sc.category, instructions, src.filter)
                        if hit is not None:
                            status, cached_article = hit
                            successes += 1
                            if status == "included" and cached_article is not None:
                                bucket.append(cached_article)
                                emit_cached_decision(
                                    "included",
                                    cached_article.title,
                                )
                            else:
                                emit_cached_decision("excluded", label_for_excluded)
                            continue

                    raw = fetch_single_raw_article(
                        client, nu, feed_date, feed_title
                    )
                    if raw is None:
                        continue
                    successes += 1

                    outcome = filter_and_summarize_outcome(
                        raw,
                        category=sc.category,
                        instructions=instructions,
                        content_max_chars=cm,
                        apply_filter=src.filter,
                    )
                    if cache is not None:
                        if outcome.outcome == "included" and outcome.output is not None:
                            cache.put(
                                nu,
                                sc.category,
                                instructions,
                                src.filter,
                                "included",
                                outcome.output,
                            )
                        elif outcome.outcome == "excluded":
                            cache.put(
                                nu,
                                sc.category,
                                instructions,
                                src.filter,
                                "excluded",
                                None,
                            )
                    if outcome.output is not None:
                        bucket.append(outcome.output)
        out.append(CategoryResult(category=sc.category, articles=bucket))

    if cache is not None:
        cache.save()
    return out
