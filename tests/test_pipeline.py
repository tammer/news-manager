"""DB-backed pipeline orchestration with mocks."""

from unittest.mock import MagicMock, patch

from news_manager.fetch import normalize_url
from news_manager.models import OutputArticle, PipelineDbRunResult, RawArticle
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
            "source_id": "sid-1",
            "url": "https://a.com",
            "use_rss": False,
            "category_id": "cid-1",
            "category_name": "News",
            "category_instruction": "per category",
        }
    ]
    mock_prefetch.side_effect = [({}, {}), ({}, {})]
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
        why="Matches category focus.",
    )
    mock_upsert_v2.return_value = None

    sb = MagicMock()
    result = run_pipeline_from_db(supabase_client=sb, max_articles=5, http_timeout=1.0)
    out = result.users
    assert len(out) == 1
    assert out[0].user_id == "user-1"
    assert len(out[0].categories) == 1
    assert out[0].categories[0].category == "News"
    assert len(out[0].categories[0].articles) == 1
    assert len(result.article_decisions) == 1
    d = result.article_decisions[0]
    assert d["included"] is True
    assert d["reason"] == "Matches category focus."
    assert d["url"] == "https://u"
    assert d["short_summary"] == "s"
    assert d["full_summary"] == "f"
    assert d["title"] == "T"
    assert "content" not in d
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
def test_run_pipeline_from_db_excluded_passes_why(
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
            "source_id": "sid-1",
            "url": "https://a.com",
            "use_rss": False,
            "category_id": "cid-1",
            "category_name": "News",
            "category_instruction": "",
        }
    ]
    mock_prefetch.side_effect = [({}, {}), ({}, {})]
    mock_discover.return_value = [("https://u", None, None)]
    mock_fetch_one.return_value = RawArticle(
        title="T", date=None, content="c", url="https://u"
    )
    mock_outcome.return_value = SummarizeOutcome(
        output=None, outcome="excluded", why="Wrong topic for this category."
    )
    mock_upsert_excl.return_value = None

    sb = MagicMock()
    result = run_pipeline_from_db(supabase_client=sb, max_articles=5, http_timeout=1.0)
    mock_upsert_excl.assert_called_once_with(
        sb,
        "user-1",
        "cid-1",
        "sid-1",
        "https://u",
        "Wrong topic for this category.",
    )
    assert len(result.article_decisions) == 1
    d = result.article_decisions[0]
    assert d["included"] is False
    assert d["reason"] == "Wrong topic for this category."
    assert d["title"] == "T"
    assert "content" not in d


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
    mock_prefetch.side_effect = [({}, {}), ({}, {})]
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
        why="Matches category focus.",
    )
    mock_upsert_v2.return_value = None

    sb = MagicMock()
    result = run_pipeline_from_db(
        supabase_client=sb,
        max_articles=5,
        http_timeout=1.0,
        category_selector="news",
    )
    out = result.users
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
    mock_prefetch.side_effect = [({}, {}), ({}, {})]
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
        why="Matches category focus.",
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


@patch("news_manager.pipeline.delete_included_article_v2")
@patch("news_manager.pipeline.upsert_included_article_v2")
@patch("news_manager.pipeline.filter_and_summarize_outcome")
@patch("news_manager.pipeline.fetch_single_raw_article")
@patch("news_manager.pipeline.discover_article_targets")
@patch("news_manager.pipeline.prefetch_processed_urls_v2")
@patch("news_manager.pipeline.fetch_sources_with_categories")
@patch("news_manager.pipeline.list_user_ids_with_sources")
def test_run_pipeline_reprocess_included_deletes_and_runs_llm(
    mock_list_users: MagicMock,
    mock_sources: MagicMock,
    mock_prefetch: MagicMock,
    mock_discover: MagicMock,
    mock_fetch_one: MagicMock,
    mock_outcome: MagicMock,
    mock_upsert_v2: MagicMock,
    mock_delete_inc: MagicMock,
) -> None:
    nu = normalize_url("https://u")
    mock_list_users.return_value = ["user-1"]
    mock_sources.return_value = [
        {
            "source_id": "sid-1",
            "url": "https://a.com",
            "use_rss": False,
            "category_id": "cid-1",
            "category_name": "News",
            "category_instruction": "per category",
        }
    ]
    mock_prefetch.return_value = ({nu: "Already included previously."}, {})
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
        why="Matches category focus.",
    )
    mock_upsert_v2.return_value = None
    mock_delete_inc.return_value = None

    sb = MagicMock()
    run_pipeline_from_db(
        supabase_client=sb, max_articles=5, http_timeout=1.0, reprocess=True
    )
    mock_delete_inc.assert_called_once_with(sb, "user-1", "cid-1", nu)
    mock_outcome.assert_called_once()
    mock_upsert_v2.assert_called_once()


