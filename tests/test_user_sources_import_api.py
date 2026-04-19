"""POST /api/user/sources/import — auth, validation, and importer (mocked)."""

from __future__ import annotations

import os
import time
from typing import Any
from unittest.mock import MagicMock, patch

import jwt
import pytest

from news_manager.resolve_app import create_app
from news_manager.user_sources_catalog import ImportSummary


@pytest.fixture
def jwt_secret() -> str:
    return "test-secret-for-jwt-verify-minimum-32-bytes!!"


def _authed_headers(jwt_secret: str, *, claims: dict[str, Any]) -> dict[str, str]:
    os.environ["SUPABASE_JWT_SECRET"] = jwt_secret
    payload = {
        "aud": "authenticated",
        "exp": int(time.time()) + 3600,
        "role": "authenticated",
        **claims,
    }
    token = jwt.encode(payload, jwt_secret, algorithm="HS256")
    token_s = token.decode("ascii") if isinstance(token, bytes) else str(token)
    return {"Authorization": f"Bearer {token_s}"}


def _new_client() -> Any:
    app = create_app()
    app.testing = True
    return app.test_client()


_MIN_CATALOG: dict[str, Any] = {
    "schema_version": 1,
    "categories": [
        {
            "category": "Tech",
            "instruction": "",
            "sources": [{"url": "https://example.com/", "use_rss": False}],
        }
    ],
}


def test_user_sources_import_401_without_token(jwt_secret: str) -> None:
    os.environ["SUPABASE_JWT_SECRET"] = jwt_secret
    c = _new_client()
    r = c.post("/api/user/sources/import", json=_MIN_CATALOG)
    assert r.status_code == 401
    data = r.get_json()
    assert data["ok"] is False
    assert data["error"] == "no_results"


def test_user_sources_import_401_bad_token(jwt_secret: str) -> None:
    os.environ["SUPABASE_JWT_SECRET"] = jwt_secret
    c = _new_client()
    r = c.post(
        "/api/user/sources/import",
        json=_MIN_CATALOG,
        headers={"Authorization": "Bearer not-a-valid-token"},
    )
    assert r.status_code == 401
    assert r.get_json()["error"] == "no_results"


def test_user_sources_import_401_missing_sub(jwt_secret: str) -> None:
    """Empty ``sub`` passes JWT decode but fails ``_required_sub`` (same as pipeline)."""
    os.environ["SUPABASE_JWT_SECRET"] = jwt_secret
    c = _new_client()
    headers = _authed_headers(jwt_secret, claims={"sub": ""})
    r = c.post("/api/user/sources/import", json=_MIN_CATALOG, headers=headers)
    assert r.status_code == 401
    data = r.get_json()
    assert data["error"] == "no_results"
    assert "sub" in data["message"].lower()


def test_user_sources_import_400_invalid_json(jwt_secret: str) -> None:
    os.environ["SUPABASE_JWT_SECRET"] = jwt_secret
    c = _new_client()
    headers = _authed_headers(jwt_secret, claims={"sub": "11111111-1111-1111-1111-111111111111"})
    r = c.post(
        "/api/user/sources/import",
        data=b"not json",
        headers={**headers, "Content-Type": "application/json"},
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_json"


def test_user_sources_import_400_invalid_body_not_object(jwt_secret: str) -> None:
    os.environ["SUPABASE_JWT_SECRET"] = jwt_secret
    c = _new_client()
    headers = _authed_headers(jwt_secret, claims={"sub": "11111111-1111-1111-1111-111111111111"})
    r = c.post("/api/user/sources/import", json=[], headers=headers)
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_body"


@patch("news_manager.resolve_app.supabase_settings")
def test_user_sources_import_503_misconfigured(mock_settings: Any, jwt_secret: str) -> None:
    mock_settings.side_effect = ValueError("SUPABASE_URL is not set")
    c = _new_client()
    headers = _authed_headers(jwt_secret, claims={"sub": "11111111-1111-1111-1111-111111111111"})
    r = c.post("/api/user/sources/import", json=_MIN_CATALOG, headers=headers)
    assert r.status_code == 503
    data = r.get_json()
    assert data["error"] == "server_misconfigured"


@patch("news_manager.resolve_app.import_user_sources_catalog")
@patch("news_manager.resolve_app.create_supabase_client")
@patch("news_manager.resolve_app.supabase_settings")
def test_user_sources_import_200_summary(
    mock_supabase_settings: Any,
    mock_create_client: Any,
    mock_import: Any,
    jwt_secret: str,
) -> None:
    mock_supabase_settings.return_value = ("https://x.supabase.co", "key")
    mock_create_client.return_value = MagicMock()
    mock_import.return_value = ImportSummary(
        categories_created=1,
        categories_reused=0,
        sources_inserted=1,
        sources_skipped=0,
    )
    c = _new_client()
    headers = _authed_headers(jwt_secret, claims={"sub": "22222222-2222-2222-2222-222222222222"})
    r = c.post("/api/user/sources/import", json=_MIN_CATALOG, headers=headers)
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["summary"] == {
        "categories_created": 1,
        "categories_reused": 0,
        "sources_inserted": 1,
        "sources_skipped": 0,
    }
    mock_import.assert_called_once()
    _client, uid, payload = mock_import.call_args[0]
    assert uid == "22222222-2222-2222-2222-222222222222"
    assert payload == _MIN_CATALOG


@patch("news_manager.resolve_app.import_user_sources_catalog")
@patch("news_manager.resolve_app.create_supabase_client")
@patch("news_manager.resolve_app.supabase_settings")
def test_user_sources_import_400_validation_error(
    mock_supabase_settings: Any,
    mock_create_client: Any,
    mock_import: Any,
    jwt_secret: str,
) -> None:
    mock_supabase_settings.return_value = ("https://x.supabase.co", "key")
    mock_create_client.return_value = MagicMock()
    mock_import.side_effect = ValueError("Missing 'categories' array.")
    c = _new_client()
    headers = _authed_headers(jwt_secret, claims={"sub": "11111111-1111-1111-1111-111111111111"})
    r = c.post("/api/user/sources/import", json={}, headers=headers)
    assert r.status_code == 400
    assert r.get_json()["error"] == "validation_error"
