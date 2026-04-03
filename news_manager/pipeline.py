"""Orchestrate fetch → summarize → category results."""

from __future__ import annotations

import logging

from news_manager.config import DEFAULT_HTTP_TIMEOUT, DEFAULT_MAX_ARTICLES
from news_manager.fetch import fetch_articles_for_source
from news_manager.models import CategoryResult, OutputArticle, RawArticle, SourceCategory
from news_manager.summarize import filter_and_summarize

logger = logging.getLogger(__name__)


def run_pipeline(
    categories: list[SourceCategory],
    instructions: str,
    *,
    max_articles: int = DEFAULT_MAX_ARTICLES,
    http_timeout: float = DEFAULT_HTTP_TIMEOUT,
    content_max_chars: int | None = None,
) -> list[CategoryResult]:
    """
    For each category, for each source: fetch articles, then filter/summarize per article.
    Appends to category.articles in fetch order; empty categories are kept.
    """
    from news_manager.config import DEFAULT_CONTENT_MAX_CHARS

    cm = content_max_chars if content_max_chars is not None else DEFAULT_CONTENT_MAX_CHARS
    out: list[CategoryResult] = []
    for sc in categories:
        bucket: list[OutputArticle] = []
        for src in sc.sources:
            raw_list: list[RawArticle] = fetch_articles_for_source(
                src,
                max_articles=max_articles,
                timeout=http_timeout,
            )
            for raw in raw_list:
                done = filter_and_summarize(
                    raw,
                    category=sc.category,
                    instructions=instructions,
                    content_max_chars=cm,
                )
                if done is not None:
                    bucket.append(done)
        out.append(CategoryResult(category=sc.category, articles=bucket))
    return out
