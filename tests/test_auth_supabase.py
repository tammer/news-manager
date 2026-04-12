"""Supabase JWT verification (HS256 + JWKS path wiring)."""

import base64
import json
import time

import jwt
import pytest

from news_manager.auth_supabase import verify_supabase_jwt, _jwks_client_cached
from news_manager.config import assert_resolve_api_supabase_auth_config


def _b64url(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_assert_resolve_api_supabase_auth_config_requires_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    with pytest.raises(ValueError, match="SUPABASE_URL"):
        assert_resolve_api_supabase_auth_config()


def test_assert_resolve_api_supabase_auth_config_ok_with_url_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    assert_resolve_api_supabase_auth_config()


def test_assert_resolve_api_supabase_auth_config_ok_with_secret_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "x" * 32)
    assert_resolve_api_supabase_auth_config()


def test_verify_es256_requires_supabase_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    _jwks_client_cached.cache_clear()
    header = _b64url({"alg": "ES256", "kid": "test-kid", "typ": "JWT"})
    payload = _b64url({"sub": "u", "exp": int(time.time()) + 60})
    token = f"{header}.{payload}.sig"
    with pytest.raises(jwt.InvalidTokenError, match="SUPABASE_URL"):
        verify_supabase_jwt(token)


def test_verify_hs256_with_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "test-secret-for-jwt-verify-minimum-32-bytes!!"
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    token = jwt.encode(
        {
            "sub": "11111111-1111-1111-1111-111111111111",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        secret,
        algorithm="HS256",
    )
    if isinstance(token, bytes):
        token = token.decode("ascii")
    claims = verify_supabase_jwt(token)
    assert claims["sub"] == "11111111-1111-1111-1111-111111111111"


def test_verify_hs256_fails_without_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    secret = "other-secret-for-jwt-verify-minimum-32-bytes!"
    token = jwt.encode(
        {
            "sub": "u",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        secret,
        algorithm="HS256",
    )
    if isinstance(token, bytes):
        token = token.decode("ascii")
    with pytest.raises(jwt.InvalidTokenError, match="SUPABASE_JWT_SECRET"):
        verify_supabase_jwt(token)


def test_verify_unsupported_alg() -> None:
    header = _b64url({"alg": "none", "typ": "JWT"})
    payload = _b64url({"sub": "u", "exp": int(time.time()) + 60})
    token = f"{header}.{payload}."
    with pytest.raises(jwt.InvalidTokenError, match="Unsupported"):
        verify_supabase_jwt(token)
