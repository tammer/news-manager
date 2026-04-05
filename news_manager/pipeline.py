"""Orchestrate fetch → summarize → incremental Supabase writes."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from news_manager.config import DEFAULT_HTTP_TIMEOUT, DEFAULT_MAX_ARTICLES
from news_manager.cookies_loader import cookie_jar_for_source
from news_manager.fetch import (
    USER_AGENT,
    discover_article_targets,
    fetch_single_raw_article,
    normalize_url,
    source_base_label,
)
from news_manager.models import CategoryResult, OutputArticle, SourceCategory
from news_manager.run_report import (
    report_already_excluded,
    report_already_in_articles,
    report_processed,
)
from news_manager.supabase_sync import (
    prefetch_processed_urls_for_category,
    upsert_excluded_url,
    upsert_included_article,
)
from news_manager.summarize import filter_and_summarize_outcome

logger = logging.getLogger(__name__)


def run_pipeline(
    categories: list[SourceCategory],
    instructions: str,
    *,
    supabase_client: Any,
    max_articles: int = DEFAULT_MAX_ARTICLES,
    http_timeout: float = DEFAULT_HTTP_TIMEOUT,
    content_max_chars: int | None = None,
) -> list[CategoryResult]:
    """
    For each category, prefetch ``news_articles`` / ``news_article_exclusions`` URLs,
    then for each source discover targets and process each URL (skip if already known).
    Same normalized URL is only processed once per category (first source wins).
    """
    from news_manager.config import DEFAULT_CONTENT_MAX_CHARS

    cm = content_max_chars if content_max_chars is not None else DEFAULT_CONTENT_MAX_CHARS
    out: list[CategoryResult] = []
    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)

    for sc in categories:
        bucket: list[OutputArticle] = []
        urls_done_this_category: set[str] = set()
        db_included, db_excluded = prefetch_processed_urls_for_category(
            supabase_client, sc.category
        )

        for src in sc.sources:
            source_label = source_base_label(src.url)
            try:
                jar = cookie_jar_for_source(src)
            except ValueError as e:
                logger.error("%s", e)
                raise
            client_kw: dict = {
                "headers": {"User-Agent": USER_AGENT},
                "timeout": http_timeout,
                "limits": limits,
            }
            if jar is not None:
                client_kw["cookies"] = jar
            with httpx.Client(**client_kw) as client:
                targets = discover_article_targets(client, src.url, kind=src.kind)
                successes = 0
                for url, feed_date, feed_title in targets:
                    if successes >= max_articles:
                        break
                    nu = normalize_url(url)
                    if nu in urls_done_this_category:
                        continue

                    if nu in db_included:
                        report_already_in_articles(nu)
                        urls_done_this_category.add(nu)
                        successes += 1
                        continue
                    if nu in db_excluded:
                        report_already_excluded(nu)
                        urls_done_this_category.add(nu)
                        successes += 1
                        continue

                    raw = fetch_single_raw_article(client, nu, feed_date, feed_title)
                    if raw is None:
                        continue
                    successes += 1

                    outcome = filter_and_summarize_outcome(
                        raw,
                        category=sc.category,
                        instructions=instructions,
                        content_max_chars=cm,
                        apply_filter=src.filter,
                        source=source_label,
                        emit_stderr=False,
                    )

                    if outcome.outcome == "included" and outcome.output is not None:
                        err = upsert_included_article(
                            supabase_client, sc.category, outcome.output
                        )
                        if err:
                            report_processed(nu, sc.category, False, err)
                        else:
                            bucket.append(outcome.output)
                            report_processed(
                                nu, sc.category, True, result="included"
                            )
                        urls_done_this_category.add(nu)
                    elif outcome.outcome == "excluded":
                        err = upsert_excluded_url(supabase_client, nu, sc.category)
                        if err:
                            report_processed(nu, sc.category, False, err)
                        else:
                            report_processed(
                                nu, sc.category, True, result="excluded"
                            )
                            db_excluded.add(nu)
                        urls_done_this_category.add(nu)
                    else:
                        report_processed(
                            nu,
                            sc.category,
                            False,
                            "LLM or parse error",
                        )

        out.append(CategoryResult(category=sc.category, articles=bucket))

    return out
