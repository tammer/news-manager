"""Export/import user categories + sources as portable JSON (v2 schema)."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote

import httpx

from news_manager.fetch import normalize_url
from news_manager.supabase_sync import fetch_sources_with_categories

CATALOG_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ImportSummary:
    categories_created: int = 0
    categories_reused: int = 0
    sources_inserted: int = 0
    sources_skipped: int = 0

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)


def fetch_user_id_by_email(*, supabase_url: str, service_role_key: str, email: str) -> str:
    """
    Resolve ``auth.users.id`` from email using the GoTrue admin API (service role).

    Uses ``GET /auth/v1/admin/users?filter=...`` then picks the user whose ``email``
    equals ``email`` (case-insensitive) among returned rows.
    """
    base = supabase_url.strip().rstrip("/")
    em = email.strip()
    if not em:
        raise ValueError("email must be non-empty")
    key = service_role_key.strip()
    if not key:
        raise ValueError("service role key must be non-empty")

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
    }
    # filter helps narrow results; we still require an exact email match in JSON.
    filter_q = quote(em, safe="")
    url = f"{base}/auth/v1/admin/users?per_page=200&filter={filter_q}"

    try:
        with httpx.Client(timeout=30.0) as http:
            resp = http.get(url, headers=headers)
    except httpx.RequestError as e:
        raise RuntimeError(f"Auth admin request failed: {e}") from e

    if resp.status_code == 401:
        raise RuntimeError("Auth admin returned 401 (check SUPABASE_SERVICE_ROLE_KEY).")
    if resp.status_code != 200:
        raise RuntimeError(
            f"Auth admin list users failed: HTTP {resp.status_code}: {resp.text[:500]}"
        )

    try:
        body = resp.json()
    except json.JSONDecodeError as e:
        raise RuntimeError("Auth admin returned non-JSON body.") from e

    users = body.get("users")
    if not isinstance(users, list):
        raise RuntimeError("Auth admin response missing 'users' array.")

    em_lower = em.lower()
    matches: list[dict[str, Any]] = []
    for u in users:
        if not isinstance(u, dict):
            continue
        ue = u.get("email")
        if isinstance(ue, str) and ue.strip().lower() == em_lower:
            matches.append(u)

    if not matches:
        raise RuntimeError(f"No auth user found with email {em!r}.")
    if len(matches) > 1:
        raise RuntimeError(f"Multiple auth users matched email {em!r}; refusing to guess.")

    uid = matches[0].get("id")
    if uid is None:
        raise RuntimeError("Auth user record missing 'id'.")
    return str(uid)


def export_user_sources_catalog(
    client: Any,
    user_id: str,
    *,
    email: str | None = None,
) -> dict[str, Any]:
    """
    Build export dict: schema_version, user_id, optional email, categories with sources.
    Stable order: categories sorted by name, sources sorted by URL.
    """
    rows = fetch_sources_with_categories(client, user_id)
    by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    meta: dict[str, dict[str, str]] = {}

    for row in rows:
        cid = str(row.get("category_id", ""))
        if not cid:
            continue
        if cid not in meta:
            nm = row.get("category_name")
            inst = row.get("category_instruction")
            meta[cid] = {
                "name": nm.strip() if isinstance(nm, str) else "",
                "instruction": inst.strip() if isinstance(inst, str) else "",
            }
        by_cat[cid].append(row)

    categories_out: list[dict[str, Any]] = []
    for cid in sorted(by_cat.keys(), key=lambda c: (meta[c]["name"].lower(), c)):
        name = meta[cid]["name"] or cid
        instruction = meta[cid]["instruction"]
        src_rows = by_cat[cid]
        sources_list: list[dict[str, Any]] = []
        for r in sorted(src_rows, key=lambda x: str(x.get("url", "")).lower()):
            u = r.get("url")
            if not isinstance(u, str) or not u.strip():
                continue
            sources_list.append(
                {"url": u.strip(), "use_rss": bool(r.get("use_rss", False))}
            )
        categories_out.append(
            {
                "category": name,
                "instruction": instruction,
                "sources": sources_list,
            }
        )

    out: dict[str, Any] = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "user_id": user_id,
        "categories": categories_out,
    }
    if email is not None:
        out["email"] = email.strip()
    return out


def _parse_catalog_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate top-level shape; return list of category dicts."""
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a JSON object.")
    ver = payload.get("schema_version", CATALOG_SCHEMA_VERSION)
    if ver != CATALOG_SCHEMA_VERSION:
        raise ValueError(f"Unsupported schema_version: {ver!r} (expected {CATALOG_SCHEMA_VERSION}).")
    cats = payload.get("categories")
    if cats is None:
        raise ValueError("Missing 'categories' array.")
    if not isinstance(cats, list):
        raise ValueError("'categories' must be an array.")
    out: list[dict[str, Any]] = []
    for i, item in enumerate(cats):
        if not isinstance(item, dict):
            raise ValueError(f"categories[{i}] must be an object.")
        cat = item.get("category")
        if not isinstance(cat, str) or not cat.strip():
            raise ValueError(f"categories[{i}] needs non-empty string 'category'.")
        inst_raw = item.get("instruction", "")
        if inst_raw is None:
            instruction = ""
        elif isinstance(inst_raw, str):
            instruction = inst_raw.strip()
        else:
            raise ValueError(f"categories[{i}] 'instruction' must be a string.")
        srcs = item.get("sources")
        if not isinstance(srcs, list) or not srcs:
            raise ValueError(f"categories[{i}] needs non-empty array 'sources'.")
        parsed_sources: list[dict[str, Any]] = []
        for j, raw in enumerate(srcs):
            if not isinstance(raw, dict):
                raise ValueError(f"categories[{i}] sources[{j}] must be an object.")
            u = raw.get("url")
            if not isinstance(u, str) or not u.strip():
                raise ValueError(
                    f"categories[{i}] sources[{j}] needs non-empty string 'url'."
                )
            ur = raw.get("use_rss", False)
            if not isinstance(ur, bool):
                raise ValueError(
                    f"categories[{i}] sources[{j}] 'use_rss' must be a boolean."
                )
            parsed_sources.append({"url": u.strip(), "use_rss": ur})
        out.append(
            {
                "category": cat.strip(),
                "instruction": instruction,
                "sources": parsed_sources,
            }
        )
    return out


