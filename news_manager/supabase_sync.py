"""Supabase incremental sync: news_articles + news_article_exclusions (see cache_change_plan.md)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from news_manager.config import supabase_settings
from news_manager.fetch import normalize_url
from news_manager.models import OutputArticle

logger = logging.getLogger(__name__)


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


def create_supabase_client() -> Any:
    """Service-role Supabase client (required for every CLI run)."""
    try:
        from supabase import create_client
    except ImportError as e:
        raise RuntimeError(
            'The "supabase" package is required. Install with: pip install "news-manager"'
        ) from e
    url, key = supabase_settings()
    return create_client(url, key)


def prefetch_processed_urls_for_category(client: Any, category: str) -> tuple[set[str], set[str]]:
    """
    Return (urls in news_articles, urls in news_article_exclusions) for this category,
    keyed by normalize_url(...) for comparison with pipeline URLs.
    """
    in_articles: set[str] = set()
    in_exclusions: set[str] = set()
    try:
        r1 = (
            client.table("news_articles")
            .select("url")
            .eq("category", category)
            .execute()
        )
        for row in r1.data or []:
            u = row.get("url")
            if isinstance(u, str) and u.strip():
                in_articles.add(normalize_url(u))
        r2 = (
            client.table("news_article_exclusions")
            .select("url")
            .eq("category", category)
            .execute()
        )
        for row in r2.data or []:
            u = row.get("url")
            if isinstance(u, str) and u.strip():
                in_exclusions.add(normalize_url(u))
    except Exception as e:
        raise RuntimeError(f"Supabase prefetch failed for category {category!r}: {e}") from e
    return in_articles, in_exclusions


def upsert_included_article(
    client: Any,
    category: str,
    article: OutputArticle,
) -> str | None:
    """
    Single upsert to news_articles. Returns None on success, or an error message string.
    """
    row = output_article_to_upsert_row(category, article)
    try:
        (
            client.table("news_articles")
            .upsert(
                [row],
                on_conflict="url,category",
                default_to_null=False,
            )
            .execute()
        )
    except Exception as e:
        return f"Supabase upsert: {e}"
    return None


def upsert_excluded_url(client: Any, url: str, category: str) -> str | None:
    """
    Record an excluded URL. Returns None on success, or an error message string.
    """
    try:
        (
            client.table("news_article_exclusions")
            .upsert(
                [{"url": url, "category": category}],
                on_conflict="url,category",
            )
            .execute()
        )
    except Exception as e:
        return f"Supabase exclusion insert: {e}"
    return None
