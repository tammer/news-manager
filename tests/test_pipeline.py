"""DB-backed pipeline orchestration with mocks."""

from unittest.mock import MagicMock, patch

from news_manager.fetch import normalize_url
from news_manager.models import OutputArticle, PipelineDbRunResult, RawArticle
from news_manager.pipeline import evaluate_single_article_from_db, run_pipeline_from_db
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


@patch("news_manager.pipeline.filter_and_summarize_outcome")
@patch("news_manager.pipeline.fetch_single_raw_article")
@patch("news_manager.pipeline.discover_article_targets")
@patch("news_manager.pipeline.prefetch_processed_urls_v2")
@patch("news_manager.pipeline.fetch_sources_with_categories")
@patch("news_manager.pipeline.list_user_ids_with_sources")
def test_run_pipeline_verbosity_zero_is_silent(
    mock_list_users: MagicMock,
    mock_sources: MagicMock,
    mock_prefetch: MagicMock,
    mock_discover: MagicMock,
    mock_fetch_one: MagicMock,
    mock_outcome: MagicMock,
    capsys,
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
    mock_prefetch.return_value = ({normalize_url("https://u"): "Already in database"}, {})
    mock_discover.return_value = [("https://u", None, None)]
    mock_fetch_one.return_value = None
    mock_outcome.return_value = SummarizeOutcome(output=None, outcome="error", why=None)

    run_pipeline_from_db(
        supabase_client=MagicMock(),
        max_articles=5,
        http_timeout=1.0,
        verbosity=0,
    )
    captured = capsys.readouterr()
    assert captured.out == ""


@patch("news_manager.pipeline.filter_and_summarize_outcome")
@patch("news_manager.pipeline.fetch_single_raw_article")
@patch("news_manager.pipeline.discover_article_targets")
@patch("news_manager.pipeline.prefetch_processed_urls_v2")
@patch("news_manager.pipeline.fetch_sources_with_categories")
@patch("news_manager.pipeline.list_user_ids_with_sources")
def test_run_pipeline_verbosity_one_prints_human_readable_progress(
    mock_list_users: MagicMock,
    mock_sources: MagicMock,
    mock_prefetch: MagicMock,
    mock_discover: MagicMock,
    mock_fetch_one: MagicMock,
    mock_outcome: MagicMock,
    capsys,
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
    mock_prefetch.return_value = ({normalize_url("https://u"): "Already in database"}, {})
    mock_discover.return_value = [("https://u", None, None)]
    mock_fetch_one.return_value = None
    mock_outcome.return_value = SummarizeOutcome(output=None, outcome="error", why=None)

    run_pipeline_from_db(
        supabase_client=MagicMock(),
        max_articles=5,
        http_timeout=1.0,
        verbosity=1,
    )
    captured = capsys.readouterr()
    out = captured.out
    assert "Starting at:" in out
    assert "Processing category: News" in out
    assert "Processing source: a.com" in out
    assert "Processing article: https://u" in out
    assert "Decision: Include because: Already in database" in out
    assert "Summary for category/source: News / a.com" in out
    assert "Index URL: https://a.com" in out
    assert "Processed 1 articles" in out
    assert "Included 1 articles" in out
    assert "Rejected 0 articles" in out


@patch("news_manager.pipeline.fetch_single_raw_article")
@patch("news_manager.pipeline.filter_and_summarize_outcome")
@patch("news_manager.pipeline.fetch_sources_for_category_user")
@patch("news_manager.pipeline.fetch_category_for_user")
def test_evaluate_single_article_uses_instruction_override(
    mock_category: MagicMock,
    mock_sources: MagicMock,
    mock_outcome: MagicMock,
    mock_fetch_one: MagicMock,
) -> None:
    mock_category.return_value = {
        "category_id": "cid-1",
        "category_name": "News",
        "category_instruction": "default instruction",
    }
    mock_sources.return_value = [{"source_id": "sid-1", "url": "https://a.com", "use_rss": False}]
    mock_fetch_one.return_value = RawArticle(
        title="T", date=None, content="c", url="https://a.com/post"
    )
    mock_outcome.return_value = SummarizeOutcome(
        output=OutputArticle(
            title="T",
            date=None,
            content="c",
            url="https://a.com/post",
            short_summary="s",
            full_summary="f",
            source="a.com",
        ),
        outcome="included",
        why="Matches new rule.",
    )

    result = evaluate_single_article_from_db(
        supabase_client=MagicMock(),
        user_id="user-1",
        category_id="cid-1",
        url="https://a.com/post",
        instructions_override="override instruction",
    )
    assert result["included"] is True
    assert result["reason"] == "Matches new rule."
    assert result["instruction_source"] == "override"
    assert mock_outcome.call_args.kwargs["instructions"] == "override instruction"


@patch("news_manager.pipeline.fetch_single_raw_article")
@patch("news_manager.pipeline.fetch_sources_for_category_user")
@patch("news_manager.pipeline.fetch_category_for_user")
def test_evaluate_single_article_fetch_failure_returns_reason(
    mock_category: MagicMock,
    mock_sources: MagicMock,
    mock_fetch_one: MagicMock,
) -> None:
    mock_category.return_value = {
        "category_id": "cid-1",
        "category_name": "News",
        "category_instruction": "default instruction",
    }
    mock_sources.return_value = []
    mock_fetch_one.return_value = None

    result = evaluate_single_article_from_db(
        supabase_client=MagicMock(),
        user_id="user-1",
        category_id="cid-1",
        url="https://example.com/a",
    )
    assert result["included"] is False
    assert result["reason"] == "Could not fetch article."
    assert result["persisted"] is False


@patch("news_manager.pipeline.filter_and_summarize_outcome")
@patch("news_manager.pipeline.fetch_single_raw_article")
@patch("news_manager.pipeline.fetch_sources_for_category_user")
@patch("news_manager.pipeline.fetch_category_for_user")
def test_evaluate_single_article_fetch_success_allows_include_path(
    mock_category: MagicMock,
    mock_sources: MagicMock,
    mock_fetch_one: MagicMock,
    mock_outcome: MagicMock,
) -> None:
    # fetch_single_raw_article may be fulfilled by direct fetch or fallback provider.
    mock_category.return_value = {
        "category_id": "cid-1",
        "category_name": "News",
        "category_instruction": "default instruction",
    }
    mock_sources.return_value = []
    mock_fetch_one.return_value = RawArticle(
        title="T", date=None, content="c", url="https://example.com/a"
    )
    mock_outcome.return_value = SummarizeOutcome(
        output=OutputArticle(
            title="T",
            date=None,
            content="c",
            url="https://example.com/a",
            short_summary="s",
            full_summary="f",
            source="example.com",
        ),
        outcome="included",
        why="Matches category focus.",
    )

    result = evaluate_single_article_from_db(
        supabase_client=MagicMock(),
        user_id="user-1",
        category_id="cid-1",
        url="https://example.com/a",
    )
    assert result["included"] is True
    assert result["reason"] == "Matches category focus."
    assert result["short_summary"] == "s"


@patch("news_manager.pipeline.fetch_single_raw_article")
@patch("news_manager.pipeline.filter_and_summarize_outcome")
@patch("news_manager.pipeline.fetch_sources_for_category_user")
@patch("news_manager.pipeline.fetch_category_for_user")
def test_evaluate_single_article_llm_error_path(
    mock_category: MagicMock,
    mock_sources: MagicMock,
    mock_outcome: MagicMock,
    mock_fetch_one: MagicMock,
) -> None:
    mock_category.return_value = {
        "category_id": "cid-1",
        "category_name": "News",
        "category_instruction": "default instruction",
    }
    mock_sources.return_value = []
    mock_fetch_one.return_value = RawArticle(
        title="T", date=None, content="c", url="https://example.com/a"
    )
    mock_outcome.return_value = SummarizeOutcome(output=None, outcome="error", why=None)

    result = evaluate_single_article_from_db(
        supabase_client=MagicMock(),
        user_id="user-1",
        category_id="cid-1",
        url="https://example.com/a",
    )
    assert result["included"] is False
    assert result["reason"] == "LLM or parse error"


@patch("news_manager.pipeline.upsert_excluded_url_v2")
@patch("news_manager.pipeline.fetch_single_raw_article")
@patch("news_manager.pipeline.filter_and_summarize_outcome")
@patch("news_manager.pipeline.fetch_sources_for_category_user")
@patch("news_manager.pipeline.fetch_category_for_user")
def test_evaluate_single_article_excluded_persist_true_writes_exclusion(
    mock_category: MagicMock,
    mock_sources: MagicMock,
    mock_outcome: MagicMock,
    mock_fetch_one: MagicMock,
    mock_upsert_excluded: MagicMock,
) -> None:
    mock_category.return_value = {
        "category_id": "cid-1",
        "category_name": "News",
        "category_instruction": "default instruction",
    }
    mock_sources.return_value = [{"source_id": "sid-1", "url": "https://a.com", "use_rss": False}]
    mock_fetch_one.return_value = RawArticle(
        title="T", date=None, content="c", url="https://a.com/post"
    )
    mock_outcome.return_value = SummarizeOutcome(
        output=None, outcome="excluded", why="Out of scope."
    )
    mock_upsert_excluded.return_value = None

    sb = MagicMock()
    result = evaluate_single_article_from_db(
        supabase_client=sb,
        user_id="user-1",
        category_id="cid-1",
        url="https://a.com/post",
        persist=True,
    )
    assert result["included"] is False
    assert result["persisted"] is True
    mock_upsert_excluded.assert_called_once_with(
        sb,
        "user-1",
        "cid-1",
        "sid-1",
        "https://a.com/post",
        "Out of scope.",
    )
