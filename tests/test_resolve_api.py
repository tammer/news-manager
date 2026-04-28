"""Source resolve API: auth and pipeline (mocked)."""

import json
import os
import time
from unittest.mock import MagicMock, patch

import httpx
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


@patch("news_manager.source_resolve._probe_feed_paths")
@patch("news_manager.source_resolve._chat_json")
@patch("news_manager.source_resolve.fetch_html_limited")
@patch("news_manager.source_resolve._collect_candidates_from_query")
def test_resolve_source_success_rss_from_link(
    mock_collect: MagicMock,
    mock_fetch: MagicMock,
    mock_chat: MagicMock,
    mock_probe: MagicMock,
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
    ]
    mock_fetch.return_value = (
        '<html><head><title>Example News</title>'
        '<link rel="alternate" type="application/rss+xml" href="/feed.xml" />'
        "</head><body></body></html>",
        "https://example.com/",
    )
    mock_probe.return_value = []

    out = resolve_source("example news", max_results=5)
    assert out["ok"] is True
    assert out["use_rss"] is True
    assert out["rss_found"] is True
    assert out["resolved_url"] == "https://example.com/feed.xml"
    assert out["homepage_url"] == "https://example.com/"


@patch("news_manager.source_resolve._probe_feed_paths")
@patch("news_manager.source_resolve._chat_json")
@patch("news_manager.source_resolve.fetch_html_limited")
@patch("news_manager.source_resolve._collect_candidates_from_query")
def test_resolve_source_pasted_feed_url(
    mock_collect: MagicMock,
    mock_fetch: MagicMock,
    mock_chat: MagicMock,
    mock_probe: MagicMock,
) -> None:
    """Pasting an RSS/Atom URL should resolve to itself with use_rss true."""
    feed_url = "https://www.lrb.co.uk/feeds/rss"
    rss_body = '<?xml version="1.0"?><rss version="2.0"><channel><title>LRB</title></channel></rss>'
    mock_collect.return_value = [{"title": "", "href": feed_url, "body": "direct"}]
    mock_fetch.return_value = (rss_body, feed_url)
    mock_probe.return_value = []

    out = resolve_source(feed_url, max_results=5)
    assert out["ok"] is True
    assert out["use_rss"] is True
    assert out["rss_found"] is True
    assert out["resolved_url"] == feed_url
    assert out["homepage_url"] == feed_url
    mock_chat.assert_not_called()


@patch("news_manager.source_resolve._probe_feed_paths")
@patch("news_manager.source_resolve._chat_json")
@patch("news_manager.source_resolve.fetch_html_limited")
@patch("news_manager.source_resolve._collect_candidates_from_query")
def test_resolve_source_astronomy_news_prefers_news_tag_feed(
    mock_collect: MagicMock,
    mock_fetch: MagicMock,
    mock_chat: MagicMock,
    mock_probe: MagicMock,
) -> None:
    mock_collect.return_value = [
        {"title": "", "href": "https://www.astronomy.com/news", "body": "direct"},
    ]
    mock_chat.side_effect = []
    mock_fetch.return_value = (
        '<html><head><link rel="alternate" type="application/rss+xml" href="/tags/news/feed/" />'
        "</head><body></body></html>",
        "https://www.astronomy.com/tags/news/",
    )
    mock_probe.return_value = []

    out = resolve_source("https://www.astronomy.com/news", max_results=5)
    assert out["ok"] is True
    assert out["use_rss"] is True
    assert out["rss_found"] is True
    assert out["resolved_url"] == "https://www.astronomy.com/tags/news/feed/"
    assert out["homepage_url"] == "https://www.astronomy.com/tags/news/"
    mock_chat.assert_not_called()


