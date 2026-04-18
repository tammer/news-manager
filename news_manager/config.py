"""Load .env and environment defaults."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MAX_ARTICLES = 15
DEFAULT_HTTP_TIMEOUT = 30.0
# Max characters of article body sent to the LLM (plan: document truncation)
DEFAULT_CONTENT_MAX_CHARS = 12000


def load_dotenv_if_present() -> None:
    """Load `.env` from cwd if present. Does not override existing os.environ."""
    load_dotenv(override=False)


def groq_api_key() -> str:
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise ValueError(
            "GROQ_API_KEY is not set. Add it to your environment or .env file."
        )
    return key


def groq_model() -> str:
    return os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL).strip() or DEFAULT_GROQ_MODEL


def supabase_url_base() -> str | None:
    """`SUPABASE_URL` without trailing slash, or None if unset."""
    u = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    return u or None


def supabase_jwt_secret_optional() -> str | None:
    """
    Legacy symmetric JWT secret (HS256), if set.
    Used with `resolve-api` when access tokens still use HS256.
    """
    s = os.environ.get("SUPABASE_JWT_SECRET", "").strip()
    return s or None


def supabase_jwt_secret() -> str:
    """
    JWT secret from Supabase (legacy HS256 path only).
    Raises if unset — prefer `supabase_jwt_secret_optional()` for optional checks.
    """
    s = supabase_jwt_secret_optional()
    if not s:
        raise ValueError(
            "SUPABASE_JWT_SECRET is not set. Add it to your environment or .env file."
        )
    return s


def assert_resolve_api_supabase_auth_config() -> None:
    """
    `resolve-api` needs at least one of:
    - `SUPABASE_URL` — JWKS verification for ES256/RS256 (JWT signing keys)
    - `SUPABASE_JWT_SECRET` — HS256 legacy verification
    """
    if not supabase_url_base() and not supabase_jwt_secret_optional():
        raise ValueError(
            "Set SUPABASE_URL (for JWT signing keys / JWKS) and/or SUPABASE_JWT_SECRET "
            "(for legacy HS256). At least one is required for resolve-api."
        )


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


def news_manager_admin_api_key_optional() -> str | None:
    """Bearer token value for privileged admin routes (e.g. create user)."""
    s = os.environ.get("NEWS_MANAGER_ADMIN_API_KEY", "").strip()
    return s or None


def load_default_user_catalog_dict() -> dict[str, Any]:
    """
    Load v1 default catalog JSON for new-user provisioning.

    Uses ``DEFAULT_USER_CATALOG_PATH`` when set (any path); otherwise
    ``news_manager/default_user_catalog.json`` next to this package.

    Returns only ``schema_version`` and ``categories`` so export-shaped files
    (extra ``user_id``, ``email``, etc.) do not confuse downstream code.
    """
    path_str = os.environ.get("DEFAULT_USER_CATALOG_PATH", "").strip()
    if path_str:
        p = Path(path_str).expanduser()
        if not p.is_file():
            raise ValueError(f"DEFAULT_USER_CATALOG_PATH is not a file: {p}")
    else:
        p = Path(__file__).resolve().parent / "default_user_catalog.json"
        if not p.is_file():
            raise ValueError(f"Default user catalog missing at {p}")
    text = p.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in default user catalog {p}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"Default user catalog must be a JSON object: {p}")
    ver = data.get("schema_version", 1)
    if ver != 1:
        raise ValueError(
            f"Unsupported schema_version in default user catalog {p}: {ver!r} (expected 1)."
        )
    cats = data.get("categories")
    if cats is None:
        raise ValueError(f"Default user catalog missing 'categories' array: {p}")
    if not isinstance(cats, list):
        raise ValueError(f"Default user catalog 'categories' must be an array: {p}")
    return {"schema_version": 1, "categories": list(cats)}
