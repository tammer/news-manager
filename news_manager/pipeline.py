"""Orchestrate fetch → summarize → incremental Supabase writes."""

from __future__ import annotations

import logging
from collections import defaultdict
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
from news_manager.models import (
    CategoryResult,
    IngestSource,
    OutputArticle,
    SourceCategory,
    UserPipelineResult,
)
from news_manager.run_report import (
    report_already_excluded,
    report_already_in_articles,
    report_processed,
)
from news_manager.supabase_sync import (
    fetch_sources_with_categories,
    fetch_user_instructions,
    list_user_ids_with_sources,
    prefetch_processed_urls_for_category,
    prefetch_processed_urls_v2,
    upsert_excluded_url,
    upsert_excluded_url_v2,
    upsert_included_article,
    upsert_included_article_v2,
)
from news_manager.summarize import filter_and_summarize_outcome, format_ingest_instructions

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
    combined_instructions = format_ingest_instructions(instructions, "")

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
                        instructions=combined_instructions,
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
                        err = upsert_excluded_url(
                            supabase_client, nu, sc.category, outcome.exclude_why
                        )
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


def run_pipeline_from_db(
    *,
    supabase_client: Any,
    max_articles: int = DEFAULT_MAX_ARTICLES,
    http_timeout: float = DEFAULT_HTTP_TIMEOUT,
    content_max_chars: int | None = None,
) -> list[UserPipelineResult]:
    """
    For each user that has sources, load global + per-source instructions from Supabase,
    prefetch v2 ``news_articles`` / ``news_article_exclusions`` by ``category_id``,
    then fetch → filter/summarize → upsert (v2). Same normalized URL once per category.
    """
    from news_manager.config import DEFAULT_CONTENT_MAX_CHARS

    cm = content_max_chars if content_max_chars is not None else DEFAULT_CONTENT_MAX_CHARS
    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    out: list[UserPipelineResult] = []

    for user_id in list_user_ids_with_sources(supabase_client):
        print(f"user {user_id}")
        global_i = fetch_user_instructions(supabase_client, user_id)
        rows = fetch_sources_with_categories(supabase_client, user_id)
        by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_cat[str(row["category_id"])].append(row)

        cat_results: list[CategoryResult] = []
        for category_id in sorted(by_cat.keys()):
            src_rows = by_cat[category_id]
            category_name = ""
            for r in src_rows:
                cn = r.get("category_name")
                if isinstance(cn, str) and cn.strip():
                    category_name = cn.strip()
                    break
            if not category_name:
                category_name = category_id

            bucket: list[OutputArticle] = []
            urls_done_this_category: set[str] = set()
            db_included, db_excluded = prefetch_processed_urls_v2(
                supabase_client, user_id, category_id
            )

            for row in src_rows:
                ing = IngestSource(
                    url=str(row["url"]),
                    category_id=category_id,
                    category_name=category_name,
                    use_rss=bool(row.get("use_rss", False)),
                    instruction=str(row.get("instruction") or ""),
                )
                src = ing.to_fetch_source()
                combined = format_ingest_instructions(global_i, ing.instruction)
                source_label = source_base_label(ing.url)
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
                            category=category_name,
                            instructions=combined,
                            content_max_chars=cm,
                            apply_filter=src.filter,
                            source=source_label,
                            emit_stderr=False,
                        )

                        if outcome.outcome == "included" and outcome.output is not None:
                            err = upsert_included_article_v2(
                                supabase_client,
                                user_id,
                                category_id,
                                outcome.output,
                            )
                            if err:
                                report_processed(nu, category_name, False, err)
                            else:
                                bucket.append(outcome.output)
                                report_processed(
                                    nu, category_name, True, result="included"
                                )
                            urls_done_this_category.add(nu)
                        elif outcome.outcome == "excluded":
                            err = upsert_excluded_url_v2(
                                supabase_client, nu, category_id, outcome.exclude_why
                            )
                            if err:
                                report_processed(nu, category_name, False, err)
                            else:
                                report_processed(
                                    nu, category_name, True, result="excluded"
                                )
                                db_excluded.add(nu)
                            urls_done_this_category.add(nu)
                        else:
                            report_processed(
                                nu,
                                category_name,
                                False,
                                "LLM or parse error",
                            )

            cat_results.append(CategoryResult(category=category_name, articles=bucket))

        out.append(UserPipelineResult(user_id=user_id, categories=cat_results))

    return out
