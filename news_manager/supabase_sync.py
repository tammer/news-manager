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


# --- Gistprism v2 (user_id + category_id; see gistprism_v2_implementation_plan.md) ---


def list_user_ids_with_sources(client: Any) -> list[str]:
    """Distinct user_id values that have at least one row in public.sources."""
    try:
        r = client.table("sources").select("user_id").execute()
    except Exception as e:
        raise RuntimeError(f"Supabase list_user_ids_with_sources failed: {e}") from e
    out: set[str] = set()
    for row in r.data or []:
        uid = row.get("user_id")
        if uid is not None:
            out.add(str(uid))
    return sorted(out)


def fetch_user_instructions(client: Any, user_id: str) -> str:
    """Global instruction text for user; empty string if no row."""
    try:
        r = (
            client.table("user_instructions")
            .select("instruction")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        raise RuntimeError(f"Supabase fetch_user_instructions failed: {e}") from e
    rows = r.data or []
    if not rows:
        return ""
    inst = rows[0].get("instruction")
    if not isinstance(inst, str):
        return ""
    return inst.strip()


def fetch_sources_with_categories(client: Any, user_id: str) -> list[dict[str, Any]]:
    """
    Sources for user with category display name resolved from public.categories.
    Each dict: url, use_rss, category_id, category_name, instruction.
    """
    try:
        sr = (
            client.table("sources")
            .select("url, use_rss, category_id, instruction")
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as e:
        raise RuntimeError(f"Supabase fetch_sources failed: {e}") from e
    rows = sr.data or []
    if not rows:
        return []
    cat_ids: list[str] = []
    seen: set[str] = set()
    for row in rows:
        cid = row.get("category_id")
        if cid is None:
            continue
        s = str(cid)
        if s not in seen:
            seen.add(s)
            cat_ids.append(s)
    names: dict[str, str] = {}
    if cat_ids:
        try:
            cr = (
                client.table("categories")
                .select("id, name")
                .in_("id", cat_ids)
                .execute()
            )
        except Exception as e:
            raise RuntimeError(f"Supabase fetch categories failed: {e}") from e
        for crow in cr.data or []:
            cid = crow.get("id")
            if cid is None:
                continue
            nm = crow.get("name")
            names[str(cid)] = nm.strip() if isinstance(nm, str) else ""

    out: list[dict[str, Any]] = []
    for row in rows:
        cid = row.get("category_id")
        if cid is None:
            continue
        cid_s = str(cid)
        url = row.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        inst = row.get("instruction")
        out.append(
            {
                "url": url.strip(),
                "use_rss": bool(row.get("use_rss", False)),
                "category_id": cid_s,
                "category_name": names.get(cid_s, ""),
                "instruction": inst.strip() if isinstance(inst, str) else "",
            }
        )
    return out


def prefetch_processed_urls_v2(
    client: Any, user_id: str, category_id: str
) -> tuple[set[str], set[str]]:
    """
    Return (urls in news_articles, urls in news_article_exclusions) for this user
    and category_id, keyed by normalize_url(...).
    """
    in_articles: set[str] = set()
    in_exclusions: set[str] = set()
    try:
        r1 = (
            client.table("news_articles")
            .select("url")
            .eq("user_id", user_id)
            .eq("category_id", category_id)
            .execute()
        )
        for row in r1.data or []:
            u = row.get("url")
            if isinstance(u, str) and u.strip():
                in_articles.add(normalize_url(u))
        r2 = (
            client.table("news_article_exclusions")
            .select("url")
            .eq("category_id", category_id)
            .execute()
        )
        for row in r2.data or []:
            u = row.get("url")
            if isinstance(u, str) and u.strip():
                in_exclusions.add(normalize_url(u))
    except Exception as e:
        raise RuntimeError(
            f"Supabase prefetch v2 failed for user_id={user_id!r} category_id={category_id!r}: {e}"
        ) from e
    return in_articles, in_exclusions


def output_article_to_upsert_row_v2(
    user_id: str, category_id: str, article: OutputArticle
) -> dict[str, Any]:
    """Row dict for v2 news_articles upsert (content fields; omit read/saved)."""
    title = article.title.strip()
    headline = title if title else "(no title)"
    row: dict[str, Any] = {
        "user_id": user_id,
        "category_id": category_id,
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


def upsert_included_article_v2(
    client: Any,
    user_id: str,
    category_id: str,
    article: OutputArticle,
) -> str | None:
    """Upsert v2 news_articles. Returns None on success, or an error message string."""
    row = output_article_to_upsert_row_v2(user_id, category_id, article)
    try:
        (
            client.table("news_articles")
            .upsert(
                [row],
                on_conflict="user_id,category_id,url",
                default_to_null=False,
            )
            .execute()
        )
    except Exception as e:
        return f"Supabase upsert: {e}"
    return None


def upsert_excluded_url_v2(
    client: Any, url: str, category_id: str
) -> str | None:
    """Record an excluded URL (v2 PK: category_id, url)."""
    try:
        (
            client.table("news_article_exclusions")
            .upsert(
                [{"category_id": category_id, "url": url}],
                on_conflict="category_id,url",
            )
            .execute()
        )
    except Exception as e:
        return f"Supabase exclusion insert: {e}"
    return None
