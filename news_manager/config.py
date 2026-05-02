"""Load .env and environment defaults."""

from __future__ import annotations

import os

from dotenv import load_dotenv

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MAX_ARTICLES = 10
DEFAULT_HTTP_TIMEOUT = 30.0
# Max characters of article body sent to the LLM (plan: document truncation)
DEFAULT_CONTENT_MAX_CHARS = 12000
DEFAULT_HTML_DISCOVERY_MAX_CANDIDATES = 200
DEFAULT_SCRAPINGDOG_TIMEOUT = 60.0
DEFAULT_SCRAPINGDOG_FALLBACK_ON = {401,403, 429}


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


def groq_model_html_discovery() -> str:
    """Optional smaller/cheaper model for homepage link picking; falls back to ``groq_model()``."""
    m = os.environ.get("GROQ_MODEL_HTML_DISCOVERY", "").strip()
    return m or groq_model()


def html_discovery_max_candidates() -> int:
    """Max homepage links sent to the HTML-discovery LLM (document order, capped)."""
    raw = os.environ.get("HTML_DISCOVERY_MAX_CANDIDATES", "").strip()
    if not raw:
        return DEFAULT_HTML_DISCOVERY_MAX_CANDIDATES
    try:
        n = int(raw)
        return max(1, min(n, 500))
    except ValueError:
        return DEFAULT_HTML_DISCOVERY_MAX_CANDIDATES


def scrapingdog_enabled() -> bool:
    raw = os.environ.get("SCRAPINGDOG_ENABLED", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def scrapingdog_api_key_optional() -> str | None:
    key = os.environ.get("SCRAPINGDOG_API_KEY", "").strip()
    return key or None


def scrapingdog_timeout() -> float:
    raw = os.environ.get("SCRAPINGDOG_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_SCRAPINGDOG_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_SCRAPINGDOG_TIMEOUT
    return max(1.0, min(value, 120.0))


def scrapingdog_fallback_statuses() -> set[int]:
    raw = os.environ.get("SCRAPINGDOG_FALLBACK_ON", "").strip()
    if not raw:
        return set(DEFAULT_SCRAPINGDOG_FALLBACK_ON)
    out: set[int] = set()
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            code = int(token)
        except ValueError:
            continue
        if 100 <= code <= 599:
            out.add(code)
    return out or set(DEFAULT_SCRAPINGDOG_FALLBACK_ON)


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
