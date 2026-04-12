"""Load .env, sources.json, instructions.md, and environment defaults."""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

from news_manager.models import Source, SourceCategory

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MAX_ARTICLES = 15
DEFAULT_HTTP_TIMEOUT = 30.0
# Max characters of article body sent to the LLM (plan: document truncation)
DEFAULT_CONTENT_MAX_CHARS = 12000


def load_dotenv_if_present() -> None:
    """Load `.env` from cwd if present. Does not override existing os.environ."""
    load_dotenv(override=False)


def read_sources_json(path: Path) -> list[SourceCategory]:
    """Parse and validate sources.json."""
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {path}: {e}") from e
    if not isinstance(data, list):
        raise ValueError(f"{path} must be a JSON array")
    out: list[SourceCategory] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"{path} item {i} must be an object")
        cat = item.get("category")
        srcs = item.get("sources")
        if not isinstance(cat, str) or not cat.strip():
            raise ValueError(f"{path} item {i} needs non-empty string 'category'")
        if not isinstance(srcs, list) or not srcs:
            raise ValueError(f"{path} item {i} needs non-empty array 'sources'")
        parsed_sources: list[Source] = []
        for j, raw in enumerate(srcs):
            parsed_sources.append(_parse_source_entry(path, i, j, raw))
        out.append(SourceCategory(category=cat.strip(), sources=parsed_sources))
    return out


def _parse_source_entry(path: Path, i: int, j: int, raw: object) -> Source:
    """String (HTML) or object with `url`, optional `kind`, optional `filter` (default true)."""
    if isinstance(raw, str):
        if not raw.strip():
            raise ValueError(f"{path} item {i} sources[{j}] must be a non-empty string")
        return Source(url=raw.strip(), kind="html", filter=True)
    if isinstance(raw, dict):
        u = raw.get("url")
        if not isinstance(u, str) or not u.strip():
            raise ValueError(f"{path} item {i} sources[{j}] object needs non-empty string 'url'")
        kind_raw = raw.get("kind", "html")
        if not isinstance(kind_raw, str):
            raise ValueError(f"{path} item {i} sources[{j}] 'kind' must be a string")
        k = kind_raw.strip().lower()
        if k not in ("html", "rss"):
            raise ValueError(
                f"{path} item {i} sources[{j}] 'kind' must be 'html' or 'rss', got {kind_raw!r}"
            )
        skind = "rss" if k == "rss" else "html"
        filt = raw.get("filter", True)
        if not isinstance(filt, bool):
            raise ValueError(
                f"{path} item {i} sources[{j}] optional 'filter' must be a boolean, got {type(filt).__name__}"
            )
        cookies_raw = raw.get("cookies")
        if cookies_raw is not None:
            if not isinstance(cookies_raw, str) or not cookies_raw.strip():
                raise ValueError(
                    f"{path} item {i} sources[{j}] optional 'cookies' must be a non-empty string path"
                )
            cookies_s = cookies_raw.strip()
        else:
            cookies_s = None
        return Source(url=u.strip(), kind=skind, filter=filt, cookies=cookies_s)
    raise ValueError(
        f"{path} item {i} sources[{j}] must be a string or an object with 'url' (and optional 'kind', 'filter')"
    )


def read_instructions(path: Path) -> str:
    """Read instructions.md as UTF-8 text."""
    return path.read_text(encoding="utf-8")


def groq_api_key() -> str:
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise ValueError(
            "GROQ_API_KEY is not set. Add it to your environment or .env file."
        )
    return key


def groq_model() -> str:
    return os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL).strip() or DEFAULT_GROQ_MODEL


def supabase_jwt_secret() -> str:
    """
    JWT secret from Supabase (Dashboard → Settings → API → JWT Secret).
    Used by `resolve-api` to verify `Authorization: Bearer` user tokens.
    """
    s = os.environ.get("SUPABASE_JWT_SECRET", "").strip()
    if not s:
        raise ValueError(
            "SUPABASE_JWT_SECRET is not set. Add it to your environment or .env file."
        )
    return s


def supabase_settings() -> tuple[str, str]:
    """
    URL and service role key for Supabase REST (required for every CLI run).
    Raises ValueError if either is missing or blank.
    """
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        raise ValueError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set. "
            "Add them to your environment or .env file."
        )
    return url, key
