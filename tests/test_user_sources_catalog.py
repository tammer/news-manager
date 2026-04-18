"""User sources catalog export/import."""

import json
from unittest.mock import MagicMock, patch

import jwt
import pytest

from news_manager.resolve_app import create_app
from news_manager.user_sources_catalog import (
    CATALOG_SCHEMA_VERSION,
    ImportSummary,
    export_user_sources_catalog,
    fetch_user_id_by_email,
    import_user_sources_catalog,
)


def test_normalize_cli_argv_backward_compat() -> None:
    from news_manager.cli import _normalize_cli_argv

    assert _normalize_cli_argv(["--from-db", "-v"]) == ["ingest", "--from-db", "-v"]
    assert _normalize_cli_argv(["ingest", "--from-db"]) == ["ingest", "--from-db"]
    assert _normalize_cli_argv(["user-sources", "export", "--email", "a@b.c"]) == [
        "user-sources",
        "export",
        "--email",
        "a@b.c",
    ]


@patch("news_manager.user_sources_catalog.fetch_sources_with_categories")
def test_export_user_sources_catalog_groups_and_sorts(mock_fetch: MagicMock) -> None:
    mock_fetch.return_value = [
        {
            "url": "https://z.com/",
            "use_rss": False,
            "category_id": "c2",
            "category_name": "Beta",
            "category_instruction": "inst-b",
        },
        {
            "url": "https://a.com/feed",
            "use_rss": True,
            "category_id": "c1",
            "category_name": "Alpha",
            "category_instruction": "inst-a",
        },
        {
            "url": "https://b.com/",
            "use_rss": False,
            "category_id": "c1",
            "category_name": "Alpha",
            "category_instruction": "inst-a",
        },
    ]
    client = MagicMock()
    out = export_user_sources_catalog(client, "user-uuid-1", email="u@example.com")
    assert out["schema_version"] == CATALOG_SCHEMA_VERSION
    assert out["user_id"] == "user-uuid-1"
    assert out["email"] == "u@example.com"
    cats = out["categories"]
    assert [c["category"] for c in cats] == ["Alpha", "Beta"]
    assert cats[0]["instruction"] == "inst-a"
    assert cats[0]["sources"] == [
        {"url": "https://a.com/feed", "use_rss": True},
        {"url": "https://b.com/", "use_rss": False},
    ]
    assert cats[1]["sources"] == [{"url": "https://z.com/", "use_rss": False}]


def test_import_user_sources_catalog_creates_category_and_sources() -> None:
    sources_t = MagicMock()
    load_exec = MagicMock()
    load_exec.execute.return_value = MagicMock(data=[])
    sources_t.select.return_value.eq.return_value = load_exec
    ins_exec = MagicMock()
    sources_t.insert.return_value.execute.return_value = ins_exec

    categories_t = MagicMock()
    cat_lookup_exec = MagicMock()
    cat_lookup_exec.execute.return_value = MagicMock(data=[])
    eq_name = MagicMock()
    eq_name.execute.return_value = MagicMock(data=[])
    eq_uid = MagicMock()
    eq_uid.eq.return_value = eq_name
    categories_t.select.return_value.eq.return_value = eq_uid

    cat_insert_exec = MagicMock()
    cat_insert_exec.execute.return_value = MagicMock(data=[{"id": "new-cat"}])
    categories_t.insert.return_value.select.return_value = cat_insert_exec

    def table(name: str) -> MagicMock:
        if name == "sources":
            return sources_t
        if name == "categories":
            return categories_t
        raise AssertionError(f"unexpected table {name}")

    client = MagicMock()
    client.table.side_effect = table

    payload = {
        "schema_version": 1,
        "categories": [
            {
                "category": "Tech",
                "instruction": "Summarize tech neutrally.",
                "sources": [{"url": "https://example.com/", "use_rss": False}],
            }
        ],
    }
    summary = import_user_sources_catalog(client, "user-1", payload)
    assert summary.categories_created == 1
    assert summary.categories_reused == 0
    assert summary.sources_inserted == 1
    assert summary.sources_skipped == 0

    categories_t.insert.assert_called_once()
    row = categories_t.insert.call_args[0][0]
    assert row["user_id"] == "user-1"
    assert row["name"] == "Tech"
    assert row["instruction"] == "Summarize tech neutrally."

    sources_t.insert.assert_called_once()
    srow = sources_t.insert.call_args[0][0]
    assert srow["user_id"] == "user-1"
    assert srow["category_id"] == "new-cat"
    assert srow["use_rss"] is False
    assert srow["url"] == "https://example.com/"


