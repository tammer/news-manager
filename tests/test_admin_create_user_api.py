"""Admin POST /api/admin/users."""

import json
from unittest.mock import MagicMock, patch

import pytest

from news_manager.resolve_app import create_app
from news_manager.user_sources_catalog import ImportSummary


def test_admin_create_user_api_no_admin_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEWS_MANAGER_ADMIN_API_KEY", raising=False)
    app = create_app()
    rv = app.test_client().post(
        "/api/admin/users",
        data=json.dumps({"email": "a@b.c", "password": "12345678"}),
        headers={"Authorization": "Bearer x", "Content-Type": "application/json"},
    )
    assert rv.status_code == 503
    data = rv.get_json()
    assert data["ok"] is False
    assert data["error"] == "server_misconfigured"


def test_admin_create_user_api_wrong_admin_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEWS_MANAGER_ADMIN_API_KEY", "correct-admin-key-12345678")
    app = create_app()
    rv = app.test_client().post(
        "/api/admin/users",
        data=json.dumps({"email": "a@b.c", "password": "12345678"}),
        headers={"Authorization": "Bearer wrong-admin-key-1234567", "Content-Type": "application/json"},
    )
    assert rv.status_code == 401
    assert rv.get_json()["ok"] is False


def test_admin_create_user_api_missing_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEWS_MANAGER_ADMIN_API_KEY", "correct-admin-key-12345678")
    app = create_app()
    rv = app.test_client().post(
        "/api/admin/users",
        data=json.dumps({"email": "a@b.c", "password": "12345678"}),
        headers={"Content-Type": "application/json"},
    )
    assert rv.status_code == 401


@patch("news_manager.resolve_app.import_user_sources_catalog")
@patch("news_manager.resolve_app.create_supabase_client")
@patch("news_manager.resolve_app.create_auth_user_with_password")
def test_admin_create_user_api_ok(
    mock_create_user: MagicMock,
    mock_create_sb: MagicMock,
    mock_import: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    admin_key = "my-admin-api-key-123456789012"
    monkeypatch.setenv("NEWS_MANAGER_ADMIN_API_KEY", admin_key)
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")

    catalog = {
        "schema_version": 1,
        "categories": [
            {
                "category": "Cat",
                "instruction": "Inst",
                "sources": [{"url": "https://example.com/", "use_rss": False}],
            }
        ],
    }
    cat_path = tmp_path / "catalog.json"
    cat_path.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setenv("DEFAULT_USER_CATALOG_PATH", str(cat_path))

    mock_create_user.return_value = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    mock_import.return_value = ImportSummary(
        categories_created=1,
        categories_reused=0,
        sources_inserted=1,
        sources_skipped=0,
    )

    app = create_app()
    rv = app.test_client().post(
        "/api/admin/users",
        data=json.dumps({"email": "new@example.com", "password": "12345678"}),
        headers={"Authorization": f"Bearer {admin_key}", "Content-Type": "application/json"},
    )
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["ok"] is True
    assert data["user_id"] == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    assert data["email"] == "new@example.com"
    assert data["summary"]["sources_inserted"] == 1

    mock_create_user.assert_called_once()
    mock_import.assert_called_once()
    _sb, uid, payload = mock_import.call_args[0]
    assert uid == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    assert payload == catalog
