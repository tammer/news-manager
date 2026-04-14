"""Tests for DB-mode config helpers."""

import pytest

from news_manager.config import (
    assert_resolve_api_supabase_auth_config,
    groq_api_key,
    supabase_jwt_secret_optional,
    supabase_settings,
)


def test_groq_api_key_returns_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "  abc123  ")
    assert groq_api_key() == "abc123"


def test_groq_api_key_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GROQ_API_KEY is not set"):
        groq_api_key()


def test_supabase_settings_returns_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-key")
    assert supabase_settings() == ("https://example.supabase.co", "service-role-key")


def test_supabase_settings_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with pytest.raises(ValueError, match="SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY"):
        supabase_settings()


def test_supabase_jwt_secret_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    assert supabase_jwt_secret_optional() is None
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "  secret  ")
    assert supabase_jwt_secret_optional() == "secret"


def test_assert_resolve_api_supabase_auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    with pytest.raises(ValueError, match="At least one is required for resolve-api"):
        assert_resolve_api_supabase_auth_config()

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    assert_resolve_api_supabase_auth_config()
