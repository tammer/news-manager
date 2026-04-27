"""Fetch and link extraction tests."""

import httpx
import pytest
from unittest.mock import MagicMock, patch

from news_manager.fetch import (
    discover_article_targets,
    extract_article_link_candidates,
    extract_article_urls,
    extract_sitemap_http_urls,
    fetch_articles_for_source,
    fetch_html,
    fetch_listing_body,
)


SITEMAP_NEWS = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/news/good-story</loc></url>
  <url><loc>https://example.com/tag/bad</loc></url>
  <url><loc>https://evil.com/other</loc></url>
</urlset>
"""


def test_extract_sitemap_http_urls_same_site_and_path_rules() -> None:
    urls = extract_sitemap_http_urls(SITEMAP_NEWS, "https://example.com/")
    assert urls == ["https://example.com/news/good-story"]


def test_discover_article_targets_auto_sitemap(monkeypatch: pytest.MonkeyPatch) -> None:
    import news_manager.fetch as fetch_mod

    def fake_listing(_client: object, url: str) -> str | None:
        assert "example.com" in url
        return SITEMAP_NEWS

    monkeypatch.setattr(fetch_mod, "fetch_listing_body", fake_listing)
    client = MagicMock()
    out = discover_article_targets(client, "https://example.com/", force_feed_xml=False)
    assert [t[0] for t in out] == ["https://example.com/news/good-story"]


def test_discover_article_targets_force_feed_sitemap(monkeypatch: pytest.MonkeyPatch) -> None:
    import news_manager.fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "fetch_listing_body", lambda _c, _u: SITEMAP_NEWS)
    client = MagicMock()
    out = discover_article_targets(client, "https://example.com/", force_feed_xml=True)
    assert [t[0] for t in out] == ["https://example.com/news/good-story"]


def test_extract_article_link_candidates_first_anchor_wins() -> None:
    html = """
    <html><body>
    <a href="/story">First</a>
    <a href="/story">Second ignored</a>
    <a href="/other">Other</a>
    </body></html>
    """
    home = "https://www.example.com/"
    cands = extract_article_link_candidates(html, home)
    urls = [u for u, _ in cands]
    assert urls.count("https://www.example.com/story") == 1
    by_url = dict(cands)
    assert by_url["https://www.example.com/story"] == "First"
    assert by_url["https://www.example.com/other"] == "Other"


def test_extract_article_urls_same_site() -> None:
    html = """
    <html><body>
    <a href="/2024/01/15/story">Story</a>
    <a href="https://evil.com/x">External</a>
    <a href="/tag/foo">Tag</a>
    </body></html>
    """
    home = "https://www.example.com/news/"
    urls = extract_article_urls(html, home)
    assert any("/2024/01/15/story" in u for u in urls)
    assert not any("evil.com" in u for u in urls)
    assert not any("/tag/" in u for u in urls)


@patch("news_manager.fetch.fetch_html")
@patch("news_manager.fetch.fetch_listing_body")
def test_fetch_articles_for_source_respects_cap(
    mock_listing: MagicMock,
    mock_fetch: MagicMock,
) -> None:
    """Returns up to max_articles when HTML repeats article links."""
    article_html = "<html><title>T</title><body><p>" + ("word " * 200) + "</p></body></html>"
    home_html = """
    <html><body>
    <a href="/a/1">one</a>
    <a href="/a/2">two</a>
    <a href="/a/3">three</a>
    </body></html>
    """

    mock_listing.return_value = home_html
    mock_fetch.return_value = article_html
    out = fetch_articles_for_source("example.com", max_articles=2, timeout=5.0)
    assert len(out) == 2


@patch("news_manager.fetch.time.sleep")
def test_fetch_html_retries_429_uses_retry_after(mock_sleep: MagicMock) -> None:
    req = httpx.Request("GET", "https://example.com/a")
    r429 = httpx.Response(429, request=req, headers={"Retry-After": "2"})
    ok_html = "<html><body>x</body></html>"
    r200 = httpx.Response(
        200,
        request=req,
        headers={"content-type": "text/html"},
        text=ok_html,
    )
    client = MagicMock()
    client.get.side_effect = [r429, r200]
    out = fetch_html(client, "https://example.com/a")
    assert out == ok_html
    assert client.get.call_count == 2
    mock_sleep.assert_called_once_with(2.0)


@patch("news_manager.fetch.time.sleep")
def test_fetch_html_429_fallback_backoff(mock_sleep: MagicMock) -> None:
    req = httpx.Request("GET", "https://example.com/b")
    r429 = httpx.Response(429, request=req, headers={})
    ok_html = "<html><body>y</body></html>"
    r200 = httpx.Response(
        200,
        request=req,
        headers={"content-type": "text/html"},
        text=ok_html,
    )
    client = MagicMock()
    client.get.side_effect = [r429, r429, r200]
    out = fetch_html(client, "https://example.com/b")
    assert out == ok_html
    assert client.get.call_count == 3
    assert mock_sleep.call_args_list[0][0][0] == 20.0
    assert mock_sleep.call_args_list[1][0][0] == 40.0


@patch("news_manager.html_discovery_llm.select_article_urls_with_llm")
@patch("news_manager.fetch.fetch_listing_body")
def test_discover_article_targets_html_llm_uses_model_order(
    mock_listing_body: MagicMock,
    mock_llm_pick: MagicMock,
) -> None:
    home_html = """
    <html><body>
    <a href="/a/1">One</a>
    <a href="/a/2">Two</a>
    </body></html>
    """
    mock_listing_body.return_value = home_html
    mock_llm_pick.return_value = ["https://example.com/a/2", "https://example.com/a/1"]
    client = MagicMock()
    out = discover_article_targets(
        client, "https://example.com", use_llm_for_html=True, force_feed_xml=False
    )
    assert out == [
        ("https://example.com/a/2", None, None),
        ("https://example.com/a/1", None, None),
    ]
    mock_llm_pick.assert_called_once()


@patch("news_manager.html_discovery_llm.select_article_urls_with_llm")
@patch("news_manager.fetch.fetch_listing_body")
def test_discover_article_targets_html_llm_falls_back_when_empty(
    mock_listing_body: MagicMock,
    mock_llm_pick: MagicMock,
) -> None:
    home_html = """<html><body><a href="/z/short">S</a><a href="/longer/path/here">L</a></body></html>"""
    mock_listing_body.return_value = home_html
    mock_llm_pick.return_value = []
    client = MagicMock()
    out = discover_article_targets(
        client, "https://example.com", use_llm_for_html=True, force_feed_xml=False
    )
    # Heuristic: longer path first
    assert [t[0] for t in out] == [
        "https://example.com/longer/path/here",
        "https://example.com/z/short",
    ]


@patch("news_manager.fetch.time.sleep")
def test_fetch_html_stops_after_max_429_attempts(mock_sleep: MagicMock) -> None:
    req = httpx.Request("GET", "https://example.com/c")
    r429 = httpx.Response(429, request=req, headers={})
    client = MagicMock()
    client.get.return_value = r429
    out = fetch_html(client, "https://example.com/c")
    assert out is None
    assert client.get.call_count == 4


def test_fetch_html_uses_scrapingdog_fallback_on_configured_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCRAPINGDOG_ENABLED", "true")
    monkeypatch.setenv("SCRAPINGDOG_API_KEY", "sd-key")
    req = httpx.Request("GET", "https://example.com/protected")
    direct = httpx.Response(403, request=req, headers={"content-type": "text/html"}, text="")
    client = MagicMock()
    client.get.return_value = direct
    fallback_resp = httpx.Response(
        200,
        request=httpx.Request("GET", "https://api.scrapingdog.com/scrape"),
        text="<html><body>from fallback</body></html>",
    )
    with patch("news_manager.fetch.httpx.get", return_value=fallback_resp) as mock_sd:
        out = fetch_html(client, "https://example.com/protected")
    assert out == "<html><body>from fallback</body></html>"
    mock_sd.assert_called_once()


def test_fetch_html_does_not_use_scrapingdog_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCRAPINGDOG_ENABLED", raising=False)
    monkeypatch.setenv("SCRAPINGDOG_API_KEY", "sd-key")
    req = httpx.Request("GET", "https://example.com/protected")
    direct = httpx.Response(403, request=req, headers={"content-type": "text/html"}, text="")
    client = MagicMock()
    client.get.return_value = direct
    with patch("news_manager.fetch.httpx.get") as mock_sd:
        out = fetch_html(client, "https://example.com/protected")
    assert out is None
    mock_sd.assert_not_called()


def test_fetch_listing_body_uses_scrapingdog_for_unclassified_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCRAPINGDOG_ENABLED", "true")
    monkeypatch.setenv("SCRAPINGDOG_API_KEY", "sd-key")
    req = httpx.Request("GET", "https://example.com/")
    direct = httpx.Response(
        200,
        request=req,
        headers={"content-type": "application/octet-stream"},
        text="binary-looking-body",
    )
    client = MagicMock()
    client.get.return_value = direct
    fallback_resp = httpx.Response(
        200,
        request=httpx.Request("GET", "https://api.scrapingdog.com/scrape"),
        text="<html><body>listing page</body></html>",
    )
    with patch("news_manager.fetch.httpx.get", return_value=fallback_resp) as mock_sd:
        out = fetch_listing_body(client, "https://example.com/")
    assert out == "<html><body>listing page</body></html>"
    mock_sd.assert_called_once()


def test_fetch_listing_body_no_fallback_on_non_configured_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCRAPINGDOG_ENABLED", "true")
    monkeypatch.setenv("SCRAPINGDOG_API_KEY", "sd-key")
    monkeypatch.setenv("SCRAPINGDOG_FALLBACK_ON", "403,429,500")
    req = httpx.Request("GET", "https://example.com/")
    direct = httpx.Response(404, request=req, text="not found")
    client = MagicMock()
    client.get.return_value = direct
    with patch("news_manager.fetch.httpx.get") as mock_sd:
        out = fetch_listing_body(client, "https://example.com/")
    assert out is None
    mock_sd.assert_not_called()
