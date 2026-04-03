"""Supabase upsert mapping and batching (mocked client)."""

from unittest.mock import MagicMock

from news_manager.models import CategoryResult, OutputArticle
from news_manager.supabase_sync import (
    output_article_to_upsert_row,
    parse_article_date_iso,
    sync_category_results_to_supabase,
)


def test_parse_article_date_iso_z_suffix() -> None:
    assert parse_article_date_iso("2024-01-15T12:00:00Z") == "2024-01-15T12:00:00+00:00"


def test_parse_article_date_iso_naive_becomes_utc() -> None:
    assert parse_article_date_iso("2024-06-01T08:30:00") == "2024-06-01T08:30:00+00:00"


def test_parse_article_date_iso_none_and_empty() -> None:
    assert parse_article_date_iso(None) is None
    assert parse_article_date_iso("") is None
    assert parse_article_date_iso("   ") is None


def test_parse_article_date_iso_garbage() -> None:
    assert parse_article_date_iso("not a date") is None


def test_output_article_to_upsert_row_omits_date_when_unparseable() -> None:
    art = OutputArticle(
        title="  Hi  ",
        date="nope",
        content="c",
        url="https://ex.com/a",
        short_summary="s",
        full_summary="f",
        source="ex.com",
    )
    row = output_article_to_upsert_row("News", art)
    assert row["category"] == "News"
    assert row["url"] == "https://ex.com/a"
    assert row["headline"] == "Hi"
    assert row["source"] == "ex.com"
    assert row["short_summary"] == "s"
    assert row["full_summary"] == "f"
    assert "article_date" not in row
    assert "read" not in row
    assert "liked" not in row


def test_output_article_to_upsert_row_empty_title() -> None:
    art = OutputArticle(
        title="  \n  ",
        date=None,
        content="c",
        url="https://ex.com/b",
        short_summary="s",
        full_summary="f",
        source="src",
    )
    row = output_article_to_upsert_row("Tech", art)
    assert row["headline"] == "(no title)"


def test_output_article_to_upsert_row_includes_article_date() -> None:
    art = OutputArticle(
        title="T",
        date="2024-03-20T00:00:00+00:00",
        content="c",
        url="https://ex.com/c",
        short_summary="s",
        full_summary="f",
        source="src",
    )
    row = output_article_to_upsert_row("Tech", art)
    assert row["article_date"] == "2024-03-20T00:00:00+00:00"


def test_sync_category_results_to_supabase_batches_and_upsert_options() -> None:
    client = MagicMock()
    table = MagicMock()
    upsert_builder = MagicMock()
    client.table.return_value = table
    table.upsert.return_value = upsert_builder
    upsert_builder.execute.return_value = MagicMock()

    articles = [
        OutputArticle(
            title=f"T{i}",
            date=None,
            content="c",
            url=f"https://ex.com/{i}",
            short_summary="s",
            full_summary="f",
            source="src",
        )
        for i in range(3)
    ]
    results = [
        CategoryResult(category="News", articles=articles[:2]),
        CategoryResult(category="Tech", articles=articles[2:]),
    ]

    sync_category_results_to_supabase(results, client=client, batch_size=2)

    client.table.assert_called_with("news_articles")
    assert table.upsert.call_count == 2
    first_batch = table.upsert.call_args_list[0][0][0]
    assert len(first_batch) == 2
    assert first_batch[0]["category"] == "News"
    assert first_batch[0]["url"] == "https://ex.com/0"
    assert first_batch[1]["category"] == "News"
    assert first_batch[1]["url"] == "https://ex.com/1"
    second_batch = table.upsert.call_args_list[1][0][0]
    assert len(second_batch) == 1
    assert second_batch[0]["category"] == "Tech"

    for call in table.upsert.call_args_list:
        kwargs = call[1]
        assert kwargs["on_conflict"] == "url,category"
        assert kwargs["default_to_null"] is False
        for row in call[0][0]:
            assert "read" not in row
            assert "liked" not in row


def test_sync_category_results_empty_no_upsert() -> None:
    client = MagicMock()
    sync_category_results_to_supabase(
        [CategoryResult(category="X", articles=[])],
        client=client,
    )
    client.table.assert_not_called()
