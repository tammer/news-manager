"""Pipeline orchestration with mocks."""

from unittest.mock import MagicMock, patch

from news_manager.fetch import normalize_url
from news_manager.models import OutputArticle, RawArticle, Source, SourceCategory
from news_manager.pipeline import run_pipeline, run_pipeline_from_db
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


@patch("news_manager.pipeline.upsert_included_article_v2")
@patch("news_manager.pipeline.filter_and_summarize_outcome")
@patch("news_manager.pipeline.fetch_single_raw_article")
@patch("news_manager.pipeline.discover_article_targets")
@patch("news_manager.pipeline.prefetch_processed_urls_v2")
@patch("news_manager.pipeline.fetch_sources_with_categories")
@patch("news_manager.pipeline.fetch_user_instructions")
@patch("news_manager.pipeline.list_user_ids_with_sources")
def test_run_pipeline_from_db_combines_instructions_and_upserts_v2(
    mock_list_users: MagicMock,
    mock_global: MagicMock,
    mock_sources: MagicMock,
    mock_prefetch: MagicMock,
    mock_discover: MagicMock,
    mock_fetch_one: MagicMock,
    mock_outcome: MagicMock,
    mock_upsert_v2: MagicMock,
) -> None:
    mock_list_users.return_value = ["user-1"]
    mock_global.return_value = "global body"
    mock_sources.return_value = [
        {
            "url": "https://a.com",
            "use_rss": False,
            "category_id": "cid-1",
            "category_name": "News",
            "instruction": "per source",
        }
    ]
    mock_prefetch.return_value = (set(), set())
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
    mock_upsert_v2.return_value = None

    sb = MagicMock()
    out = run_pipeline_from_db(supabase_client=sb, max_articles=5, http_timeout=1.0)
    assert len(out) == 1
    assert out[0].user_id == "user-1"
    assert len(out[0].categories) == 1
    assert out[0].categories[0].category == "News"
    assert len(out[0].categories[0].articles) == 1
    mock_upsert_v2.assert_called_once()
    assert mock_upsert_v2.call_args[0][1] == "user-1"
    assert mock_upsert_v2.call_args[0][2] == "cid-1"
    inst = mock_outcome.call_args.kwargs["instructions"]
    assert "global body" in inst
    assert "per source" in inst
