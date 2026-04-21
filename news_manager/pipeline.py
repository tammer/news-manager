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
    same_site,
    source_base_label,
)
from news_manager.models import (
    CategoryResult,
    IngestSource,
    OutputArticle,
    PipelineDbRunResult,
    UserPipelineResult,
)
from news_manager.run_report import (
    SourceSummary,
    report_article,
    report_category,
    report_decision,
    report_source,
    report_source_summary,
    report_start,
    report_user,
)
from news_manager.supabase_sync import (
    delete_excluded_url_v2,
    delete_included_article_v2,
    fetch_category_for_user,
    fetch_included_article_for_user,
    fetch_sources_for_category_user,
    fetch_sources_with_categories,
    list_user_ids_with_sources,
    prefetch_processed_urls_v2,
    upsert_excluded_url_v2,
    upsert_included_article_v2,
)
from news_manager.summarize import filter_and_summarize_outcome

logger = logging.getLogger(__name__)


def _normalized_selector(selector: str | None) -> str | None:
    if selector is None:
        return None
    value = selector.strip()
    return value.casefold() if value else None


def _trimmed_selector(selector: str | None) -> str | None:
    if selector is None:
        return None
    value = selector.strip()
    return value if value else None


def _matches_selector(row: dict[str, Any], selector: str, keys: tuple[str, ...]) -> bool:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip().casefold() == selector:
            return True
    return False


def _public_article_decision(
    *,
    url: str,
    source: str,
    title: str | None,
    date: str | None,
    short_summary: str | None,
    full_summary: str | None,
    included: bool,
    reason: str | None,
) -> dict[str, Any]:
    """API-safe article row (no raw body)."""
    return {
        "date": date,
        "full_summary": full_summary,
        "short_summary": short_summary,
        "source": source,
        "title": title,
        "url": url,
        "included": included,
        "reason": reason,
    }


def _public_from_output_article(
    out: OutputArticle, *, reason: str | None = None
) -> dict[str, Any]:
    return _public_article_decision(
        url=out.url,
        source=out.source,
        title=out.title,
        date=out.date,
        short_summary=out.short_summary,
        full_summary=out.full_summary,
        included=True,
        reason=reason,
    )


