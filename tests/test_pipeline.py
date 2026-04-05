"""Pipeline orchestration with mocks."""

from unittest.mock import MagicMock, patch

from news_manager.fetch import normalize_url
from news_manager.models import OutputArticle, RawArticle, Source, SourceCategory
from news_manager.pipeline import run_pipeline
from news_manager.summarize import SummarizeOutcome


def _mock_supabase_client(
    *,
    news_urls: tuple[str, ...] = (),
    excl_urls: tuple[str, ...] = (),
) -> MagicMock:
    """Prefetch returns normalized URLs; upsert chains succeed by default."""
    client = MagicMock()

    def table(_name: str) -> MagicMock:
        t = MagicMock()
        sel = MagicMock()
        urls = news_urls if _name == "news_articles" else excl_urls
        sel.execute.return_value = MagicMock(data=[{"url": u} for u in urls])
        t.select.return_value.eq.return_value = sel
        up = MagicMock()
        up.execute.return_value = MagicMock()
        t.upsert.return_value = up
        return t

    client.table.side_effect = table
    return client


@patch("news_manager.pipeline.filter_and_summarize_outcome")
@patch("news_manager.pipeline.fetch_single_raw_article")
@patch("news_manager.pipeline.discover_article_targets")
def test_run_pipeline_keeps_empty_category(
    mock_discover: MagicMock,
    mock_fetch_one: MagicMock,
    mock_outcome: MagicMock,
) -> None:
    mock_discover.return_value = [("https://u", None, None)]
    mock_fetch_one.return_value = RawArticle(
        title="T", date=None, content="c", url="https://u"
    )
    mock_outcome.return_value = SummarizeOutcome(output=None, outcome="excluded")

    cats = [
        SourceCategory(category="News", sources=[Source(url="a.com", filter=True)]),
        SourceCategory(category="Science", sources=[Source(url="b.com", filter=True)]),
    ]
    sb = _mock_supabase_client()
    out = run_pipeline(
        cats,
        instructions="x",
        supabase_client=sb,
        max_articles=5,
        http_timeout=1.0,
    )
    assert len(out) == 2
    assert out[0].category == "News"
    assert out[0].articles == []
    assert out[1].category == "Science"
    assert out[1].articles == []


@patch("news_manager.pipeline.filter_and_summarize_outcome")
@patch("news_manager.pipeline.fetch_single_raw_article")
@patch("news_manager.pipeline.discover_article_targets")
def test_run_pipeline_includes_summarized(
    mock_discover: MagicMock,
    mock_fetch_one: MagicMock,
    mock_outcome: MagicMock,
) -> None:
    mock_discover.return_value = [("https://u", None, None)]
    mock_fetch_one.return_value = RawArticle(
        title="T", date=None, content="c", url="https://u"
    )
    mock_outcome.return_value = SummarizeOutcome(
        output=OutputArticle(
            title="T",
            date=None,
            content="c",
            url="https://u",
            short_summary="s",
            full_summary="f",
            source="a.com",
        ),
        outcome="included",
    )

    cats = [SourceCategory(category="News", sources=[Source(url="a.com", filter=True)])]
    sb = _mock_supabase_client()
    out = run_pipeline(
        cats,
        instructions="x",
        supabase_client=sb,
        max_articles=5,
        http_timeout=1.0,
    )
    assert len(out[0].articles) == 1
    assert out[0].articles[0].short_summary == "s"
    assert out[0].articles[0].source == "a.com"
    assert mock_outcome.call_args.kwargs["apply_filter"] is True
    assert mock_outcome.call_args.kwargs["source"] == "a.com"


@patch("news_manager.pipeline.filter_and_summarize_outcome")
@patch("news_manager.pipeline.fetch_single_raw_article")
@patch("news_manager.pipeline.discover_article_targets")
def test_run_pipeline_dedupes_same_url_across_sources(
    mock_discover: MagicMock,
    mock_fetch_one: MagicMock,
    mock_outcome: MagicMock,
) -> None:
    target = ("https://shared.example/a", None, None)
    mock_discover.side_effect = [[target], [target]]
    mock_fetch_one.return_value = RawArticle(
        title="T", date=None, content="c", url="https://shared.example/a"
    )
    mock_outcome.return_value = SummarizeOutcome(
        output=OutputArticle(
            title="T",
            date=None,
            content="c",
            url="https://shared.example/a",
            short_summary="s",
            full_summary="f",
            source="a.com",
        ),
        outcome="included",
    )
    cats = [
        SourceCategory(
            category="News",
            sources=[
                Source(url="https://a.com/feed", kind="rss", filter=True),
                Source(url="https://b.com/feed", kind="rss", filter=True),
            ],
        )
    ]
    sb = _mock_supabase_client()
    out = run_pipeline(
        cats,
        instructions="x",
        supabase_client=sb,
        max_articles=5,
        http_timeout=1.0,
    )
    assert len(out[0].articles) == 1
    assert out[0].articles[0].source == "a.com"
    assert mock_fetch_one.call_count == 1
    assert mock_outcome.call_count == 1


@patch("news_manager.pipeline.filter_and_summarize_outcome")
@patch("news_manager.pipeline.fetch_single_raw_article")
@patch("news_manager.pipeline.discover_article_targets")
def test_run_pipeline_apply_filter_false(
    mock_discover: MagicMock,
    mock_fetch_one: MagicMock,
    mock_outcome: MagicMock,
) -> None:
    mock_discover.return_value = [("https://u", None, None)]
    mock_fetch_one.return_value = RawArticle(
        title="T", date=None, content="c", url="https://u"
    )
    mock_outcome.return_value = SummarizeOutcome(
        output=OutputArticle(
            title="T",
            date=None,
            content="c",
            url="https://u",
            short_summary="s",
            full_summary="f",
            source="a.com",
        ),
        outcome="included",
    )
    cats = [
        SourceCategory(
            category="News",
            sources=[Source(url="a.com", filter=False)],
        )
    ]
    sb = _mock_supabase_client()
    run_pipeline(
        cats,
        instructions="x",
        supabase_client=sb,
        max_articles=5,
        http_timeout=1.0,
    )
    assert mock_outcome.call_args.kwargs["apply_filter"] is False
    assert mock_outcome.call_args.kwargs["source"] == "a.com"


@patch("news_manager.pipeline.filter_and_summarize_outcome")
@patch("news_manager.pipeline.fetch_single_raw_article")
@patch("news_manager.pipeline.discover_article_targets")
def test_run_pipeline_skips_url_already_in_news_articles(
    mock_discover: MagicMock,
    mock_fetch: MagicMock,
    mock_outcome: MagicMock,
) -> None:
    url = "https://example.com/post/1"
    mock_discover.return_value = [(url, None, "Post title")]
    mock_fetch.return_value = RawArticle(
        title="T",
        date=None,
        content="c " * 200,
        url=url,
    )
    mock_outcome.return_value = SummarizeOutcome(
        output=OutputArticle(
            title="T",
            date=None,
            content="c",
            url=url,
            short_summary="s",
            full_summary="f",
            source="feed",
        ),
        outcome="included",
    )
    cats = [
        SourceCategory(
            category="News",
            sources=[Source(url="https://feed", kind="rss", filter=True)],
        )
    ]
    sb = _mock_supabase_client(news_urls=(normalize_url(url),))
    run_pipeline(
        cats,
        instructions="same",
        supabase_client=sb,
        max_articles=5,
        http_timeout=1.0,
    )
    assert mock_fetch.call_count == 0
    assert mock_outcome.call_count == 0
