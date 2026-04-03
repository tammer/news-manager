"""RSS/Atom feed parsing and RSS fetch path."""

from unittest.mock import MagicMock, patch

from news_manager.fetch import fetch_articles_for_source, parse_feed_entries

RSS_MINIMAL = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Hello</title><link>https://example.com/a/post</link>
<pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate></item>
</channel></rss>
"""


def test_parse_feed_entries_rss2() -> None:
    rows = parse_feed_entries(RSS_MINIMAL)
    assert len(rows) == 1
    assert rows[0][0] == "https://example.com/a/post"
    assert rows[0][1] is not None
    assert rows[0][2] == "Hello"


def test_parse_feed_entries_atom() -> None:
    atom = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Atom post</title>
    <link href="https://x.test/p/1" rel="alternate" type="text/html"/>
    <updated>2024-06-01T00:00:00Z</updated>
  </entry>
</feed>
"""
    rows = parse_feed_entries(atom)
    assert len(rows) >= 1
    assert "x.test" in rows[0][0]


@patch("news_manager.fetch.fetch_html")
@patch("news_manager.fetch.fetch_feed_xml")
def test_fetch_articles_for_source_rss_uses_feed_then_html(
    mock_feed: MagicMock,
    mock_html: MagicMock,
) -> None:
    mock_feed.return_value = RSS_MINIMAL
    article_html = (
        "<html><head><title>T</title></head><body><p>"
        + ("word " * 200)
        + "</p></body></html>"
    )
    mock_html.return_value = article_html

    out = fetch_articles_for_source(
        "https://example.com/feed",
        kind="rss",
        max_articles=5,
        timeout=5.0,
    )
    assert len(out) == 1
    assert out[0].url == "https://example.com/a/post"
    mock_feed.assert_called_once()
    mock_html.assert_called()
