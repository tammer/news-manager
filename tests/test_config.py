"""Tests for DB-mode config helpers."""

import pytest

from news_manager.config import (
    assert_resolve_api_supabase_auth_config,
    groq_api_key,
    groq_model_html_discovery,
    html_discovery_max_candidates,
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


def test_groq_model_html_discovery_falls_back_to_groq_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_MODEL_HTML_DISCOVERY", raising=False)
    monkeypatch.setenv("GROQ_MODEL", "model-main")
    assert groq_model_html_discovery() == "model-main"
    monkeypatch.setenv("GROQ_MODEL_HTML_DISCOVERY", "  model-discovery  ")
    assert groq_model_html_discovery() == "model-discovery"


def test_html_discovery_max_candidates_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HTML_DISCOVERY_MAX_CANDIDATES", raising=False)
    assert html_discovery_max_candidates() == 200
    monkeypatch.setenv("HTML_DISCOVERY_MAX_CANDIDATES", "50")
    assert html_discovery_max_candidates() == 50
    monkeypatch.setenv("HTML_DISCOVERY_MAX_CANDIDATES", "not-int")
    assert html_discovery_max_candidates() == 200
    monkeypatch.setenv("HTML_DISCOVERY_MAX_CANDIDATES", "9999")
    assert html_discovery_max_candidates() == 500


def test_assert_resolve_api_supabase_auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    with pytest.raises(ValueError, match="At least one is required for resolve-api"):
        assert_resolve_api_supabase_auth_config()

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    assert_resolve_api_supabase_auth_config()