def evaluate_single_article_from_db(
    *,
    supabase_client: Any,
    user_id: str,
    category_id: str,
    url: str | None = None,
    article_id: str | None = None,
    instructions_override: str | None = None,
    persist: bool = False,
    http_timeout: float = DEFAULT_HTTP_TIMEOUT,
    content_max_chars: int | None = None,
) -> dict[str, Any]:
    """
    Evaluate one article against category instructions with optional persistence.

    Returns API-safe decision payload with include/exclude reason and summaries.
    """
    from news_manager.config import DEFAULT_CONTENT_MAX_CHARS

    url_value = url.strip() if isinstance(url, str) else ""
    article_id_value = article_id.strip() if isinstance(article_id, str) else ""
    if bool(url_value) == bool(article_id_value):
        raise ValueError("Provide exactly one of 'url' or 'article_id'.")

    article_url = url_value
    if article_id_value:
        row = fetch_included_article_for_user(
            supabase_client, user_id=user_id, article_id=article_id_value
        )
        if row is None:
            raise LookupError("Article not found for this user.")
        row_category_id = row["category_id"]
        if row_category_id != category_id:
            raise ValueError(
                "Provided category_id does not match the article's category."
            )
        article_url = row["url"]

    normalized_url = normalize_url(article_url)
    category = fetch_category_for_user(
        supabase_client, user_id=user_id, category_id=category_id
    )
    if category is None:
        raise LookupError("Category not found for this user.")

    sources = fetch_sources_for_category_user(
        supabase_client, user_id=user_id, category_id=category_id
    )
    selected_source = sources[0] if sources else None
    for source in sources:
        try:
            if same_site(source["url"], normalized_url):
                selected_source = source
                break
        except ValueError:
            continue

    source_url = selected_source["url"] if selected_source is not None else normalized_url
    source_label = source_base_label(source_url)
    apply_filter = True

    cm = content_max_chars if content_max_chars is not None else DEFAULT_CONTENT_MAX_CHARS
    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    client_kw: dict[str, Any] = {
        "headers": {"User-Agent": USER_AGENT},
        "timeout": http_timeout,
        "limits": limits,
    }
    if selected_source is not None:
        ing = IngestSource(
            url=source_url,
            category_id=category_id,
            category_name=category["category_name"],
            use_rss=bool(selected_source.get("use_rss", False)),
        )
        src = ing.to_fetch_source()
        jar = cookie_jar_for_source(src)
        if jar is not None:
            client_kw["cookies"] = jar

    with httpx.Client(**client_kw) as client:
        raw = fetch_single_raw_article(client, normalized_url, None, None)
    if raw is None:
        return {
            "included": False,
            "reason": "Could not fetch article.",
            "url": normalized_url,
            "title": None,
            "date": None,
            "source": source_label,
            "short_summary": None,
            "full_summary": None,
            "persisted": False,
            "instruction_source": (
                "override"
                if isinstance(instructions_override, str) and instructions_override.strip()
                else "category"
            ),
        }

    instructions = (
        instructions_override.strip()
        if isinstance(instructions_override, str) and instructions_override.strip()
        else category["category_instruction"]
    )
    instruction_source = "override" if instructions_override and instructions_override.strip() else "category"
    outcome = filter_and_summarize_outcome(
        raw,
        category=category["category_name"],
        instructions=instructions,
        content_max_chars=cm,
        apply_filter=apply_filter,
        source=source_label,
        emit_stderr=False,
    )

    persisted = False
    persist_error: str | None = None
    if outcome.outcome == "included" and outcome.output is not None:
        include_why = outcome.why or "Included by filter."
        if persist:
            persist_error = upsert_included_article_v2(
                supabase_client,
                user_id,
                category_id,
                outcome.output,
                include_why,
            )
            persisted = persist_error is None
        payload = _public_from_output_article(outcome.output, reason=include_why)
        payload["persisted"] = persisted
        payload["instruction_source"] = instruction_source
        payload["persist_error"] = persist_error
        return payload

    if outcome.outcome == "excluded":
        why = outcome.why or "Excluded by filter."
        if persist:
            source_id = (
                selected_source["source_id"].strip()
                if selected_source is not None
                and isinstance(selected_source.get("source_id"), str)
                else ""
            )
            if not source_id:
                persist_error = "Could not persist exclusion: no source found for category."
            else:
                persist_error = upsert_excluded_url_v2(
                    supabase_client,
                    user_id,
                    category_id,
                    source_id,
                    normalized_url,
                    why,
                )
            persisted = persist_error is None
        return {
            "included": False,
            "reason": why,
            "url": normalized_url,
            "title": raw.title,
            "date": raw.date,
            "source": source_label,
            "short_summary": None,
            "full_summary": None,
            "persisted": persisted,
            "instruction_source": instruction_source,
            "persist_error": persist_error,
        }

    return {
        "included": False,
        "reason": "LLM or parse error",
        "url": normalized_url,
        "title": raw.title,
        "date": raw.date,
        "source": source_label,
        "short_summary": None,
        "full_summary": None,
        "persisted": False,
        "instruction_source": instruction_source,
        "persist_error": None,
    }