@patch("news_manager.pipeline.delete_excluded_url_v2")
@patch("news_manager.pipeline.upsert_included_article_v2")
@patch("news_manager.pipeline.filter_and_summarize_outcome")
@patch("news_manager.pipeline.fetch_single_raw_article")
@patch("news_manager.pipeline.discover_article_targets")
@patch("news_manager.pipeline.prefetch_processed_urls_v2")
@patch("news_manager.pipeline.fetch_sources_with_categories")
@patch("news_manager.pipeline.list_user_ids_with_sources")
def test_run_pipeline_reprocess_excluded_deletes_and_runs_llm(
    mock_list_users: MagicMock,
    mock_sources: MagicMock,
    mock_prefetch: MagicMock,
    mock_discover: MagicMock,
    mock_fetch_one: MagicMock,
    mock_outcome: MagicMock,
    mock_upsert_v2: MagicMock,
    mock_delete_exc: MagicMock,
) -> None:
    nu = normalize_url("https://u")
    mock_list_users.return_value = ["user-1"]
    mock_sources.return_value = [
        {
            "source_id": "sid-1",
            "url": "https://a.com",
            "use_rss": False,
            "category_id": "cid-1",
            "category_name": "News",
            "category_instruction": "",
        }
    ]
    mock_prefetch.return_value = ({}, {nu: "Out of scope previously."})
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
        why="Matches category focus.",
    )
    mock_upsert_v2.return_value = None
    mock_delete_exc.return_value = None

    sb = MagicMock()
    run_pipeline_from_db(
        supabase_client=sb, max_articles=5, http_timeout=1.0, reprocess=True
    )
    mock_delete_exc.assert_called_once_with(sb, "user-1", "cid-1", nu)
    mock_outcome.assert_called_once()


@patch("news_manager.pipeline.filter_and_summarize_outcome")
@patch("news_manager.pipeline.fetch_single_raw_article")
@patch("news_manager.pipeline.discover_article_targets")
@patch("news_manager.pipeline.prefetch_processed_urls_v2")
@patch("news_manager.pipeline.fetch_sources_with_categories")
@patch("news_manager.pipeline.list_user_ids_with_sources")
def test_run_pipeline_cached_included_skips_without_reprocess(
    mock_list_users: MagicMock,
    mock_sources: MagicMock,
    mock_prefetch: MagicMock,
    mock_discover: MagicMock,
    mock_fetch_one: MagicMock,
    mock_outcome: MagicMock,
) -> None:
    nu = normalize_url("https://u")
    mock_list_users.return_value = ["user-1"]
    mock_sources.return_value = [
        {
            "source_id": "sid-1",
            "url": "https://a.com",
            "use_rss": False,
            "category_id": "cid-1",
            "category_name": "News",
            "category_instruction": "",
        }
    ]
    mock_prefetch.return_value = ({nu: "Matches existing include criteria."}, {})
    mock_discover.return_value = [("https://u", None, None)]

    sb = MagicMock()
    result = run_pipeline_from_db(supabase_client=sb, max_articles=5, http_timeout=1.0)
    mock_outcome.assert_not_called()
    mock_fetch_one.assert_not_called()
    assert len(result.article_decisions) == 1
    assert result.article_decisions[0]["included"] is True
    assert result.article_decisions[0]["reason"] == "Matches existing include criteria."


@patch("news_manager.pipeline.filter_and_summarize_outcome")
@patch("news_manager.pipeline.fetch_single_raw_article")
@patch("news_manager.pipeline.discover_article_targets")
@patch("news_manager.pipeline.prefetch_processed_urls_v2")
@patch("news_manager.pipeline.fetch_sources_with_categories")
@patch("news_manager.pipeline.list_user_ids_with_sources")
def test_run_pipeline_cached_excluded_uses_stored_why(
    mock_list_users: MagicMock,
    mock_sources: MagicMock,
    mock_prefetch: MagicMock,
    mock_discover: MagicMock,
    mock_fetch_one: MagicMock,
    mock_outcome: MagicMock,
) -> None:
    nu = normalize_url("https://u")
    why = "Does not match local coverage focus."
    mock_list_users.return_value = ["user-1"]
    mock_sources.return_value = [
        {
            "source_id": "sid-1",
            "url": "https://a.com",
            "use_rss": False,
            "category_id": "cid-1",
            "category_name": "News",
            "category_instruction": "",
        }
    ]
    mock_prefetch.return_value = ({}, {nu: why})
    mock_discover.return_value = [("https://u", None, None)]

    sb = MagicMock()
    result = run_pipeline_from_db(supabase_client=sb, max_articles=5, http_timeout=1.0)
    mock_outcome.assert_not_called()
    mock_fetch_one.assert_not_called()
    assert len(result.article_decisions) == 1
    assert result.article_decisions[0]["included"] is False
    assert result.article_decisions[0]["reason"] == why