@patch("news_manager.source_resolve._probe_feed_paths")
@patch("news_manager.source_resolve._chat_json")
@patch("news_manager.source_resolve.fetch_html_limited")
@patch("news_manager.source_resolve._collect_candidates_from_query")
def test_resolve_source_section_url_rejects_site_wide_rss(
    mock_collect: MagicMock,
    mock_fetch: MagicMock,
    mock_chat: MagicMock,
    mock_probe: MagicMock,
) -> None:
    """Topic hub must not resolve to root /index.rss linked from page chrome."""
    mock_collect.return_value = [
        {"title": "", "href": "https://apnews.com/hub/book-reviews", "body": "direct"},
    ]
    mock_chat.side_effect = [{"is_article_listing": True, "reason": ""}]
    mock_fetch.return_value = (
        '<html><head><link rel="alternate" type="application/rss+xml" href="/index.rss" />'
        "</head><body></body></html>",
        "https://apnews.com/hub/book-reviews",
    )
    mock_probe.return_value = []

    out = resolve_source("https://apnews.com/hub/book-reviews", max_results=5)
    assert out["ok"] is True
    assert out["use_rss"] is False
    assert out["rss_found"] is True
    assert out["resolved_url"] == "https://apnews.com/hub/book-reviews"
    assert "site-wide" in out["notes"].lower()


@patch("news_manager.source_resolve._llm_pick_homepage")
@patch("news_manager.source_resolve._probe_feed_paths")
@patch("news_manager.source_resolve._chat_json")
@patch("news_manager.source_resolve.fetch_html_limited")
@patch("news_manager.source_resolve._collect_candidates_from_query")
def test_direct_pasted_url_skips_homepage_llm(
    mock_collect: MagicMock,
    mock_fetch: MagicMock,
    mock_chat: MagicMock,
    mock_probe: MagicMock,
    mock_llm_pick: MagicMock,
) -> None:
    """Regression: LLM 'canonical homepage' must not rewrite /hub/... to site root."""
    mock_collect.return_value = [
        {"title": "", "href": "https://apnews.com/hub/book-reviews", "body": "direct"},
    ]
    mock_chat.side_effect = [{"is_article_listing": True, "reason": ""}]
    mock_fetch.return_value = ("<html><title>H</title></html>", "https://apnews.com/hub/book-reviews")
    mock_probe.return_value = []

    resolve_source("https://apnews.com/hub/book-reviews", max_results=5)

    mock_llm_pick.assert_not_called()


@patch("news_manager.source_resolve._probe_feed_paths")
@patch("news_manager.source_resolve._chat_json")
@patch("news_manager.source_resolve.fetch_html_limited")
@patch("news_manager.source_resolve._collect_candidates_from_query")
def test_resolve_source_section_scoped_rss_still_preferred(
    mock_collect: MagicMock,
    mock_fetch: MagicMock,
    mock_chat: MagicMock,
    mock_probe: MagicMock,
) -> None:
    mock_collect.return_value = [
        {"title": "", "href": "https://example.com/hub/books", "body": "direct"},
    ]
    mock_chat.side_effect = []
    mock_fetch.return_value = (
        '<html><head><link rel="alternate" type="application/rss+xml" href="/hub/books/feed.xml" />'
        "</head><body></body></html>",
        "https://example.com/hub/books",
    )
    mock_probe.return_value = []

    out = resolve_source("https://example.com/hub/books", max_results=5)
    assert out["ok"] is True
    assert out["use_rss"] is True
    assert out["resolved_url"] == "https://example.com/hub/books/feed.xml"


