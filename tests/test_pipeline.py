"""DB-backed pipeline orchestration with mocks."""

from unittest.mock import MagicMock, patch

from news_manager.models import OutputArticle, RawArticle
from news_manager.pipeline import run_pipeline_from_db
from news_manager.summarize import SummarizeOutcome


@patch("news_manager.pipeline.upsert_included_article_v2")
@patch("news_manager.pipeline.filter_and_summarize_outcome")
@patch("news_manager.pipeline.fetch_single_raw_article")
@patch("news_manager.pipeline.discover_article_targets")
@patch("news_manager.pipeline.prefetch_processed_urls_v2")
@patch("news_manager.pipeline.fetch_sources_with_categories")
@patch("news_manager.pipeline.list_user_ids_with_sources")
def test_run_pipeline_from_db_resolves_instructions_and_upserts_v2(
    mock_list_users: MagicMock,
    mock_sources: MagicMock,
    mock_prefetch: MagicMock,
    mock_discover: MagicMock,
    mock_fetch_one: MagicMock,
    mock_outcome: MagicMock,
    mock_upsert_v2: MagicMock,
) -> None:
    mock_list_users.return_value = ["user-1"]
    mock_sources.return_value = [
        {
            "url": "https://a.com",
            "use_rss": False,
            "category_id": "cid-1",
            "category_name": "News",
            "category_instruction": "per category",
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
    assert inst == "per category"


@patch("news_manager.pipeline.upsert_excluded_url_v2")
@patch("news_manager.pipeline.filter_and_summarize_outcome")
@patch("news_manager.pipeline.fetch_single_raw_article")
@patch("news_manager.pipeline.discover_article_targets")
@patch("news_manager.pipeline.prefetch_processed_urls_v2")
@patch("news_manager.pipeline.fetch_sources_with_categories")
@patch("news_manager.pipeline.list_user_ids_with_sources")
def test_run_pipeline_from_db_excluded_passes_exclude_why(
    mock_list_users: MagicMock,
    mock_sources: MagicMock,
    mock_prefetch: MagicMock,
    mock_discover: MagicMock,
    mock_fetch_one: MagicMock,
    mock_outcome: MagicMock,
    mock_upsert_excl: MagicMock,
) -> None:
    mock_list_users.return_value = ["user-1"]
    mock_sources.return_value = [
        {
            "url": "https://a.com",
            "use_rss": False,
            "category_id": "cid-1",
            "category_name": "News",
            "category_instruction": "",
        }
    ]
    mock_prefetch.return_value = (set(), set())
    mock_discover.return_value = [("https://u", None, None)]
    mock_fetch_one.return_value = RawArticle(
        title="T", date=None, content="c", url="https://u"
    )
    mock_outcome.return_value = SummarizeOutcome(
        output=None, outcome="excluded", exclude_why="Wrong topic for this category."
    )
    mock_upsert_excl.return_value = None

    sb = MagicMock()
    run_pipeline_from_db(supabase_client=sb, max_articles=5, http_timeout=1.0)
    mock_upsert_excl.assert_called_once_with(
        sb, "https://u", "cid-1", "Wrong topic for this category."
    )


@patch("news_manager.pipeline.upsert_included_article_v2")
@patch("news_manager.pipeline.filter_and_summarize_outcome")
@patch("news_manager.pipeline.fetch_single_raw_article")
@patch("news_manager.pipeline.discover_article_targets")
@patch("news_manager.pipeline.prefetch_processed_urls_v2")
@patch("news_manager.pipeline.fetch_sources_with_categories")
@patch("news_manager.pipeline.list_user_ids_with_sources")
def test_run_pipeline_from_db_category_selector_matches_name_or_id(
    mock_list_users: MagicMock,
    mock_sources: MagicMock,
    mock_prefetch: MagicMock,
    mock_discover: MagicMock,
    mock_fetch_one: MagicMock,
    mock_outcome: MagicMock,
    mock_upsert_v2: MagicMock,
) -> None:
    mock_list_users.return_value = ["user-1"]
    mock_sources.return_value = [
        {
            "source_id": "sid-1",
            "source_name": "Source A",
            "url": "https://a.com",
            "use_rss": False,
            "category_id": "cid-1",
            "category_name": "News",
            "category_instruction": "per category",
        },
        {
            "source_id": "sid-2",
            "source_name": "Source B",
            "url": "https://b.com",
            "use_rss": False,
            "category_id": "cid-2",
            "category_name": "Sports",
            "category_instruction": "other instruction",
        },
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
    out = run_pipeline_from_db(
        supabase_client=sb,
        max_articles=5,
        http_timeout=1.0,
        category_selector="news",
    )
    assert len(out) == 1
    assert len(out[0].categories) == 1
    assert out[0].categories[0].category == "News"
    mock_upsert_v2.assert_called_once()
    assert mock_upsert_v2.call_args[0][1] == "user-1"
    assert mock_upsert_v2.call_args[0][2] == "cid-1"

    mock_upsert_v2.reset_mock()
    run_pipeline_from_db(
        supabase_client=sb,
        max_articles=5,
        http_timeout=1.0,
        category_selector="cid-2",
    )
    mock_upsert_v2.assert_called_once()
    assert mock_upsert_v2.call_args[0][1] == "user-1"
    assert mock_upsert_v2.call_args[0][2] == "cid-2"


@patch("news_manager.pipeline.upsert_included_article_v2")
@patch("news_manager.pipeline.filter_and_summarize_outcome")
@patch("news_manager.pipeline.fetch_single_raw_article")
@patch("news_manager.pipeline.discover_article_targets")
@patch("news_manager.pipeline.prefetch_processed_urls_v2")
@patch("news_manager.pipeline.fetch_sources_with_categories")
@patch("news_manager.pipeline.list_user_ids_with_sources")
def test_run_pipeline_from_db_source_selector_matches_name_or_id(
    mock_list_users: MagicMock,
    mock_sources: MagicMock,
    mock_prefetch: MagicMock,
    mock_discover: MagicMock,
    mock_fetch_one: MagicMock,
    mock_outcome: MagicMock,
    mock_upsert_v2: MagicMock,
) -> None:
    mock_list_users.return_value = ["user-1"]
    mock_sources.return_value = [
        {
            "source_id": "sid-1",
            "source_name": "Source A",
            "url": "https://a.com",
            "use_rss": False,
            "category_id": "cid-1",
            "category_name": "News",
            "category_instruction": "per category",
        },
        {
            "source_id": "sid-2",
            "source_name": "Source B",
            "url": "https://b.com",
            "use_rss": False,
            "category_id": "cid-1",
            "category_name": "News",
            "category_instruction": "per category",
        },
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
    run_pipeline_from_db(
        supabase_client=sb,
        max_articles=5,
        http_timeout=1.0,
        source_selector="source b",
    )
    mock_discover.assert_called_once()
    mock_upsert_v2.assert_called_once()

    mock_discover.reset_mock()
    mock_upsert_v2.reset_mock()
    run_pipeline_from_db(
        supabase_client=sb,
        max_articles=5,
        http_timeout=1.0,
        source_selector="sid-1",
    )
    mock_discover.assert_called_once()
    mock_upsert_v2.assert_called_once()
