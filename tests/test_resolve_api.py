"""Source resolve API: auth and pipeline (mocked)."""

import json
import os
import time
from unittest.mock import MagicMock, patch

import jwt
import pytest

from news_manager.resolve_app import create_app
from news_manager.source_resolve import resolve_source, resolve_source_json_body, url_fetch_allowed


@pytest.fixture
def jwt_secret() -> str:
    return "test-secret-for-jwt-verify-minimum-32-bytes!!"


@pytest.fixture
def authed_headers(jwt_secret: str) -> dict[str, str]:
    os.environ["SUPABASE_JWT_SECRET"] = jwt_secret
    token = jwt.encode(
        {
            "sub": "11111111-1111-1111-1111-111111111111",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
            "role": "authenticated",
        },
        jwt_secret,
        algorithm="HS256",
    )
    if isinstance(token, bytes):
        token_s = token.decode("ascii")
    else:
        token_s = str(token)
    return {"Authorization": f"Bearer {token_s}"}


def test_url_fetch_allowed_blocks_private() -> None:
    assert url_fetch_allowed("https://example.com/") is True
    assert url_fetch_allowed("file:///etc/passwd") is False
    assert url_fetch_allowed("http://127.0.0.1/") is False
    assert url_fetch_allowed("https://192.168.1.1/") is False


def test_resolve_source_json_body_validation() -> None:
    bad, st = resolve_source_json_body(b"not json")
    assert st == 400
    assert bad["ok"] is False

    bad2, st2 = resolve_source_json_body(json.dumps({}).encode())
    assert st2 == 400
    assert "query" in bad2["message"].lower()


@patch("news_manager.source_resolve._chat_json")
@patch("news_manager.source_resolve.fetch_html_limited")
@patch("news_manager.source_resolve._collect_candidates_from_query")
def test_resolve_source_success_rss_from_link(
    mock_collect: MagicMock,
    mock_fetch: MagicMock,
    mock_chat: MagicMock,
) -> None:
    mock_collect.return_value = [
        {
            "title": "Example News",
            "href": "https://example.com/",
            "body": "News site",
        }
    ]
    mock_chat.side_effect = [
        {
            "homepage_url": "https://example.com/",
            "website_title": "Example News",
            "confidence": "high",
            "notes": "",
        },
        {"is_article_listing": True, "reason": "index"},
    ]
    mock_fetch.return_value = (
        '<html><head><title>Example News</title>'
        '<link rel="alternate" type="application/rss+xml" href="/feed.xml" />'
        "</head><body></body></html>",
        "https://example.com/",
    )

    out = resolve_source("example news", max_results=5)
    assert out["ok"] is True
    assert out["use_rss"] is True
    assert out["rss_found"] is True
    assert out["resolved_url"] == "https://example.com/feed.xml"
    assert out["homepage_url"] == "https://example.com/"


@patch("news_manager.source_resolve._chat_json")
@patch("news_manager.source_resolve.fetch_html_limited")
@patch("news_manager.source_resolve._collect_candidates_from_query")
def test_resolve_source_not_a_listing(
    mock_collect: MagicMock,
    mock_fetch: MagicMock,
    mock_chat: MagicMock,
) -> None:
    mock_collect.return_value = [{"title": "X", "href": "https://example.com/a", "body": ""}]
    mock_chat.side_effect = [
        {
            "homepage_url": "https://example.com/a",
            "website_title": "X",
            "confidence": "high",
            "notes": "",
        },
        {"is_article_listing": False, "reason": "single article"},
    ]
    mock_fetch.return_value = ("<html><title>One</title></html>", "https://example.com/a")

    out = resolve_source("x")
    assert out["ok"] is False
    assert out["error"] == "not_a_listing"


def test_flask_resolve_401_without_token(jwt_secret: str) -> None:
    os.environ["SUPABASE_JWT_SECRET"] = jwt_secret
    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.post("/api/sources/resolve", json={"query": "test"})
    assert r.status_code == 401


def test_flask_resolve_401_bad_token(jwt_secret: str) -> None:
    os.environ["SUPABASE_JWT_SECRET"] = jwt_secret
    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.post(
        "/api/sources/resolve",
        json={"query": "test"},
        headers={"Authorization": "Bearer not-a-valid-token"},
    )
    assert r.status_code == 401


@patch("news_manager.source_resolve._chat_json")
@patch("news_manager.source_resolve.fetch_html_limited")
@patch("news_manager.source_resolve._collect_candidates_from_query")
def test_flask_resolve_200_mocked(
    mock_collect: MagicMock,
    mock_fetch: MagicMock,
    mock_chat: MagicMock,
    jwt_secret: str,
    authed_headers: dict[str, str],
) -> None:
    os.environ["SUPABASE_JWT_SECRET"] = jwt_secret
    mock_collect.return_value = [{"title": "X", "href": "https://example.com/", "body": ""}]
    mock_chat.side_effect = [
        {
            "homepage_url": "https://example.com/",
            "website_title": "X",
            "confidence": "high",
            "notes": "",
        },
        {"is_article_listing": True, "reason": ""},
    ]
    mock_fetch.return_value = ("<html><title>X</title></html>", "https://example.com/")

    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.post(
        "/api/sources/resolve",
        json={"query": "example"},
        headers=authed_headers,
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["use_rss"] is False
    assert data["rss_found"] is False