def run_pipeline_from_db(
    *,
    supabase_client: Any,
    max_articles: int = DEFAULT_MAX_ARTICLES,
    http_timeout: float = DEFAULT_HTTP_TIMEOUT,
    content_max_chars: int | None = None,
    user_id_selector: str | None = None,
    category_selector: str | None = None,
    source_selector: str | None = None,
    reprocess: bool = False,
    html_discovery_llm: bool = False,
    verbosity: int = 1,
) -> PipelineDbRunResult:
    """
    For each user that has sources, load category names and instructions from Supabase.
    All sources under the same ``category_id`` share that category’s ``instruction`` text
    for the LLM. Prefetch v2 ``news_articles`` / ``news_article_exclusions`` by
    ``user_id`` and ``category_id``, then fetch → filter/summarize → upsert (v2). Same normalized URL
    once per category.
    """
    from news_manager.config import DEFAULT_CONTENT_MAX_CHARS

    cm = content_max_chars if content_max_chars is not None else DEFAULT_CONTENT_MAX_CHARS
    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    out: list[UserPipelineResult] = []
    article_decisions: list[dict[str, Any]] = []
    user_id_selector_trimmed = _trimmed_selector(user_id_selector)
    category_selector_norm = _normalized_selector(category_selector)
    source_selector_norm = _normalized_selector(source_selector)
    user_ids = list_user_ids_with_sources(supabase_client)
    if user_id_selector_trimmed is not None:
        user_ids = [uid for uid in user_ids if uid == user_id_selector_trimmed]
        if not user_ids:
            logger.info(
                "No users matched --user-id selector: %s", user_id_selector_trimmed
            )

    if not user_ids:
        return PipelineDbRunResult(users=[], article_decisions=[])

    report_start(verbosity=verbosity)

    for user_id in user_ids:
        report_user(verbosity=verbosity, user_id=user_id)
        rows = fetch_sources_with_categories(supabase_client, user_id)
        if category_selector_norm is not None:
            rows = [
                row
                for row in rows
                if _matches_selector(
                    row,
                    category_selector_norm,
                    ("category_id", "category_name"),
                )
            ]
        if source_selector_norm is not None:
            rows = [
                row
                for row in rows
                if _matches_selector(
                    row,
                    source_selector_norm,
                    ("source_id", "source_name"),
                )
            ]
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
            report_category(verbosity=verbosity, category=category_name)

            llm_instructions = ""
            if src_rows:
                ci0 = src_rows[0].get("category_instruction")
                if isinstance(ci0, str):
                    llm_instructions = ci0.strip()

            bucket: list[OutputArticle] = []
            urls_done_this_category: set[str] = set()
            db_included, db_excluded = prefetch_processed_urls_v2(
                supabase_client, user_id, category_id
            )

            for row in src_rows:
                source_id = str(row.get("source_id") or "").strip()
                if not source_id:
                    logger.error(
                        "Skipping source row without source_id for category %s",
                        category_id,
                    )
                    continue
                ing = IngestSource(
                    url=str(row["url"]),
                    category_id=category_id,
                    category_name=category_name,
                    use_rss=bool(row.get("use_rss", False)),
                )
                src = ing.to_fetch_source()
                source_label = source_base_label(ing.url)
                report_source(verbosity=verbosity, source=source_label)
                source_summary = SourceSummary()
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
                use_llm_html = html_discovery_llm and not ing.use_rss
                logger.info(
                    "Pipeline discover: user=%s source_id=%s host=%s force_feed_xml=%s "
                    "html_discovery_llm=%s",
                    user_id,
                    source_id,
                    source_label,
                    ing.use_rss,
                    use_llm_html,
                )
                with httpx.Client(**client_kw) as client:
                    targets = discover_article_targets(
                        client,
                        src.url,
                        force_feed_xml=ing.use_rss,
                        use_llm_for_html=use_llm_html,
                    )
                    successes = 0
                    for url, feed_date, feed_title in targets:
                        if successes >= max_articles:
                            break
                        nu = normalize_url(url)
                        if nu in urls_done_this_category:
                            continue
                        report_article(verbosity=verbosity, url=nu)

                        if nu in db_included:
                            if reprocess:
                                del_err = delete_included_article_v2(
                                    supabase_client, user_id, category_id, nu
                                )
                                if del_err:
                                    logger.warning("%s", del_err)
                                db_included.pop(nu, None)
                            else:
                                why = db_included.get(nu) or "Already in database"
                                report_decision(
                                    verbosity=verbosity,
                                    included=True,
                                    reason=why,
                                )
                                article_decisions.append(
                                    _public_article_decision(
                                        url=nu,
                                        source=source_label,
                                        title=None,
                                        date=None,
                                        short_summary=None,
                                        full_summary=None,
                                        included=True,
                                        reason=why,
                                    )
                                )
                                urls_done_this_category.add(nu)
                                successes += 1
                                source_summary = SourceSummary(
                                    processed=source_summary.processed + 1,
                                    included=source_summary.included + 1,
                                    rejected=source_summary.rejected,
                                )
                                continue
                        if nu in db_excluded:
                            if reprocess:
                                del_err = delete_excluded_url_v2(
                                    supabase_client, user_id, category_id, nu
                                )
                                if del_err:
                                    logger.warning("%s", del_err)
                                db_excluded.pop(nu, None)
                            else:
                                why = db_excluded.get(nu) or "Already excluded"
                                report_decision(
                                    verbosity=verbosity,
                                    included=False,
                                    reason=why,
                                )
                                article_decisions.append(
                                    _public_article_decision(
                                        url=nu,
                                        source=source_label,
                                        title=None,
                                        date=None,
                                        short_summary=None,
                                        full_summary=None,
                                        included=False,
                                        reason=why,
                                    )
                                )
                                urls_done_this_category.add(nu)
                                successes += 1
                                source_summary = SourceSummary(
                                    processed=source_summary.processed + 1,
                                    included=source_summary.included,
                                    rejected=source_summary.rejected + 1,
                                )
                                continue

                        raw = fetch_single_raw_article(client, nu, feed_date, feed_title)
                        if raw is None:
                            reason = "Could not fetch article"
                            report_decision(
                                verbosity=verbosity,
                                included=False,
                                reason=reason,
                            )
                            article_decisions.append(
                                _public_article_decision(
                                    url=nu,
                                    source=source_label,
                                    title=None,
                                    date=None,
                                    short_summary=None,
                                    full_summary=None,
                                    included=False,
                                    reason=reason,
                                )
                            )
                            source_summary = SourceSummary(
                                processed=source_summary.processed + 1,
                                included=source_summary.included,
                                rejected=source_summary.rejected + 1,
                            )
                            continue
                        successes += 1

                        outcome = filter_and_summarize_outcome(
                            raw,
                            category=category_name,
                            instructions=llm_instructions,
                            content_max_chars=cm,
                            apply_filter=src.filter,
                            source=source_label,
                            emit_stderr=False,
                        )

                        if outcome.outcome == "included" and outcome.output is not None:
                            include_why = outcome.why or "Included by filter."
                            err = upsert_included_article_v2(
                                supabase_client,
                                user_id,
                                category_id,
                                outcome.output,
                                include_why,
                            )
                            if err:
                                report_decision(
                                    verbosity=verbosity,
                                    included=False,
                                    reason=err,
                                )
                                article_decisions.append(
                                    _public_article_decision(
                                        url=nu,
                                        source=source_label,
                                        title=outcome.output.title,
                                        date=outcome.output.date,
                                        short_summary=outcome.output.short_summary,
                                        full_summary=outcome.output.full_summary,
                                        included=False,
                                        reason=err,
                                    )
                                )
                            else:
                                bucket.append(outcome.output)
                                report_decision(
                                    verbosity=verbosity,
                                    included=True,
                                    reason=include_why,
                                )
                                db_included[nu] = include_why
                                article_decisions.append(
                                    _public_from_output_article(
                                        outcome.output, reason=include_why
                                    )
                                )
                            urls_done_this_category.add(nu)
                            source_summary = SourceSummary(
                                processed=source_summary.processed + 1,
                                included=source_summary.included + (0 if err else 1),
                                rejected=source_summary.rejected + (1 if err else 0),
                            )
                        elif outcome.outcome == "excluded":
                            why = outcome.why or "Excluded by filter."
                            err = upsert_excluded_url_v2(
                                supabase_client,
                                user_id,
                                category_id,
                                source_id,
                                nu,
                                why,
                            )
                            if err:
                                report_decision(
                                    verbosity=verbosity,
                                    included=False,
                                    reason=err,
                                )
                                article_decisions.append(
                                    _public_article_decision(
                                        url=nu,
                                        source=source_label,
                                        title=raw.title,
                                        date=raw.date,
                                        short_summary=None,
                                        full_summary=None,
                                        included=False,
                                        reason=err,
                                    )
                                )
                            else:
                                report_decision(
                                    verbosity=verbosity,
                                    included=False,
                                    reason=why,
                                )
                                db_excluded[nu] = why
                                article_decisions.append(
                                    _public_article_decision(
                                        url=nu,
                                        source=source_label,
                                        title=raw.title,
                                        date=raw.date,
                                        short_summary=None,
                                        full_summary=None,
                                        included=False,
                                        reason=why,
                                    )
                                )
                            urls_done_this_category.add(nu)
                            source_summary = SourceSummary(
                                processed=source_summary.processed + 1,
                                included=source_summary.included,
                                rejected=source_summary.rejected + 1,
                            )
                        else:
                            reason = "LLM or parse error"
                            report_decision(
                                verbosity=verbosity,
                                included=False,
                                reason=reason,
                            )
                            article_decisions.append(
                                _public_article_decision(
                                    url=nu,
                                    source=source_label,
                                    title=raw.title,
                                    date=raw.date,
                                    short_summary=None,
                                    full_summary=None,
                                    included=False,
                                    reason=reason,
                                )
                            )
                            source_summary = SourceSummary(
                                processed=source_summary.processed + 1,
                                included=source_summary.included,
                                rejected=source_summary.rejected + 1,
                            )
                report_source_summary(
                    verbosity=verbosity,
                    category=category_name,
                    source=source_label,
                    index_url=ing.url,
                    summary=source_summary,
                )

            cat_results.append(CategoryResult(category=category_name, articles=bucket))

        out.append(UserPipelineResult(user_id=user_id, categories=cat_results))

    return PipelineDbRunResult(users=out, article_decisions=article_decisions)
