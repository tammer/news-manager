"""Upsert pipeline results to Supabase news_articles (see database_plan.md)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from news_manager.config import supabase_settings
from news_manager.models import CategoryResult, OutputArticle

logger = logging.getLogger(__name__)

_UPSERT_BATCH_SIZE = 75


def parse_article_date_iso(raw: str | None) -> str | None:
    """
    Parse article date to an ISO 8601 string for timestamptz, or None if unknown.
    Handles trailing Z (UTC). Naive datetimes are interpreted as UTC.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def output_article_to_upsert_row(category: str, article: OutputArticle) -> dict[str, Any]:
    """Row dict for upsert: content fields only — omit read/liked (database_plan §6)."""
    title = article.title.strip()
    headline = title if title else "(no title)"
    row: dict[str, Any] = {
        "category": category,
        "url": article.url,
        "headline": headline,
        "source": article.source,
        "short_summary": article.short_summary,
        "full_summary": article.full_summary,
    }
    ad = parse_article_date_iso(article.date)
    if ad is not None:
        row["article_date"] = ad
    return row


def _default_supabase_client() -> Any:
    try:
        from supabase import create_client
    except ImportError as e:
        raise RuntimeError(
            'The "supabase" package is required for --write-supabase. '
            'Install with: pip install "news-manager[supabase]"'
        ) from e
    url, key = supabase_settings()
    return create_client(url, key)


def sync_category_results_to_supabase(
    results: list[CategoryResult],
    *,
    client: Any | None = None,
    batch_size: int = _UPSERT_BATCH_SIZE,
) -> None:
    """
    Upsert all included articles. Batched. Does not send read/liked so flags survive re-runs.
    """
    rows: list[dict[str, Any]] = []
    for block in results:
        cat = block.category
        for article in block.articles:
            rows.append(output_article_to_upsert_row(cat, article))

    if not rows:
        logger.info("No articles to sync to Supabase.")
        return

    if client is None:
        client = _default_supabase_client()

    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        try:
            (
                client.table("news_articles")
                .upsert(
                    batch,
                    on_conflict="url,category",
                    default_to_null=False,
                )
                .execute()
            )
        except Exception as e:
            raise RuntimeError(
                f"Supabase upsert failed (batch starting at index {i}): {e}"
            ) from e

    logger.info("Synced %d article row(s) to Supabase.", len(rows))