def _select_category_id(client: Any, user_id: str, name: str) -> str | None:
    try:
        r = (
            client.table("categories")
            .select("id")
            .eq("user_id", user_id)
            .eq("name", name)
            .execute()
        )
    except Exception as e:
        raise RuntimeError(f"Supabase categories select failed: {e}") from e
    rows = r.data or []
    if not rows:
        return None
    rid = rows[0].get("id")
    return str(rid) if rid is not None else None


def _insert_category(client: Any, user_id: str, name: str, instruction: str) -> str:
    row = {"user_id": user_id, "name": name, "instruction": instruction}
    try:
        r = client.table("categories").insert(row).execute()
    except Exception as e:
        raise RuntimeError(f"Supabase categories insert failed: {e}") from e
    data = r.data or []
    if not data:
        raise RuntimeError("Categories insert returned no row.")
    rid = data[0].get("id")
    if rid is None:
        raise RuntimeError("Categories insert returned no id.")
    return str(rid)


def _load_existing_normalized_urls(client: Any, user_id: str) -> set[str]:
    try:
        r = client.table("sources").select("url").eq("user_id", user_id).execute()
    except Exception as e:
        raise RuntimeError(f"Supabase sources select failed: {e}") from e
    out: set[str] = set()
    for row in r.data or []:
        u = row.get("url")
        if isinstance(u, str) and u.strip():
            try:
                out.add(normalize_url(u.strip()))
            except ValueError:
                continue
    return out


def import_user_sources_catalog(client: Any, user_id: str, payload: dict[str, Any]) -> ImportSummary:
    """
    Insert missing categories and sources for ``user_id``.

    - Category exists (same ``user_id`` + exact ``name``): reuse id; do **not** update instruction.
    - Source exists (same ``user_id`` + same normalized ``url`` in any category): skip insert.
    """
    categories = _parse_catalog_payload(payload)
    existing_urls = _load_existing_normalized_urls(client, user_id)

    categories_created = 0
    categories_reused = 0
    sources_inserted = 0
    sources_skipped = 0

    for block in categories:
        name = str(block["category"])
        instruction = str(block["instruction"])
        cat_id = _select_category_id(client, user_id, name)
        if cat_id is None:
            cat_id = _insert_category(client, user_id, name, instruction)
            categories_created += 1
        else:
            categories_reused += 1

        for src in block["sources"]:
            raw_u = str(src["url"])
            try:
                norm = normalize_url(raw_u)
            except ValueError as e:
                raise ValueError(f"Invalid source URL {raw_u!r}: {e}") from e

            if norm in existing_urls:
                sources_skipped += 1
                continue

            ins = {
                "user_id": user_id,
                "url": norm,
                "use_rss": bool(src["use_rss"]),
                "category_id": cat_id,
            }
            try:
                client.table("sources").insert(ins).execute()
            except Exception as e:
                raise RuntimeError(f"Supabase sources insert failed for {norm!r}: {e}") from e
            existing_urls.add(norm)
            sources_inserted += 1

    return ImportSummary(
        categories_created=categories_created,
        categories_reused=categories_reused,
        sources_inserted=sources_inserted,
        sources_skipped=sources_skipped,
    )
