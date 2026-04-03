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
    """Allow a string (HTML homepage) or `{\"url\": \"...\", \"kind\": \"rss\"}`."""
    if isinstance(raw, str):
        if not raw.strip():
            raise ValueError(f"{path} item {i} sources[{j}] must be a non-empty string")
        return Source(url=raw.strip(), kind="html")
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
        return Source(url=u.strip(), kind=skind)
    raise ValueError(
        f"{path} item {i} sources[{j}] must be a string or an object with 'url' (and optional 'kind')"
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
