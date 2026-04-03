"""Fetch and link extraction tests."""

from unittest.mock import MagicMock, patch

from news_manager.fetch import extract_article_urls, fetch_articles_for_source


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
