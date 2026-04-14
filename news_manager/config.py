"""Load .env and environment defaults."""

from __future__ import annotations

import os

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