def test_import_user_sources_catalog_skips_existing_category_and_source() -> None:
    sources_t = MagicMock()
    load_exec = MagicMock()
    load_exec.execute.return_value = MagicMock(data=[{"url": "https://example.com/"}])
    sources_t.select.return_value.eq.return_value = load_exec

    categories_t = MagicMock()
    cat_lookup_exec = MagicMock()
    cat_lookup_exec.execute.return_value = MagicMock(data=[{"id": "existing-cat"}])
    eq_name = MagicMock()
    eq_name.execute.return_value = cat_lookup_exec
    eq_uid = MagicMock()
    eq_uid.eq.return_value = eq_name
    categories_t.select.return_value.eq.return_value = eq_uid

    def table(name: str) -> MagicMock:
        if name == "sources":
            return sources_t
        if name == "categories":
            return categories_t
        raise AssertionError(f"unexpected table {name}")

    client = MagicMock()
    client.table.side_effect = table

    payload = {
        "schema_version": 1,
        "categories": [
            {
                "category": "Tech",
                "instruction": "ignored when category exists",
                "sources": [{"url": "https://example.com/", "use_rss": True}],
            }
        ],
    }
    summary = import_user_sources_catalog(client, "user-1", payload)
    assert summary.categories_created == 0
    assert summary.categories_reused == 1
    assert summary.sources_inserted == 0
    assert summary.sources_skipped == 1
    categories_t.insert.assert_not_called()
    sources_t.insert.assert_not_called()


def test_import_user_sources_catalog_validation() -> None:
    client = MagicMock()
    with pytest.raises(ValueError, match="schema_version"):
        import_user_sources_catalog(client, "u", {"schema_version": 99, "categories": []})
    with pytest.raises(ValueError, match="sources"):
        import_user_sources_catalog(
            client,
            "u",
            {"schema_version": 1, "categories": [{"category": "X", "instruction": "", "sources": []}]},
        )


@patch("news_manager.user_sources_catalog.httpx.Client")
def test_fetch_user_id_by_email(mock_client_cls: MagicMock) -> None:
    http_inst = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = http_inst
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "users": [
            {"id": "11111111-1111-1111-1111-111111111111", "email": "Other@Example.com"},
        ]
    }
    http_inst.get.return_value = resp

    uid = fetch_user_id_by_email(
        supabase_url="https://proj.supabase.co",
        service_role_key="service-key",
        email="other@example.com",
    )
    assert uid == "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def jwt_secret() -> str:
    return "test-secret-for-jwt-verify-minimum-32-bytes!!"


@pytest.fixture
def authed_headers(jwt_secret: str) -> dict[str, str]:
    import time
    import os

    os.environ["SUPABASE_JWT_SECRET"] = jwt_secret
    token = jwt.encode(
        {
            "sub": "22222222-2222-2222-2222-222222222222",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
            "role": "authenticated",
        },
        jwt_secret,
        algorithm="HS256",
    )
    token_s = token.decode("ascii") if isinstance(token, bytes) else str(token)
    return {"Authorization": f"Bearer {token_s}"}


@patch("news_manager.resolve_app.import_user_sources_catalog")
@patch("news_manager.resolve_app.create_supabase_client")
@patch("news_manager.resolve_app.supabase_settings")
def test_user_sources_import_api_ok(
    mock_settings: MagicMock,
    mock_create_sb: MagicMock,
    mock_import: MagicMock,
    authed_headers: dict[str, str],
) -> None:
    mock_settings.return_value = ("https://x.supabase.co", "key")
    mock_import.return_value = ImportSummary(
        categories_created=0,
        categories_reused=1,
        sources_inserted=2,
        sources_skipped=0,
    )
    app = create_app()
    body = {"schema_version": 1, "categories": []}
    rv = app.test_client().post(
        "/api/user/sources/import",
        data=json.dumps(body),
        headers={**authed_headers, "Content-Type": "application/json"},
    )
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["ok"] is True
    assert data["summary"]["sources_inserted"] == 2
    mock_import.assert_called_once()


def test_user_sources_import_api_invalid_json(authed_headers: dict[str, str]) -> None:
    app = create_app()
    rv = app.test_client().post(
        "/api/user/sources/import",
        data="{",
        headers={**authed_headers, "Content-Type": "application/json"},
    )
    assert rv.status_code == 400
    assert rv.get_json()["ok"] is False