@patch("news_manager.source_resolve._probe_feed_paths")
@patch("news_manager.source_resolve._chat_json")
@patch("news_manager.source_resolve.fetch_html_limited")
@patch("news_manager.source_resolve._collect_candidates_from_query")
def test_resolve_source_not_a_listing(
    mock_collect: MagicMock,
    mock_fetch: MagicMock,
    mock_chat: MagicMock,
    mock_probe: MagicMock,
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
    mock_probe.return_value = []

    out = resolve_source("x")
    assert out["ok"] is False
    assert out["error"] == "not_a_listing"


@patch("news_manager.source_resolve._probe_feed_paths")
@patch("news_manager.source_resolve._chat_json")
@patch("news_manager.source_resolve.fetch_html_limited")
@patch("news_manager.source_resolve._collect_candidates_from_query")
def test_resolve_source_feed_probe_skips_subscribe_homepage(
    mock_collect: MagicMock,
    mock_fetch: MagicMock,
    mock_chat: MagicMock,
    mock_probe: MagicMock,
) -> None:
    """Valid /feed succeeds even when HTML looks like subscribe-only (no listing LLM)."""
    mock_collect.return_value = [
        {"title": "Sub", "href": "https://letters.example.com/", "body": ""},
    ]
    mock_chat.side_effect = [
        {
            "homepage_url": "https://letters.example.com/",
            "website_title": "Letters",
            "confidence": "high",
            "notes": "",
        },
    ]
    mock_fetch.return_value = (
        "<html><title>Subscribe to our newsletter</title><body>No posts here</body></html>",
        "https://letters.example.com/",
    )
    mock_probe.return_value = ["https://letters.example.com/feed"]

    out = resolve_source("letters example")
    assert out["ok"] is True
    assert out["use_rss"] is True
    assert out["rss_found"] is True
    assert out["resolved_url"] == "https://letters.example.com/feed"
    assert mock_chat.call_count == 1


def test_flask_resolve_401_without_token(jwt_secret: str) -> None:
    os.environ["SUPABASE_JWT_SECRET"] = jwt_secret
    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.post("/api/sources/resolve", json={"query": "test"})
    assert r.status_code == 401


def test_cors_allows_gistprism_origin_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESOLVE_CORS_ORIGIN", raising=False)
    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.options(
        "/api/sources/resolve",
        headers={"Origin": "https://gistprism.tammer.com"},
    )
    assert r.status_code == 204
    assert r.headers.get("Access-Control-Allow-Origin") == "https://gistprism.tammer.com"
    assert r.headers.get("Access-Control-Allow-Methods") == "POST, GET, OPTIONS"


def test_cors_keeps_default_origins_when_custom_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESOLVE_CORS_ORIGIN", "https://custom.example.com")
    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.options(
        "/api/sources/resolve",
        headers={"Origin": "https://gistprism.tammer.com"},
    )
    assert r.status_code == 204
    assert r.headers.get("Access-Control-Allow-Origin") == "https://gistprism.tammer.com"


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


@patch("news_manager.source_resolve._probe_feed_paths")
@patch("news_manager.source_resolve._chat_json")
@patch("news_manager.source_resolve.fetch_html_limited")
@patch("news_manager.source_resolve._collect_candidates_from_query")
def test_flask_resolve_200_mocked(
    mock_collect: MagicMock,
    mock_fetch: MagicMock,
    mock_chat: MagicMock,
    mock_probe: MagicMock,
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
    mock_probe.return_value = []

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


def test_fetch_html_limited_uses_scrapingdog_on_configured_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from news_manager.source_resolve import fetch_html_limited

    monkeypatch.setenv("SCRAPINGDOG_ENABLED", "true")
    monkeypatch.setenv("SCRAPINGDOG_API_KEY", "sd-key")
    req = httpx.Request("GET", "https://example.com/")
    direct = httpx.Response(
        403,
        request=req,
        headers={"content-type": "text/html"},
        text="blocked",
    )
    fallback_resp = httpx.Response(
        200,
        request=httpx.Request("GET", "https://api.scrapingdog.com/scrape"),
        text="<html><title>Fallback</title></html>",
    )
    with patch("news_manager.source_resolve.httpx.Client") as mock_client_cls:
        cm = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = cm
        stream_cm = MagicMock()
        stream_cm.__enter__.return_value = direct
        cm.stream.return_value = stream_cm
        with patch("news_manager.source_resolve.httpx.get", return_value=fallback_resp) as mock_sd:
            html, final_url, err = fetch_html_limited("https://example.com/")
    assert err is None
    assert html is not None and "Fallback" in html
    assert final_url == "https://example.com/"
    mock_sd.assert_called_once()


def test_fetch_html_limited_no_scrapingdog_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from news_manager.source_resolve import fetch_html_limited

    monkeypatch.delenv("SCRAPINGDOG_ENABLED", raising=False)
    monkeypatch.setenv("SCRAPINGDOG_API_KEY", "sd-key")
    req = httpx.Request("GET", "https://example.com/")
    direct = httpx.Response(
        403,
        request=req,
        headers={"content-type": "text/html"},
        text="blocked",
    )
    with patch("news_manager.source_resolve.httpx.Client") as mock_client_cls:
        cm = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = cm
        stream_cm = MagicMock()
        stream_cm.__enter__.return_value = direct
        cm.stream.return_value = stream_cm
        with patch("news_manager.source_resolve.httpx.get") as mock_sd:
            html, final_url, err = fetch_html_limited("https://example.com/")
    assert html is None
    assert final_url is None
    assert isinstance(err, dict)
    assert err.get("reason") == "http_403"
    mock_sd.assert_not_called()
