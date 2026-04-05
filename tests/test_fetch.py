"""Fetch and link extraction tests."""

import httpx
from unittest.mock import MagicMock, patch

from news_manager.fetch import extract_article_urls, fetch_articles_for_source, fetch_html


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
def test_fetch_articles_for_source_respects_cap(mock_fetch: MagicMock) -> None:
    """Returns up to max_articles when HTML repeats article links."""
    article_html = "<html><title>T</title><body><p>" + ("word " * 200) + "</p></body></html>"
    home_html = """
    <html><body>
    <a href="/a/1">one</a>
    <a href="/a/2">two</a>
    <a href="/a/3">three</a>
    </body></html>
    """

    def side_effect(client: object, url: str) -> str | None:
        if url.rstrip("/") == "https://example.com":
            return home_html
        return article_html

    mock_fetch.side_effect = side_effect
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
    assert mock_sleep.call_args_list[0][0][0] == 3.0
    assert mock_sleep.call_args_list[1][0][0] == 6.0


@patch("news_manager.fetch.time.sleep")
def test_fetch_html_stops_after_max_429_attempts(mock_sleep: MagicMock) -> None:
    req = httpx.Request("GET", "https://example.com/c")
    r429 = httpx.Response(429, request=req, headers={})
    client = MagicMock()
    client.get.return_value = r429
    out = fetch_html(client, "https://example.com/c")
    assert out is None
    assert client.get.call_count == 4
