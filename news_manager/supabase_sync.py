"""Supabase incremental sync helpers for DB-backed ingest."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from news_manager.config import supabase_settings
from news_manager.fetch import normalize_url
from news_manager.models import OutputArticle

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


def fetch_sources_with_categories(client: Any, user_id: str) -> list[dict[str, Any]]:
    """
    Sources for user with category display name and instruction from public.categories.
    Each dict: source_id, source_name, url, use_rss, category_id, category_name,
    category_instruction
    (``category_instruction`` is stripped text, or ``""`` when null / blank).
    """
    try:
        sr = (
            client.table("sources")
            .select("id, name, url, use_rss, category_id")
            .eq("user_id", user_id)
            .execute()
        )
    except Exception:
        # Some v2 deployments may not have sources.name; retry without it.
        try:
            sr = (
                client.table("sources")
                .select("id, url, use_rss, category_id")
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
    instructions: dict[str, str] = {}
    if cat_ids:
        try:
            cr = (
                client.table("categories")
                .select("id, name, instruction")
                .in_("id", cat_ids)
                .execute()
            )
        except Exception as e:
            raise RuntimeError(f"Supabase fetch categories failed: {e}") from e
        for crow in cr.data or []:
            cid = crow.get("id")
            if cid is None:
                continue
            cid_key = str(cid)
            nm = crow.get("name")
            names[cid_key] = nm.strip() if isinstance(nm, str) else ""
            inst = crow.get("instruction")
            if isinstance(inst, str):
                instructions[cid_key] = inst.strip()
            else:
                instructions[cid_key] = ""

    out: list[dict[str, Any]] = []
    for row in rows:
        cid = row.get("category_id")
        if cid is None:
            continue
        cid_s = str(cid)
        url = row.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        out.append(
            {
                "source_id": str(row.get("id") or ""),
                "source_name": (
                    row.get("name").strip()
                    if isinstance(row.get("name"), str)
                    else ""
                ),
                "url": url.strip(),
                "use_rss": bool(row.get("use_rss", False)),
                "category_id": cid_s,
                "category_name": names.get(cid_s, ""),
                "category_instruction": instructions.get(cid_s, ""),
            }
        )
    return out


def fetch_category_for_user(
    client: Any, *, user_id: str, category_id: str
) -> dict[str, str] | None:
    """Category metadata for one user/category pair, or None when not found."""
    try:
        r = (
            client.table("categories")
            .select("id, name, instruction")
            .eq("id", category_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        raise RuntimeError(
            f"Supabase fetch category failed for user_id={user_id!r} category_id={category_id!r}: {e}"
        ) from e
    rows = r.data or []
    if not rows:
        return None
    row = rows[0]
    name = row.get("name")
    instruction = row.get("instruction")
    return {
        "category_id": str(row.get("id") or category_id),
        "category_name": name.strip() if isinstance(name, str) and name.strip() else category_id,
        "category_instruction": (
            instruction.strip() if isinstance(instruction, str) and instruction.strip() else ""
        ),
    }


def fetch_sources_for_category_user(
    client: Any, *, user_id: str, category_id: str
) -> list[dict[str, Any]]:
    """Sources in a category for one user (minimal fields for evaluation/persistence)."""
    try:
        r = (
            client.table("sources")
            .select("id, url, use_rss")
            .eq("user_id", user_id)
            .eq("category_id", category_id)
            .execute()
        )
    except Exception as e:
        raise RuntimeError(
            f"Supabase fetch category sources failed for user_id={user_id!r} category_id={category_id!r}: {e}"
        ) from e
    out: list[dict[str, Any]] = []
    for row in r.data or []:
        url = row.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        out.append(
            {
                "source_id": str(row.get("id") or "").strip(),
                "url": url.strip(),
                "use_rss": bool(row.get("use_rss", False)),
            }
        )
    return out


def fetch_included_article_for_user(
    client: Any, *, user_id: str, article_id: str
) -> dict[str, str] | None:
    """Resolve a news_articles row by ID scoped to the owning user."""
    try:
        r = (
            client.table("news_articles")
            .select("id, category_id, url")
            .eq("id", article_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        raise RuntimeError(
            f"Supabase fetch included article failed for user_id={user_id!r} article_id={article_id!r}: {e}"
        ) from e
    rows = r.data or []
    if not rows:
        return None
    row = rows[0]
    url = row.get("url")
    category = row.get("category_id")
    if not isinstance(url, str) or not url.strip() or category is None:
        return None
    return {
        "article_id": str(row.get("id") or article_id),
        "category_id": str(category),
        "url": url.strip(),
    }


def _clean_optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def prefetch_processed_urls_v2(
    client: Any, user_id: str, category_id: str
) -> tuple[dict[str, str | None], dict[str, str | None]]:
    """
    Return ({normalized_url: why} in news_articles,
    {normalized_url: why} in news_article_exclusions) for this user and category_id.
    Exclusions are filtered by ``user_id`` and ``category_id`` (natural key remains
    ``category_id`` + ``url``).
    """
    in_articles: dict[str, str | None] = {}
    in_exclusions: dict[str, str | None] = {}
    try:
        r1 = (
            client.table("news_articles")
            .select("url, why")
            .eq("user_id", user_id)
            .eq("category_id", category_id)
            .execute()
        )
        for row in r1.data or []:
            u = row.get("url")
            if isinstance(u, str) and u.strip():
                in_articles[normalize_url(u)] = _clean_optional_text(row.get("why"))
        r2 = (
            client.table("news_article_exclusions")
            .select("url, why")
            .eq("user_id", user_id)
            .eq("category_id", category_id)
            .execute()
        )
        for row in r2.data or []:
            u = row.get("url")
            if isinstance(u, str) and u.strip():
                in_exclusions[normalize_url(u)] = _clean_optional_text(row.get("why"))
    except Exception as e:
        raise RuntimeError(
            f"Supabase prefetch v2 failed for user_id={user_id!r} category_id={category_id!r}: {e}"
        ) from e
    return in_articles, in_exclusions


def output_article_to_upsert_row_v2(
    user_id: str, category_id: str, article: OutputArticle, why: str | None = None
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
        "why": why,
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
    why: str | None = None,
) -> str | None:
    """Upsert v2 news_articles. Returns None on success, or an error message string."""
    row = output_article_to_upsert_row_v2(user_id, category_id, article, why=why)
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
    client: Any,
    user_id: str,
    category_id: str,
    source_id: str,
    url: str,
    why: str | None = None,
) -> str | None:
    """
    Record an excluded URL (v2 PK: category_id, url).
    ``user_id`` and ``source_id`` are stored for RLS and lineage.
    Optional ``why`` explains the filter decision.
    """
    row: dict[str, Any] = {
        "user_id": user_id,
        "category_id": category_id,
        "source_id": source_id,
        "url": url,
        "why": why,
    }
    try:
        (
            client.table("news_article_exclusions")
            .upsert(
                [row],
                on_conflict="category_id,url",
            )
            .execute()
        )
    except Exception as e:
        return f"Supabase exclusion insert: {e}"
    return None


def delete_included_article_v2(
    client: Any, user_id: str, category_id: str, normalized_url: str
) -> str | None:
    """
    Delete one ``news_articles`` row whose stored ``url`` normalizes to ``normalized_url``.
    Returns None on success or if no matching row; otherwise an error message.
    """
    try:
        r = (
            client.table("news_articles")
            .select("url")
            .eq("user_id", user_id)
            .eq("category_id", category_id)
            .execute()
        )
    except Exception as e:
        return f"Supabase delete included (select): {e}"
    url_to_delete: str | None = None
    for row in r.data or []:
        u = row.get("url")
        if isinstance(u, str) and u.strip() and normalize_url(u) == normalized_url:
            url_to_delete = u.strip()
            break
    if url_to_delete is None:
        return None
    try:
        (
            client.table("news_articles")
            .delete()
            .eq("user_id", user_id)
            .eq("category_id", category_id)
            .eq("url", url_to_delete)
            .execute()
        )
    except Exception as e:
        return f"Supabase delete included: {e}"
    return None


def delete_excluded_url_v2(
    client: Any, user_id: str, category_id: str, normalized_url: str
) -> str | None:
    """
    Delete one ``news_article_exclusions`` row whose stored ``url`` normalizes to
    ``normalized_url`` for this ``user_id`` and ``category_id``.
    Returns None on success or if no matching row; otherwise an error message.
    """
    try:
        r = (
            client.table("news_article_exclusions")
            .select("url")
            .eq("user_id", user_id)
            .eq("category_id", category_id)
            .execute()
        )
    except Exception as e:
        return f"Supabase delete excluded (select): {e}"
    url_to_delete: str | None = None
    for row in r.data or []:
        u = row.get("url")
        if isinstance(u, str) and u.strip() and normalize_url(u) == normalized_url:
            url_to_delete = u.strip()
            break
    if url_to_delete is None:
        return None
    try:
        (
            client.table("news_article_exclusions")
            .delete()
            .eq("user_id", user_id)
            .eq("category_id", category_id)
            .eq("url", url_to_delete)
            .execute()
        )
    except Exception as e:
        return f"Supabase delete excluded: {e}"
    return None
