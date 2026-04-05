"""Supabase helpers (mocked client)."""

from unittest.mock import MagicMock

from news_manager.models import OutputArticle
from news_manager.supabase_sync import (
    output_article_to_upsert_row,
    parse_article_date_iso,
    prefetch_processed_urls_for_category,
    upsert_excluded_url,
    upsert_included_article,
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


def _client_with_prefetch(news_urls: list[str], excl_urls: list[str]) -> MagicMock:
    client = MagicMock()

    def table(name: str) -> MagicMock:
        t = MagicMock()
        sel = MagicMock()
        urls = news_urls if name == "news_articles" else excl_urls
        sel.execute.return_value = MagicMock(data=[{"url": u} for u in urls])
        t.select.return_value.eq.return_value = sel
        up = MagicMock()
        up.execute.return_value = MagicMock()
        t.upsert.return_value = up
        return t

    client.table.side_effect = table
    return client


def test_prefetch_processed_urls_for_category() -> None:
    client = _client_with_prefetch(
        news_urls=["https://one.example/p"],
        excl_urls=["https://two.example/q"],
    )
    inc, exc = prefetch_processed_urls_for_category(client, "News")
    assert "https://one.example/p" in inc
    assert "https://two.example/q" in exc
    client.table.assert_called()


def test_upsert_included_article_calls_news_articles() -> None:
    news_table = MagicMock()
    excl_table = MagicMock()
    for t in (news_table, excl_table):
        sel = MagicMock()
        sel.execute.return_value = MagicMock(data=[])
        t.select.return_value.eq.return_value = sel
        up = MagicMock()
        up.execute.return_value = MagicMock()
        t.upsert.return_value = up

    client = MagicMock()

    def table(name: str) -> MagicMock:
        return news_table if name == "news_articles" else excl_table

    client.table.side_effect = table

    art = OutputArticle(
        title="T",
        date=None,
        content="c",
        url="https://ex.com/z",
        short_summary="s",
        full_summary="f",
        source="src",
    )
    assert upsert_included_article(client, "News", art) is None
    assert news_table.upsert.called
    kwargs = news_table.upsert.call_args[1]
    assert kwargs["on_conflict"] == "url,category"
    assert kwargs["default_to_null"] is False


def test_upsert_included_article_returns_error_on_failure() -> None:
    client = MagicMock()

    def table(_name: str) -> MagicMock:
        t = MagicMock()
        sel = MagicMock()
        sel.execute.return_value = MagicMock(data=[])
        t.select.return_value.eq.return_value = sel
        up = MagicMock()
        up.execute.side_effect = RuntimeError("boom")
        t.upsert.return_value = up
        return t

    client.table.side_effect = table
    art = OutputArticle(
        title="T",
        date=None,
        content="c",
        url="https://ex.com/z",
        short_summary="s",
        full_summary="f",
        source="src",
    )
    err = upsert_included_article(client, "News", art)
    assert err is not None
    assert "boom" in err


def test_upsert_excluded_url_calls_exclusions_table() -> None:
    news_table = MagicMock()
    excl_table = MagicMock()
    for t in (news_table, excl_table):
        sel = MagicMock()
        sel.execute.return_value = MagicMock(data=[])
        t.select.return_value.eq.return_value = sel
        up = MagicMock()
        up.execute.return_value = MagicMock()
        t.upsert.return_value = up

    client = MagicMock()

    def table(name: str) -> MagicMock:
        return news_table if name == "news_articles" else excl_table

    client.table.side_effect = table

    assert upsert_excluded_url(client, "https://x.com/a", "News") is None
    assert excl_table.upsert.called
    row = excl_table.upsert.call_args[0][0][0]
    assert row["url"] == "https://x.com/a"
    assert row["category"] == "News"
